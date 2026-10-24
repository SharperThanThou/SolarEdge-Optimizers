"""Polling coordinator for SolarEdge optimizer data."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    OptimizerReading,
    OptimizerSource,
    SiteLayout,
    SourceAuthError,
    SourceError,
)
from .const import (
    CONF_CONSECUTIVE_SAMPLES,
    CONF_LOW_OUTPUT_PERCENT,
    CONF_MIN_PEER_POWER,
    CONF_SCAN_INTERVAL,
    CONF_STALE_AFTER,
    DEFAULT_CONSECUTIVE_SAMPLES,
    DEFAULT_LOW_OUTPUT_PERCENT,
    DEFAULT_MIN_PEER_POWER,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_STALE_AFTER,
    DOMAIN,
)
from .health import HealthMonitor, HealthSettings, OptimizerHealth

_LOGGER = logging.getLogger(__name__)

type SolarEdgeOptimizersConfigEntry = ConfigEntry[SolarEdgeOptimizersCoordinator]


@dataclass(frozen=True, slots=True)
class SiteData:
    """Everything the entities need from one poll."""

    readings: dict[str, OptimizerReading]
    health: dict[str, OptimizerHealth]
    fetched_at: datetime


def health_settings_from_options(options: dict) -> HealthSettings:
    """Build health settings from config entry options."""
    return HealthSettings(
        low_output_ratio=options.get(
            CONF_LOW_OUTPUT_PERCENT, DEFAULT_LOW_OUTPUT_PERCENT
        )
        / 100,
        min_peer_power=float(options.get(CONF_MIN_PEER_POWER, DEFAULT_MIN_PEER_POWER)),
        consecutive_samples=int(
            options.get(CONF_CONSECUTIVE_SAMPLES, DEFAULT_CONSECUTIVE_SAMPLES)
        ),
        stale_after=timedelta(
            minutes=options.get(CONF_STALE_AFTER, DEFAULT_STALE_AFTER)
        ),
    )


class SolarEdgeOptimizersCoordinator(DataUpdateCoordinator[SiteData]):
    """Fetches optimizer readings and keeps their health up to date."""

    config_entry: SolarEdgeOptimizersConfigEntry
    layout: SiteLayout

    def __init__(
        self,
        hass: HomeAssistant,
        entry: SolarEdgeOptimizersConfigEntry,
        source: OptimizerSource,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                minutes=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.source = source
        self.monitor = HealthMonitor(health_settings_from_options(dict(entry.options)))

    async def _async_setup(self) -> None:
        """Fetch the site layout once, before the first refresh."""
        try:
            self.layout = await self.source.async_get_layout()
        except SourceAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SourceError as err:
            raise UpdateFailed(f"Error fetching site layout: {err}") from err

    async def _async_update_data(self) -> SiteData:
        """Fetch the latest readings and assess optimizer health."""
        try:
            readings = await self.source.async_get_readings()
        except SourceAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except SourceError as err:
            raise UpdateFailed(f"Error fetching optimizer data: {err}") from err

        now = dt_util.utcnow()
        health = self.monitor.update(self.layout, readings, now)
        return SiteData(readings=readings, health=health, fetched_at=now)

    def is_stale(self, reading: OptimizerReading | None) -> bool:
        """Return True if a reading is too old to describe the present."""
        if reading is None or reading.last_measurement is None:
            return True
        return (
            dt_util.utcnow() - reading.last_measurement
            > self.monitor.settings.stale_after
        )
