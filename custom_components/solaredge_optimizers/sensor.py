"""Sensors for SolarEdge optimizers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Optimizer, OptimizerReading
from .coordinator import SolarEdgeOptimizersConfigEntry, SolarEdgeOptimizersCoordinator
from .entity import OptimizerEntity, SiteEntity
from .health import HealthStatus, OptimizerHealth

# Entities only read coordinator data.
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class ReadingSensorDescription(SensorEntityDescription):
    """Describes a sensor that reports one field of an optimizer reading."""

    value_fn: Callable[[OptimizerReading], float | datetime | None]
    # The value to report once the reading is stale, e.g. 0 W at night.
    stale_value: float | None = None
    # Timestamps stay meaningful however old they are.
    report_when_stale: bool = False


READING_SENSORS: tuple[ReadingSensorDescription, ...] = (
    ReadingSensorDescription(
        key="power",
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPower.WATT,
        suggested_display_precision=0,
        value_fn=lambda reading: reading.power,
        stale_value=0.0,
    ),
    ReadingSensorDescription(
        key="current",
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        suggested_display_precision=2,
        value_fn=lambda reading: reading.current,
        stale_value=0.0,
    ),
    ReadingSensorDescription(
        key="module_voltage",
        translation_key="module_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda reading: reading.voltage,
    ),
    ReadingSensorDescription(
        key="optimizer_voltage",
        translation_key="optimizer_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        suggested_display_precision=1,
        value_fn=lambda reading: reading.optimizer_voltage,
    ),
    ReadingSensorDescription(
        key="last_measurement",
        translation_key="last_measurement",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda reading: reading.last_measurement,
        report_when_stale=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SolarEdgeOptimizersConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for every optimizer and the site."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        UnhealthyCountSensor(coordinator),
        LatestMeasurementSensor(coordinator),
    ]
    for optimizer in coordinator.layout.optimizers.values():
        entities.extend(
            ReadingSensor(coordinator, optimizer, description)
            for description in READING_SENSORS
        )
        entities.append(HealthStatusSensor(coordinator, optimizer))
        entities.append(PeerRatioSensor(coordinator, optimizer))
    async_add_entities(entities)


class ReadingSensor(OptimizerEntity, SensorEntity):
    """One field of an optimizer's live reading."""

    entity_description: ReadingSensorDescription

    def __init__(
        self,
        coordinator: SolarEdgeOptimizersCoordinator,
        optimizer: Optimizer,
        description: ReadingSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, optimizer, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | datetime | None:
        """Return the reading, or the stale value once it is out of date."""
        reading = self.reading
        description = self.entity_description
        if not description.report_when_stale and self.coordinator.is_stale(reading):
            return description.stale_value
        if reading is None:
            return None
        return description.value_fn(reading)


class HealthStatusSensor(OptimizerEntity, SensorEntity):
    """The optimizer's confirmed health status."""

    _attr_translation_key = "health"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [status.value for status in HealthStatus]

    def __init__(
        self, coordinator: SolarEdgeOptimizersCoordinator, optimizer: Optimizer
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, optimizer, "health")

    @property
    def native_value(self) -> str:
        """Return the confirmed health status."""
        health = self.health
        return (health.status if health else HealthStatus.UNKNOWN).value

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        """Return what the latest samples point to."""
        health = self.health or OptimizerHealth(serial=self.optimizer.serial)
        return {
            "candidate": health.candidate.value,
            "streak": health.streak,
            "peer_median_power": health.peer_median,
            "peer_count": health.peer_count,
            "string": self.optimizer.string_name,
        }


class PeerRatioSensor(OptimizerEntity, SensorEntity):
    """The optimizer's power as a percentage of its peers' median."""

    _attr_translation_key = "peer_ratio"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(
        self, coordinator: SolarEdgeOptimizersCoordinator, optimizer: Optimizer
    ) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, optimizer, "peer_ratio")

    @property
    def native_value(self) -> float | None:
        """Return the ratio, or None when there is too little light to compare."""
        health = self.health
        if health is None or health.ratio is None:
            return None
        return health.ratio * 100


class UnhealthyCountSensor(SiteEntity, SensorEntity):
    """How many optimizers currently have a confirmed problem."""

    _attr_translation_key = "unhealthy_optimizers"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SolarEdgeOptimizersCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "unhealthy_optimizers")

    @property
    def native_value(self) -> int:
        """Return the number of optimizers with a problem."""
        return sum(health.problem for health in self.coordinator.data.health.values())


class LatestMeasurementSensor(SiteEntity, SensorEntity):
    """The newest measurement any optimizer has reported.

    If this stops moving during the day the inverter or its internet
    connection is down, rather than any single optimizer.
    """

    _attr_translation_key = "latest_measurement"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: SolarEdgeOptimizersCoordinator) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator, "latest_measurement")

    @property
    def native_value(self) -> datetime | None:
        """Return the newest optimizer measurement time."""
        times = [
            reading.last_measurement
            for reading in self.coordinator.data.readings.values()
            if reading.last_measurement is not None
        ]
        return max(times, default=None)
