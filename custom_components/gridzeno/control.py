"""Sending a command to the dongle, for the actions and the control entities.

A command is what the dongle's local API takes (local-api-v1.md, "POST
/api/v1/command"); the dongle checks it against the owner's limits and runs
it as a one-block plan. Its refusals become Home Assistant errors with their
own text."""

from __future__ import annotations

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import BadAnswer, CannotConnect, CommandRefused, NotAuthorized
from .const import (
    ATTR_DURATION,
    ATTR_EXPORT_LIMIT,
    ATTR_FLOOR_SOC,
    ATTR_POWER,
    ATTR_TARGET_SOC,
    DOMAIN,
    SERVICE_CHARGE,
    SERVICE_HOLD,
    SERVICE_LIMIT_EXPORT,
    SERVICE_SELL,
)
from .coordinator import GridZenoCoordinator

def build_command(service: str, data: dict) -> dict:
    """An action's fields as the dongle's command (local-api-v1.md)."""
    command: dict = {"duration_min": data[ATTR_DURATION]} if ATTR_DURATION in data else {}
    if service == SERVICE_CHARGE:
        command.update(mode="charge_grid", target_soc=data[ATTR_TARGET_SOC])
    elif service == SERVICE_HOLD:
        command.update(mode="hold")
    elif service == SERVICE_SELL:
        command.update(mode="sell", power_w=data[ATTR_POWER])
        if ATTR_FLOOR_SOC in data:
            command["floor_soc"] = data[ATTR_FLOOR_SOC]
    elif service == SERVICE_LIMIT_EXPORT:
        command.update(mode="inverter")
    if ATTR_EXPORT_LIMIT in data:
        command["export_limit_percent"] = data[ATTR_EXPORT_LIMIT]
    return command


# The dongle's errors with their own text in strings.json.
REFUSALS = {
    "auto_mode", "scope", "not_paired", "no_battery", "export_limit_not_writable", "commands_disabled",
    "target_soc_out_of_range", "power_out_of_range", "floor_out_of_range", "duration_out_of_range",
    "control_not_allowed", "mode_not_supported",
}


async def async_send(coordinator: GridZenoCoordinator, command: dict | None) -> None:
    """Runs `command` on the dongle, or, with None, gives the inverter its own
    settings back. Fetches the state afterwards."""
    try:
        if command is None:
            await coordinator.client.release()
        else:
            await coordinator.client.command(command)
    except CommandRefused as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key=f"refused_{err.code}" if err.code in REFUSALS else "refused",
            translation_placeholders={"code": err.code},
        ) from err
    except NotAuthorized as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="not_authorized") from err
    except CannotConnect as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect") from err
    except BadAnswer as err:
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="bad_answer") from err
    # At once, not the debounced request_refresh: a second command within its
    # ten-second cooldown would otherwise show the first one until the next poll.
    await coordinator.async_refresh()
