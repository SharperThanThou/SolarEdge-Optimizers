"""Optimizer source backed by the SolarEdge monitoring portal.

The official Monitoring API does not expose per-optimizer telemetry, so this
uses the same endpoints as the monitoring web app via the ``solaredge-web``
library (also used by Home Assistant's core SolarEdge integration). Those
endpoints are undocumented and may change without notice.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from typing import Any, TypeVar

import aiohttp
from solaredge_web import SolarEdgeWeb

from .base import SourceAuthError, SourceError
from .models import Inverter, Optimizer, OptimizerReading, SiteLayout

_T = TypeVar("_T")

_AUTH_STATUSES = (401, 403)


class CloudSource:
    """Read optimizer data from monitoring.solaredge.com."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        username: str,
        password: str,
        site_id: str,
    ) -> None:
        """Initialize the source.

        ``session`` should be dedicated to this source: the portal login is
        held in its cookie jar.
        """
        self.site_id = site_id
        self._client = SolarEdgeWeb(username, password, site_id, session)

    async def async_get_layout(self) -> SiteLayout:
        """Return the inverters and optimizers in the site layout."""
        equipment = await self._call(self._client.async_get_equipment)
        return parse_layout(self.site_id, equipment)

    async def async_get_readings(self) -> dict[str, OptimizerReading]:
        """Return live readings for every optimizer, keyed by serial."""
        data = await self._call(self._client.async_get_optimizer_data)
        return {
            serial: OptimizerReading(
                serial=serial,
                power=item.power,
                voltage=item.voltage,
                optimizer_voltage=item.optimizer_voltage,
                current=item.current,
                last_measurement=item.last_measurement,
                model=item.model,
            )
            for serial, item in data.items()
        }

    async def _call(self, func: Callable[[], Awaitable[_T]]) -> _T:
        """Run a client call, translating its errors into source errors."""
        try:
            return await func()
        except aiohttp.ClientResponseError as err:
            if err.status in _AUTH_STATUSES:
                raise SourceAuthError(str(err)) from err
            raise SourceError(str(err)) from err
        except (aiohttp.ClientError, TimeoutError) as err:
            raise SourceError(str(err) or type(err).__name__) from err
        except (KeyError, TypeError, ValueError) as err:
            raise SourceError(f"Unexpected response from portal: {err}") from err


def parse_layout(site_id: str, equipment: dict[str, dict[str, Any]]) -> SiteLayout:
    """Build a :class:`SiteLayout` from ``SolarEdgeWeb.async_get_equipment``.

    The equipment dict is flat, but each node keeps its ``children``, so the
    inverter nodes are walked to learn which string each optimizer is in.
    """
    inverters: dict[str, Inverter] = {}
    optimizers: dict[str, Optimizer] = {}

    for node in equipment.values():
        if node.get("type") != "INVERTER" or not node.get("serial"):
            continue
        serial = node["serial"]
        inverters[serial] = Inverter(
            serial=serial,
            name=node.get("name") or f"Inverter {serial}",
            model=(node.get("properties") or {}).get("model"),
        )
        for opt_node, string_node in _walk_optimizers(node, None):
            opt = _make_optimizer(opt_node, serial, string_node)
            if opt is not None:
                optimizers.setdefault(opt.serial, opt)

    # Optimizers the layout did not place under an inverter.
    for node in equipment.values():
        if node.get("type") == "OPTIMIZER":
            opt = _make_optimizer(node, None, None)
            if opt is not None:
                optimizers.setdefault(opt.serial, opt)

    return SiteLayout(
        site_id=site_id,
        name=f"SolarEdge site {site_id}",
        inverters=inverters,
        optimizers=optimizers,
    )


def _walk_optimizers(
    node: dict[str, Any], string_node: dict[str, Any] | None
) -> Iterator[tuple[dict[str, Any], dict[str, Any] | None]]:
    """Yield each optimizer below ``node`` with its nearest string node."""
    for child in node.get("children") or []:
        child_type = child.get("type")
        if child_type == "OPTIMIZER":
            yield child, string_node
        else:
            yield from _walk_optimizers(
                child, child if child_type == "STRING" else string_node
            )


def _make_optimizer(
    node: dict[str, Any],
    inverter_serial: str | None,
    string_node: dict[str, Any] | None,
) -> Optimizer | None:
    """Build an optimizer from a layout node, skipping retired units."""
    serial = node.get("serial")
    if not serial or (node.get("properties") or {}).get("status") == "INACTIVE":
        return None
    string_id: str | None = None
    string_name: str | None = None
    if string_node is not None:
        string_id = (
            string_node.get("serial")
            or (string_node.get("properties") or {}).get("identifier")
            or string_node.get("uuid")
        )
        string_name = string_node.get("name")
    return Optimizer(
        serial=serial,
        name=node.get("name") or f"Optimizer {serial}",
        inverter_serial=inverter_serial,
        string_id=string_id,
        string_name=string_name,
    )
