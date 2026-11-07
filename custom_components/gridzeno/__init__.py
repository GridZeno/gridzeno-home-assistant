"""GridZeno: the GridZeno dongle in Home Assistant, on the home network.

Home Assistant talks to the dongle directly over its local API, never
through GridZeno's servers. Reading works on
every GridZeno subscription. Controlling the battery needs the owner to have
given this Home Assistant the right to (in GridZeno, when approving it), and
GridZeno to be set to manual: while GridZeno controls the plant, Home
Assistant only reads.
"""

from __future__ import annotations

import voluptuous as vol

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import DongleClient
from .const import (
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DURATION,
    ATTR_EXPORT_LIMIT,
    ATTR_FLOOR_SOC,
    ATTR_POWER,
    ATTR_TARGET_SOC,
    CONF_CLIENT_ID,
    CONF_KEY,
    CONF_SCOPE,
    DOMAIN,
    SCOPE_CONTROL,
    SERVICE_CHARGE,
    SERVICE_HOLD,
    SERVICE_LIMIT_EXPORT,
    SERVICE_RELEASE,
    SERVICE_SELL,
)
from .control import async_send, build_command
from .coordinator import GridZenoConfigEntry, GridZenoCoordinator, api_issue_id

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

_DURATION = vol.All(vol.Coerce(int), vol.Range(min=15, max=720))
_PERCENT = vol.All(vol.Coerce(int), vol.Range(min=0, max=100))
_ENTRY = {vol.Required(ATTR_CONFIG_ENTRY_ID): cv.string}

SERVICE_SCHEMAS = {
    SERVICE_CHARGE: vol.Schema(
        {**_ENTRY, vol.Required(ATTR_TARGET_SOC): _PERCENT, vol.Required(ATTR_DURATION): _DURATION,
         vol.Optional(ATTR_EXPORT_LIMIT): _PERCENT}
    ),
    SERVICE_HOLD: vol.Schema(
        {**_ENTRY, vol.Required(ATTR_DURATION): _DURATION, vol.Optional(ATTR_EXPORT_LIMIT): _PERCENT}
    ),
    SERVICE_SELL: vol.Schema(
        {**_ENTRY, vol.Required(ATTR_POWER): vol.All(vol.Coerce(int), vol.Range(min=100, max=30000)),
         vol.Optional(ATTR_FLOOR_SOC): _PERCENT, vol.Required(ATTR_DURATION): _DURATION,
         vol.Optional(ATTR_EXPORT_LIMIT): _PERCENT}
    ),
    SERVICE_LIMIT_EXPORT: vol.Schema(
        {**_ENTRY, vol.Required(ATTR_EXPORT_LIMIT): _PERCENT, vol.Required(ATTR_DURATION): _DURATION}
    ),
    SERVICE_RELEASE: vol.Schema(_ENTRY),
}


def _coordinator(hass: HomeAssistant, entry_id: str) -> GridZenoCoordinator:
    entry = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or not hasattr(entry, "runtime_data"):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_entry")
    coordinator = getattr(entry, "runtime_data", None)
    if not isinstance(coordinator, GridZenoCoordinator):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_entry")
    if entry.data.get(CONF_SCOPE) != SCOPE_CONTROL:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_only")
    return coordinator


async def _run(call: ServiceCall) -> None:
    coordinator = _coordinator(call.hass, call.data[ATTR_CONFIG_ENTRY_ID])
    command = None if call.service == SERVICE_RELEASE else build_command(call.service, dict(call.data))
    await async_send(coordinator, command)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    for service, schema in SERVICE_SCHEMAS.items():
        hass.services.async_register(DOMAIN, service, _run, schema=schema)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GridZenoConfigEntry) -> bool:
    client = DongleClient(
        async_get_clientsession(hass),
        entry.data["host"],
        entry.data["port"],
        entry.data[CONF_CLIENT_ID],
        bytes.fromhex(entry.data[CONF_KEY]),
    )
    coordinator = GridZenoCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GridZenoConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: GridZenoConfigEntry) -> None:
    """Removing the integration takes this Home Assistant off the dongle's
    list too. Best effort: the owner can also remove it in GridZeno."""
    ir.async_delete_issue(hass, DOMAIN, api_issue_id(entry.entry_id))
    client = DongleClient(
        async_get_clientsession(hass),
        entry.data["host"],
        entry.data["port"],
        entry.data[CONF_CLIENT_ID],
        bytes.fromhex(entry.data[CONF_KEY]),
    )
    try:
        await client.remove_self()
    except Exception:  # noqa: BLE001 -- the entry goes either way
        pass
