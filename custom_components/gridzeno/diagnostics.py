"""A downloadable report for support, without the key."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_CLIENT_ID, CONF_KEY
from .coordinator import GridZenoConfigEntry

TO_REDACT = {CONF_KEY, CONF_CLIENT_ID, "host"}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: GridZenoConfigEntry) -> dict[str, Any]:
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "state": data.state if data else None,
        "plan": data.plan if data else None,
        "outlook": {k: v for k, v in (data.outlook or {}).items() if k != "quarters"} if data else None,
        "outlook_quarters": len((data.outlook or {}).get("quarters") or []) if data else 0,
    }
