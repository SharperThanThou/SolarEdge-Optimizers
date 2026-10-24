"""Diagnostics for the SolarEdge Optimizers integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from .coordinator import SolarEdgeOptimizersConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: SolarEdgeOptimizersConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "layout": asdict(coordinator.layout),
        "readings": {serial: asdict(r) for serial, r in data.readings.items()},
        "health": {serial: asdict(h) for serial, h in data.health.items()},
        "fetched_at": data.fetched_at.isoformat(),
    }
