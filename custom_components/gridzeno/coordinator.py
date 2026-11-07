"""Fetches the dongle's state every few seconds, and its plan and prices
when they change."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import BadAnswer, CannotConnect, DongleClient, NotAuthorized
from .const import CONF_DEVICE_ID, DOMAIN, SCAN_INTERVAL, SETTING_DEFAULTS, SLOW_REFRESH
from .protocol import API_VERSION

_LOGGER = logging.getLogger(__name__)

type GridZenoConfigEntry = ConfigEntry[GridZenoCoordinator]


@dataclass
class GridZenoData:
    """What the entities read: the dongle's last answers."""

    state: dict
    plan: dict = field(default_factory=dict)
    outlook: dict = field(default_factory=dict)


class GridZenoCoordinator(DataUpdateCoordinator[GridZenoData]):
    """One dongle."""

    config_entry: GridZenoConfigEntry

    def __init__(self, hass: HomeAssistant, entry: GridZenoConfigEntry, client: DongleClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {client.host}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        # The control numbers' values, what the select sends (const.py).
        self.command_settings: dict[str, float | None] = dict(SETTING_DEFAULTS)
        self._plan_version: str | None = None
        self._outlook_version: str | None = None
        self._slow_at: datetime | None = None
        self._api_seen: int | None = None

    async def _async_update_data(self) -> GridZenoData:
        try:
            state = await self.client.state()
            self._check_api(state.get("api"))
            previous = self.data
            plan = previous.plan if previous else {}
            outlook = previous.outlook if previous else {}
            versions = state.get("versions") or {}
            now = dt_util.utcnow()
            slow_due = self._slow_at is None or now - self._slow_at >= SLOW_REFRESH
            if slow_due or versions.get("plan") != self._plan_version:
                plan = await self.client.plan()
                self._plan_version = versions.get("plan")
            if slow_due or versions.get("outlook") != self._outlook_version:
                outlook = await self.client.outlook()
                self._outlook_version = versions.get("outlook")
            if slow_due:
                self._slow_at = now
        except NotAuthorized as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (CannotConnect, BadAnswer) as err:
            raise UpdateFailed(f"The dongle did not answer: {err}") from err
        return GridZenoData(state=state, plan=plan, outlook=outlook)

    def _check_api(self, api: object) -> None:
        """A repair in Home Assistant when the dongle speaks another version of
        the local API than this integration: it says which of the two to
        update. Removed again when they match."""
        if not isinstance(api, int) or api == self._api_seen:
            return
        self._api_seen = api
        issue_id = api_issue_id(self.config_entry.entry_id)
        if api == API_VERSION:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="dongle_api_newer" if api > API_VERSION else "dongle_api_older",
            translation_placeholders={
                "device_id": self.config_entry.data[CONF_DEVICE_ID],
                "dongle_api": str(api),
                "integration_api": str(API_VERSION),
            },
        )


def api_issue_id(entry_id: str) -> str:
    return f"api_version_{entry_id}"
