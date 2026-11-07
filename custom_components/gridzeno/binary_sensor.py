"""Binary sensors: can the dongle read the inverter, reach GridZeno, and is its plan fresh."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import GridZenoConfigEntry, GridZenoCoordinator, GridZenoData
from .entity import GridZenoEntity
from .sensor import _fresh


@dataclass(frozen=True, kw_only=True)
class GridZenoBinaryDescription(BinarySensorEntityDescription):
    value_fn: Callable[[GridZenoData], bool | None]


BINARY_SENSORS = (
    GridZenoBinaryDescription(
        key="inverter_connected", translation_key="inverter_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: bool(d.state.get("inverter_ok")) and _fresh(d),
    ),
    GridZenoBinaryDescription(
        key="cloud_connected", translation_key="cloud_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY, entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.state.get("health") or {}).get("cloud_ok"),
    ),
    GridZenoBinaryDescription(
        key="plan_stale", translation_key="plan_stale", device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.state.get("plan") or {}).get("stale"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GridZenoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    async_add_entities(GridZenoBinarySensor(entry.runtime_data, d) for d in BINARY_SENSORS)


class GridZenoBinarySensor(GridZenoEntity, BinarySensorEntity):
    entity_description: GridZenoBinaryDescription

    def __init__(self, coordinator: GridZenoCoordinator, description: GridZenoBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)
