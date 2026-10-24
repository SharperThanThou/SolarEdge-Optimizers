"""Data models shared by every optimizer data source.

These are deliberately independent of both Home Assistant and any particular
SolarEdge client, so a new backend (for example a local one) only has to
produce these objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Inverter:
    """An inverter in the site layout."""

    serial: str
    name: str
    model: str | None = None


@dataclass(frozen=True, slots=True)
class Optimizer:
    """An optimizer in the site layout.

    ``string_id`` groups optimizers wired in series. It is ``None`` when the
    layout does not place the optimizer in a string.
    """

    serial: str
    name: str
    inverter_serial: str | None
    string_id: str | None
    string_name: str | None = None


@dataclass(frozen=True, slots=True)
class OptimizerReading:
    """The most recent telemetry the source has for one optimizer.

    Power is in W, voltages in V and current in A. ``voltage`` is the module
    (panel) side, ``optimizer_voltage`` the optimizer's output side.
    ``last_measurement`` is timezone aware.
    """

    serial: str
    power: float | None = None
    voltage: float | None = None
    optimizer_voltage: float | None = None
    current: float | None = None
    last_measurement: datetime | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class SiteLayout:
    """Static description of a site: its inverters and optimizers."""

    site_id: str
    name: str
    inverters: dict[str, Inverter] = field(default_factory=dict)
    optimizers: dict[str, Optimizer] = field(default_factory=dict)
