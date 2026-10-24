"""The SolarEdge Optimizers integration."""

from __future__ import annotations

from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .api import CloudSource
from .const import CONF_SITE_ID, DOMAIN
from .coordinator import SolarEdgeOptimizersConfigEntry, SolarEdgeOptimizersCoordinator

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(
    hass: HomeAssistant, entry: SolarEdgeOptimizersConfigEntry
) -> bool:
    """Set up SolarEdge Optimizers from a config entry."""
    # A dedicated session: the portal login lives in its cookie jar. It shares
    # Home Assistant's connector, so it is detached rather than closed.
    session = async_create_clientsession(hass, auto_cleanup=False)
    entry.async_on_unload(session.detach)

    source = CloudSource(
        session,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_SITE_ID],
    )
    coordinator = SolarEdgeOptimizersCoordinator(hass, entry, source)
    await coordinator.async_config_entry_first_refresh()

    _async_register_parent_devices(hass, entry, coordinator)

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: SolarEdgeOptimizersConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


def _async_register_parent_devices(
    hass: HomeAssistant,
    entry: SolarEdgeOptimizersConfigEntry,
    coordinator: SolarEdgeOptimizersCoordinator,
) -> None:
    """Register the site and inverter devices that optimizers hang off."""
    registry = dr.async_get(hass)
    layout = coordinator.layout
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, layout.site_id)},
        name=layout.name,
        manufacturer="SolarEdge",
        model="Site",
        configuration_url="https://monitoring.solaredge.com/",
    )
    for inverter in layout.inverters.values():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, inverter.serial)},
            name=inverter.name,
            manufacturer="SolarEdge",
            model=inverter.model,
            serial_number=inverter.serial,
            via_device=(DOMAIN, layout.site_id),
        )
