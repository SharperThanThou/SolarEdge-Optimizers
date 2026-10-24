"""Problem binary sensors for SolarEdge optimizers."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Optimizer
from .coordinator import SolarEdgeOptimizersConfigEntry, SolarEdgeOptimizersCoordinator
from .entity import OptimizerEntity, SiteEntity, optimizer_display_name

# Entities only read coordinator data.
PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeOptimizersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up problem sensors for every optimizer and the site."""
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [SiteProblemSensor(coordinator)]
    entities.extend(
        OptimizerProblemSensor(coordinator, optimizer)
        for optimizer in coordinator.layout.optimizers.values()
    )
    async_add_entities(entities)


class OptimizerProblemSensor(OptimizerEntity, BinarySensorEntity):
    """On when the optimizer has a confirmed problem."""

    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(
        self, coordinator: SolarEdgeOptimizersCoordinator, optimizer: Optimizer
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, optimizer, "problem")

    @property
    def is_on(self) -> bool:
        """Return True if the optimizer has a confirmed problem."""
        health = self.health
        return health is not None and health.problem


class SiteProblemSensor(SiteEntity, BinarySensorEntity):
    """On when any optimizer on the site has a confirmed problem."""

    _attr_translation_key = "any_optimizer_problem"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SolarEdgeOptimizersCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "any_optimizer_problem")

    @property
    def is_on(self) -> bool:
        """Return True if any optimizer has a confirmed problem."""
        return any(health.problem for health in self.coordinator.data.health.values())

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """List the optimizers with problems, for use in notifications."""
        optimizers = self.coordinator.layout.optimizers
        return {
            "unhealthy_optimizers": [
                {
                    "name": optimizer_display_name(optimizers[serial]),
                    "serial": serial,
                    "status": health.status.value,
                }
                for serial, health in self.coordinator.data.health.items()
                if health.problem and serial in optimizers
            ]
        }
