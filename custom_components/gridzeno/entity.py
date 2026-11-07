"""The dongle as a device in Home Assistant."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_DEVICE_ID, DOMAIN, MANUFACTURER
from .coordinator import GridZenoCoordinator


class GridZenoEntity(CoordinatorEntity[GridZenoCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: GridZenoCoordinator, key: str) -> None:
        super().__init__(coordinator)
        device_id = coordinator.config_entry.data[CONF_DEVICE_ID]
        self._attr_unique_id = f"{device_id}_{key}"
        state = coordinator.data.state if coordinator.data else {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            name=f"GridZeno {device_id}",
            manufacturer=MANUFACTURER,
            model="Bridge" if state.get("firmware") == "bridge" else "Dongle",
            sw_version=state.get("firmware"),
            serial_number=device_id,
        )
