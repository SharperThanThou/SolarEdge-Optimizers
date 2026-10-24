"""Shared fixtures for SolarEdge Optimizers tests."""

from __future__ import annotations

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.solaredge_optimizers.api import (
    Inverter,
    Optimizer,
    OptimizerReading,
    SiteLayout,
)
from custom_components.solaredge_optimizers.const import CONF_SITE_ID, DOMAIN

SITE_ID = "1234567"
INVERTER = "7E0A1B2C-3D"
STRING_A = [f"2100000{i}-AB" for i in range(1, 6)]
STRING_B = [f"2200000{i}-CD" for i in range(1, 6)]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom integrations in every test."""


def make_layout() -> SiteLayout:
    """Return a site with one inverter and two strings of five optimizers."""
    optimizers = {}
    for string_no, serials in enumerate((STRING_A, STRING_B), start=1):
        for index, serial in enumerate(serials, start=1):
            optimizers[serial] = Optimizer(
                serial=serial,
                name=f"1.{string_no}.{index}",
                inverter_serial=INVERTER,
                string_id=f"{INVERTER}_{string_no}",
                string_name=f"String 1.{string_no}",
            )
    return SiteLayout(
        site_id=SITE_ID,
        name=f"SolarEdge site {SITE_ID}",
        inverters={
            INVERTER: Inverter(serial=INVERTER, name="Inverter 1", model="SE7600H")
        },
        optimizers=optimizers,
    )


def make_readings(
    when: datetime,
    power: float = 300.0,
    overrides: dict[str, float | None] | None = None,
) -> dict[str, OptimizerReading]:
    """Return readings for every optimizer, all at ``power`` unless overridden."""
    overrides = overrides or {}
    return {
        serial: OptimizerReading(
            serial=serial,
            power=overrides.get(serial, power),
            voltage=38.0,
            optimizer_voltage=40.0,
            current=8.0,
            last_measurement=when,
            model="P401",
        )
        for serial in STRING_A + STRING_B
    }


@pytest.fixture
def now() -> datetime:
    """Return a fixed midday instant."""
    return datetime(2026, 6, 21, 18, 0, tzinfo=UTC)


@pytest.fixture
def mock_source(now: datetime) -> Generator[AsyncMock]:
    """Patch the cloud source used by the integration and the config flow."""
    source = AsyncMock()
    source.async_get_layout.return_value = make_layout()
    source.async_get_readings.return_value = make_readings(now - timedelta(minutes=5))
    with (
        patch(
            "custom_components.solaredge_optimizers.CloudSource", return_value=source
        ),
        patch(
            "custom_components.solaredge_optimizers.config_flow.CloudSource",
            return_value=source,
        ),
    ):
        yield source


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Return a config entry for the test site."""
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=SITE_ID,
        title=f"SolarEdge site {SITE_ID}",
        data={
            CONF_SITE_ID: SITE_ID,
            "username": "me@example.com",
            "password": "secret",
        },
    )
