"""Tests for the monitoring portal source."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from solaredge_web import OptimizerData

from custom_components.solaredge_optimizers.api import (
    CloudSource,
    SourceAuthError,
    SourceError,
)
from custom_components.solaredge_optimizers.api.cloud import (
    panel_power_from_model,
    parse_layout,
)


def _optimizer(serial: str, name: str, **extra: object) -> dict:
    return {"type": "OPTIMIZER", "serial": serial, "name": name, **extra}


_PANEL = {
    "panelManufacturerName": "Silfab Solar Inc.",
    "panelModelName": "SLA-M 320",
    "panelsCount": 1,
}


def _equipment() -> dict[str, dict]:
    """Return equipment shaped like ``SolarEdgeWeb.async_get_equipment``."""
    string_1 = {
        "type": "STRING",
        "serial": "INV1_1",
        "name": "String 1.1",
        "children": [
            _optimizer("OPT-1", "1.1.1", properties=_PANEL),
            _optimizer("OPT-2", "1.1.2", properties={**_PANEL, "panelsCount": 2}),
        ],
    }
    string_2 = {
        "type": "STRING",
        "properties": {"identifier": "INV1_2"},
        "name": "String 1.2",
        "children": [
            _optimizer("OPT-3", "1.2.1"),
            _optimizer("OPT-OLD", "1.2.1", properties={"status": "INACTIVE"}),
        ],
    }
    inverter = {
        "type": "INVERTER",
        "serial": "INV1",
        "name": "Inverter 1",
        "properties": {"model": "SE7600H", "maxActivePower": 7600.0},
        "children": [string_1, string_2],
    }
    loose = _optimizer("OPT-LOOSE", "9.9.9")
    nodes = [
        inverter,
        string_1,
        string_2,
        *string_1["children"],
        string_2["children"][0],
        loose,
    ]
    return {
        node.get("serial") or node["properties"]["identifier"]: node for node in nodes
    }


def test_parse_layout() -> None:
    layout = parse_layout("42", _equipment())
    assert set(layout.inverters) == {"INV1"}
    assert layout.inverters["INV1"].model == "SE7600H"
    assert layout.inverters["INV1"].max_power == 7600.0
    assert set(layout.optimizers) == {"OPT-1", "OPT-2", "OPT-3", "OPT-LOOSE"}

    opt = layout.optimizers["OPT-1"]
    assert opt.inverter_serial == "INV1"
    assert opt.string_id == "INV1_1"
    assert opt.string_name == "String 1.1"
    assert layout.optimizers["OPT-3"].string_id == "INV1_2"
    assert opt.panel_model == "Silfab Solar Inc. SLA-M 320"
    assert opt.panel_power == 320.0
    assert opt.panels == 1
    assert layout.optimizers["OPT-2"].panels == 2
    # No panel details: unknown model and power, one panel.
    assert layout.optimizers["OPT-3"].panel_model is None
    assert layout.optimizers["OPT-3"].panel_power is None
    assert layout.optimizers["OPT-3"].panels == 1

    loose = layout.optimizers["OPT-LOOSE"]
    assert loose.inverter_serial is None
    assert loose.string_id is None


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        ("SLA-M 320", 320.0),
        ("JAM60S20-385/MR", 385.0),
        ("Q.PEAK DUO BLK-G10+ 400", 400.0),
        ("REC405AA", 405.0),
        ("CS6R-400MS", 400.0),
        ("Unknown panel", None),
        ("Model 12", None),
        (None, None),
    ],
)
def test_panel_power_from_model(model: str | None, expected: float | None) -> None:
    assert panel_power_from_model(model) == expected


async def test_readings() -> None:
    when = datetime(2026, 6, 21, 18, 0, tzinfo=UTC)
    client = MagicMock()
    client.async_get_optimizer_data = AsyncMock(
        return_value={
            "OPT-1": OptimizerData(
                serial="OPT-1",
                power=250.0,
                voltage=37.5,
                optimizer_voltage=40.1,
                current=6.6,
                last_measurement=when,
                model="P401",
            )
        }
    )
    with patch(
        "custom_components.solaredge_optimizers.api.cloud.SolarEdgeWeb",
        return_value=client,
    ):
        source = CloudSource(MagicMock(), "user", "pass", "42")
        readings = await source.async_get_readings()

    reading = readings["OPT-1"]
    assert reading.power == 250.0
    assert reading.optimizer_voltage == 40.1
    assert reading.last_measurement == when


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (aiohttp.ClientResponseError(MagicMock(), (), status=401), SourceAuthError),
        (aiohttp.ClientResponseError(MagicMock(), (), status=403), SourceAuthError),
        (aiohttp.ClientResponseError(MagicMock(), (), status=500), SourceError),
        (aiohttp.ClientConnectionError(), SourceError),
        (TimeoutError(), SourceError),
        (KeyError("siteStructure"), SourceError),
    ],
)
async def test_errors(error: Exception, expected: type[Exception]) -> None:
    client = MagicMock()
    client.async_get_equipment = AsyncMock(side_effect=error)
    with patch(
        "custom_components.solaredge_optimizers.api.cloud.SolarEdgeWeb",
        return_value=client,
    ):
        source = CloudSource(MagicMock(), "user", "pass", "42")
        with pytest.raises(expected):
            await source.async_get_layout()
