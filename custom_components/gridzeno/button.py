"""A button: give the inverter its own settings back (ends a running command).

Only for a Home Assistant the owner let control the plant."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_SCOPE, SCOPE_CONTROL
from .control import async_send
from .coordinator import GridZenoConfigEntry, GridZenoCoordinator
from .entity import GridZenoEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: GridZenoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.data.get(CONF_SCOPE) == SCOPE_CONTROL:
        async_add_entities([GridZenoReleaseButton(entry.runtime_data)])


class GridZenoReleaseButton(GridZenoEntity, ButtonEntity):
    _attr_translation_key = "release"

    def __init__(self, coordinator: GridZenoCoordinator) -> None:
        super().__init__(coordinator, "release")

    @property
    def available(self) -> bool:
        control = (self.coordinator.data.state.get("control") or {}) if self.coordinator.data else {}
        return super().available and control.get("writer") == "home_assistant"

    async def async_press(self) -> None:
        await async_send(self.coordinator, None)
