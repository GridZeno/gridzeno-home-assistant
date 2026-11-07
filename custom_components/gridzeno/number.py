"""Numbers: what the next command from the "Command" select asks for.

Kept in Home Assistant and restored over a restart; the select sends them
with the command. Changing one does not change a running command. Only for
a Home Assistant the owner let control the plant."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntityDescription, NumberMode, RestoreNumber
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CONF_SCOPE,
    SCOPE_CONTROL,
    SETTING_DURATION,
    SETTING_EXPORT_LIMIT,
    SETTING_FLOOR,
    SETTING_POWER,
    SETTING_TARGET_SOC,
)
from .coordinator import GridZenoConfigEntry, GridZenoCoordinator
from .entity import GridZenoEntity

# A sale is never asked for above this when the dongle does not know the
# inverter's rating (the dongle refuses above the rating anyway).
MAX_SELL_W = 30000


@dataclass(frozen=True, kw_only=True)
class GridZenoNumberDescription(NumberEntityDescription):
    pass


NUMBERS = (
    GridZenoNumberDescription(
        key=SETTING_TARGET_SOC, translation_key=SETTING_TARGET_SOC, native_unit_of_measurement=PERCENTAGE,
        native_min_value=1, native_max_value=100, native_step=1, mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    GridZenoNumberDescription(
        key=SETTING_POWER, translation_key=SETTING_POWER, native_unit_of_measurement=UnitOfPower.WATT,
        native_min_value=100, native_max_value=MAX_SELL_W, native_step=100, mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    GridZenoNumberDescription(
        key=SETTING_FLOOR, translation_key=SETTING_FLOOR, native_unit_of_measurement=PERCENTAGE,
        native_min_value=0, native_max_value=100, native_step=1, mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
    GridZenoNumberDescription(
        key=SETTING_DURATION, translation_key=SETTING_DURATION, native_unit_of_measurement=UnitOfTime.MINUTES,
        native_min_value=15, native_max_value=720, native_step=15, mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
    ),
    GridZenoNumberDescription(
        key=SETTING_EXPORT_LIMIT, translation_key=SETTING_EXPORT_LIMIT, native_unit_of_measurement=PERCENTAGE,
        native_min_value=0, native_max_value=100, native_step=1, mode=NumberMode.SLIDER,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: GridZenoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.data.get(CONF_SCOPE) == SCOPE_CONTROL:
        async_add_entities(GridZenoCommandNumber(entry.runtime_data, d) for d in NUMBERS)


class GridZenoCommandNumber(GridZenoEntity, RestoreNumber):
    entity_description: GridZenoNumberDescription

    def __init__(self, coordinator: GridZenoCoordinator, description: GridZenoNumberDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last is not None and last.native_value is not None:
            self.coordinator.command_settings[self.entity_description.key] = last.native_value

    @property
    def available(self) -> bool:
        # A setting, not a reading: it can be changed while the dongle is away.
        return True

    @property
    def native_value(self) -> float | None:
        return self.coordinator.command_settings.get(self.entity_description.key)

    @property
    def native_max_value(self) -> float:
        if self.entity_description.key == SETTING_POWER and self.coordinator.data is not None:
            rated = (self.coordinator.data.state.get("control") or {}).get("rated_w")
            if isinstance(rated, int) and rated > 0:
                return float(min(rated, MAX_SELL_W))
        return super().native_max_value

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.command_settings[self.entity_description.key] = value
        self.async_write_ha_state()
