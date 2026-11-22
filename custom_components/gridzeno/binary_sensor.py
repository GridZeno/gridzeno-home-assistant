"""Binary sensors: can the dongle read the inverter, reach GridZeno, and is its plan fresh;
and the signals for the owner's own automations (signals.py): a cheap hour, a
surplus and a negative price now."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import signals
from .coordinator import GridZenoConfigEntry, GridZenoCoordinator, GridZenoData
from .entity import GridZenoEntity
from .sensor import _fresh, _quarter_now, cheap_hours_option, surplus_option


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
    coordinator = entry.runtime_data
    entities: list[BinarySensorEntity] = [GridZenoBinarySensor(coordinator, d) for d in BINARY_SENSORS]
    entities += [GridZenoCheapHourSensor(coordinator), GridZenoNegativePriceSensor(coordinator),
                 GridZenoSurplusSensor(coordinator)]
    async_add_entities(entities)


class GridZenoBinarySensor(GridZenoEntity, BinarySensorEntity):
    entity_description: GridZenoBinaryDescription

    def __init__(self, coordinator: GridZenoCoordinator, description: GridZenoBinaryDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)


class _OutlookSignal(GridZenoEntity, BinarySensorEntity):
    """A signal from the prices: only with prices (Smart and Pro)."""

    def __init__(self, coordinator: GridZenoCoordinator, key: str) -> None:
        super().__init__(coordinator, key)
        self._attr_translation_key = key

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None and bool(
            self.coordinator.data.outlook.get("available")
        )


class GridZenoCheapHourSensor(_OutlookSignal):
    """On in the day's N cheapest hours (N in the integration's options)."""

    def __init__(self, coordinator: GridZenoCoordinator) -> None:
        super().__init__(coordinator, "cheap_hour_now")

    def _quarters(self) -> list[dict]:
        return self.coordinator.data.outlook.get("quarters") or []

    @property
    def is_on(self) -> bool | None:
        return signals.cheap_now(
            self._quarters(), cheap_hours_option(self.coordinator), dt_util.utcnow(), dt_util.get_default_time_zone()
        )

    @property
    def extra_state_attributes(self) -> dict:
        n = cheap_hours_option(self.coordinator)
        hours = signals.cheap_hours(self._quarters(), n, dt_util.get_default_time_zone())
        return {
            "hours_per_day": n,
            "hours": [{"start": h.start.isoformat(), "price": round(h.price, 4)} for h in hours],
        }


class GridZenoNegativePriceSensor(_OutlookSignal):
    """On when the buy price now is below zero."""

    def __init__(self, coordinator: GridZenoCoordinator) -> None:
        super().__init__(coordinator, "negative_price_now")

    @property
    def is_on(self) -> bool | None:
        return signals.negative_now(_quarter_now(self.coordinator.data))


class GridZenoSurplusSensor(GridZenoEntity, RestoreEntity, BinarySensorEntity):
    """On when the plant sells over X W and the battery is full, or there is
    no battery; with hysteresis (signals.SurplusTracker). Every subscription:
    it reads only the measurements. Starts in the state it had before a
    restart or a reload (a changed option reloads the integration), so a
    relay on it is not switched off and on again."""

    _attr_translation_key = "surplus_now"

    def __init__(self, coordinator: GridZenoCoordinator) -> None:
        super().__init__(coordinator, "surplus_now")
        self._tracker = signals.SurplusTracker()
        self._value: bool | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None and last.state in (STATE_ON, STATE_OFF):
            self._tracker.restore(last.state == STATE_ON, dt_util.utcnow())
        self._update()

    def _update(self) -> None:
        data = self.coordinator.data
        if data is None or not _fresh(data):
            self._value = None
            return
        self._value = self._tracker.update(data.state, surplus_option(self.coordinator), dt_util.utcnow())

    @callback
    def _handle_coordinator_update(self) -> None:
        self._update()
        super()._handle_coordinator_update()

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None and _fresh(self.coordinator.data)

    @property
    def is_on(self) -> bool | None:
        return self._value

    @property
    def extra_state_attributes(self) -> dict:
        return {"threshold_w": surplus_option(self.coordinator)}
