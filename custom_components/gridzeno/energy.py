"""The solar forecast for Home Assistant's energy dashboard: GridZeno's own
forecast, which the dongle fetched with its prices (Smart and Pro)."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .coordinator import GridZenoCoordinator


async def async_get_solar_forecast(hass: HomeAssistant, config_entry_id: str) -> dict[str, dict[str, float]] | None:
    entry = hass.config_entries.async_get_entry(config_entry_id)
    coordinator = getattr(entry, "runtime_data", None) if entry is not None else None
    if not isinstance(coordinator, GridZenoCoordinator) or coordinator.data is None:
        return None
    outlook = coordinator.data.outlook
    if not outlook.get("available"):
        return None
    hours: dict[str, float] = {}
    for quarter in outlook.get("quarters") or []:
        start = dt_util.parse_datetime(quarter.get("start") or "")
        watts = quarter.get("pv_w")
        if start is None or watts is None:
            continue
        hour = start.replace(minute=0, second=0, microsecond=0).isoformat()
        hours[hour] = hours.get(hour, 0.0) + watts * 0.25
    return {"wh_hours": {hour: round(wh, 1) for hour, wh in sorted(hours.items())}}
