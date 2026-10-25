"""Base entities for the SolarEdge Optimizers integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Optimizer, OptimizerReading
from .const import DOMAIN
from .coordinator import SolarEdgeOptimizersCoordinator
from .health import OptimizerHealth


def optimizer_display_name(optimizer: Optimizer) -> str:
    """Return a device name for an optimizer.

    The portal often names optimizers by position only, e.g. ``1.1.3``.
    """
    name = optimizer.name
    if not any(char.isalpha() for char in name):
        name = f"Optimizer {name}"
    return name


class SiteEntity(CoordinatorEntity[SolarEdgeOptimizersCoordinator]):
    """An entity that describes the whole site."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SolarEdgeOptimizersCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        layout = coordinator.layout
        self._attr_unique_id = f"{layout.site_id}_{key}"
        # The site device itself is registered in async_setup_entry.
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, layout.site_id)})


class OptimizerEntity(CoordinatorEntity[SolarEdgeOptimizersCoordinator]):
    """An entity that belongs to one optimizer."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SolarEdgeOptimizersCoordinator,
        optimizer: Optimizer,
        key: str,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.optimizer = optimizer
        self._attr_unique_id = f"{optimizer.serial}_{key}"

        layout = coordinator.layout
        reading = coordinator.data.readings.get(optimizer.serial)
        inverter = layout.inverters.get(optimizer.inverter_serial or "")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, optimizer.serial)},
            name=optimizer_display_name(optimizer),
            manufacturer="SolarEdge",
            model=reading.model if reading else None,
            serial_number=optimizer.serial,
            via_device=(DOMAIN, inverter.serial if inverter else layout.site_id),
        )

    @property
    def reading(self) -> OptimizerReading | None:
        """Return the optimizer's latest reading."""
        return self.coordinator.data.readings.get(self.optimizer.serial)

    @property
    def health(self) -> OptimizerHealth | None:
        """Return the optimizer's latest health assessment."""
        return self.coordinator.data.health.get(self.optimizer.serial)
