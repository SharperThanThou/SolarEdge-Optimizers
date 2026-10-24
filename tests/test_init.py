"""Tests for setup, entities and error handling."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.solaredge_optimizers.api import SourceAuthError, SourceError
from custom_components.solaredge_optimizers.const import DOMAIN
from custom_components.solaredge_optimizers.diagnostics import (
    async_get_config_entry_diagnostics,
)

from .conftest import INVERTER, STRING_A, make_readings


def entity_id(hass: HomeAssistant, serial: str, key: str) -> str:
    """Look up an entity id by its unique id."""
    platform = (
        "binary_sensor" if key in ("problem", "any_optimizer_problem") else "sensor"
    )
    result = er.async_get(hass).async_get_entity_id(platform, DOMAIN, f"{serial}_{key}")
    assert result is not None, f"{serial}_{key}"
    return result


async def setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    """Set up the integration."""
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_creates_entities(
    hass: HomeAssistant,
    mock_source: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    freezer.move_to(now)
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.LOADED

    serial = STRING_A[0]
    assert hass.states.get(entity_id(hass, serial, "power")).state == "300.0"
    assert hass.states.get(entity_id(hass, serial, "module_voltage")).state == "38.0"
    assert hass.states.get(entity_id(hass, serial, "optimizer_voltage")).state == "40.0"
    assert hass.states.get(entity_id(hass, serial, "current")).state == "8.0"
    assert hass.states.get(entity_id(hass, serial, "health")).state == "ok"
    assert hass.states.get(entity_id(hass, serial, "peer_ratio")).state == "100.0"
    assert hass.states.get(entity_id(hass, serial, "problem")).state == STATE_OFF

    site = config_entry.unique_id
    assert hass.states.get(entity_id(hass, site, "unhealthy_optimizers")).state == "0"
    assert (
        hass.states.get(entity_id(hass, site, "any_optimizer_problem")).state
        == STATE_OFF
    )

    devices = dr.async_get(hass)
    optimizer_device = devices.async_get_device(identifiers={(DOMAIN, serial)})
    inverter_device = devices.async_get_device(identifiers={(DOMAIN, INVERTER)})
    assert optimizer_device.name == "Optimizer 1.1.1"
    assert optimizer_device.via_device_id == inverter_device.id


async def test_problem_is_reported(
    hass: HomeAssistant,
    mock_source: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    freezer.move_to(now)
    await setup(hass, config_entry)
    bad = STRING_A[0]

    for sample in range(1, 4):
        freezer.tick(timedelta(minutes=15))
        mock_source.async_get_readings.return_value = make_readings(
            now + sample * timedelta(minutes=15), overrides={bad: 0.0}
        )
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

    assert hass.states.get(entity_id(hass, bad, "health")).state == "no_output"
    assert hass.states.get(entity_id(hass, bad, "problem")).state == STATE_ON
    site = config_entry.unique_id
    assert hass.states.get(entity_id(hass, site, "unhealthy_optimizers")).state == "1"
    site_problem = hass.states.get(entity_id(hass, site, "any_optimizer_problem"))
    assert site_problem.state == STATE_ON
    assert site_problem.attributes["unhealthy_optimizers"] == [
        {"name": "Optimizer 1.1.1", "serial": bad, "status": "no_output"}
    ]


async def test_stale_readings_report_zero_power(
    hass: HomeAssistant,
    mock_source: AsyncMock,
    config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    now: datetime,
) -> None:
    freezer.move_to(now)
    mock_source.async_get_readings.return_value = make_readings(
        now - timedelta(hours=6)
    )
    await setup(hass, config_entry)
    serial = STRING_A[0]
    assert hass.states.get(entity_id(hass, serial, "power")).state == "0.0"
    assert hass.states.get(entity_id(hass, serial, "module_voltage")).state == "unknown"
    assert (
        hass.states.get(entity_id(hass, serial, "last_measurement")).state != "unknown"
    )


async def test_setup_retries_on_connection_error(
    hass: HomeAssistant, mock_source: AsyncMock, config_entry: MockConfigEntry
) -> None:
    mock_source.async_get_layout.side_effect = SourceError("down")
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_source: AsyncMock, config_entry: MockConfigEntry
) -> None:
    mock_source.async_get_readings.side_effect = SourceAuthError("expired")
    await setup(hass, config_entry)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_unload(
    hass: HomeAssistant, mock_source: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.NOT_LOADED


async def test_diagnostics_redacts_credentials(
    hass: HomeAssistant, mock_source: AsyncMock, config_entry: MockConfigEntry
) -> None:
    await setup(hass, config_entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, config_entry)
    assert diagnostics["entry"]["data"]["password"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["username"] == "**REDACTED**"
    assert len(diagnostics["readings"]) == 10
