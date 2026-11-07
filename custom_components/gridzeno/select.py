"""The command select: what Home Assistant asks the dongle to do now.

Shows the running command (or "The inverter's own settings"). Choosing a
command sends it with the values of the command numbers (number.py); choosing
"The inverter's own settings" ends a running one. Works only while you
control the plant yourself: while GridZeno does, the dongle refuses and the
error says why. Only for a Home Assistant the owner let control the plant."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    ATTR_DURATION,
    ATTR_EXPORT_LIMIT,
    ATTR_FLOOR_SOC,
    ATTR_POWER,
    ATTR_TARGET_SOC,
    COMMAND_MODES,
    COMMAND_NONE,
    COMMAND_OPTIONS,
    CONF_SCOPE,
    SCOPE_CONTROL,
    SERVICE_CHARGE,
    SERVICE_HOLD,
    SERVICE_LIMIT_EXPORT,
    SERVICE_SELL,
    SETTING_DURATION,
    SETTING_EXPORT_LIMIT,
    SETTING_FLOOR,
    SETTING_POWER,
    SETTING_TARGET_SOC,
)
from .control import async_send, build_command
from .coordinator import GridZenoConfigEntry, GridZenoCoordinator
from .entity import GridZenoEntity

# Each option as an action with its fields, built from the command numbers.
_SERVICES = {"charge_grid": SERVICE_CHARGE, "hold": SERVICE_HOLD, "sell": SERVICE_SELL, "limit_export": SERVICE_LIMIT_EXPORT}


def command_for(option: str, settings: dict) -> dict | None:
    """The dongle command an option sends, or None to end a running one."""
    if option == COMMAND_NONE:
        return None
    data: dict = {ATTR_DURATION: int(settings[SETTING_DURATION])}
    if option == "charge_grid":
        data[ATTR_TARGET_SOC] = int(settings[SETTING_TARGET_SOC])
    elif option == "sell":
        data[ATTR_POWER] = int(settings[SETTING_POWER])
        if settings.get(SETTING_FLOOR) is not None:
            data[ATTR_FLOOR_SOC] = int(settings[SETTING_FLOOR])
    elif option == "limit_export":
        data[ATTR_EXPORT_LIMIT] = int(settings[SETTING_EXPORT_LIMIT])
    return build_command(_SERVICES[option], data)


async def async_setup_entry(
    hass: HomeAssistant, entry: GridZenoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.data.get(CONF_SCOPE) == SCOPE_CONTROL:
        async_add_entities([GridZenoCommandSelect(entry.runtime_data)])


class GridZenoCommandSelect(GridZenoEntity, SelectEntity):
    _attr_translation_key = "command"
    _attr_options = COMMAND_OPTIONS

    def __init__(self, coordinator: GridZenoCoordinator) -> None:
        super().__init__(coordinator, "command")

    @property
    def current_option(self) -> str | None:
        if self.coordinator.data is None:
            return None
        command = (self.coordinator.data.state.get("control") or {}).get("local_command") or {}
        return COMMAND_MODES.get(command.get("mode"), COMMAND_NONE)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.coordinator.data is None:
            return None
        command = (self.coordinator.data.state.get("control") or {}).get("local_command")
        return {"until": command.get("until")} if command else None

    async def async_select_option(self, option: str) -> None:
        await async_send(self.coordinator, command_for(option, self.coordinator.command_settings))
