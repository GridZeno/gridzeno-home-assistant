"""Adding a GridZeno dongle: found on the network or typed in, then paired.

Pairing is the protocol's key exchange (protocol.py): Home Assistant and the
dongle work out the same key without it ever crossing the network, and both
show the same six-digit code. The owner compares the code in GridZeno (web
app or phone app, Integrations, Home Assistant) and approves it there, to
read or to read and control. This flow waits for that answer.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from . import protocol as p
from .api import BadAnswer, CannotConnect, CommandRefused, DongleClient, Pairing
from .const import (
    CONF_CLIENT_ID,
    CONF_DEVICE_ID,
    CONF_KEY,
    CONF_SCOPE,
    DEFAULT_PORT,
    DOMAIN,
    PAIR_POLL_SECONDS,
    PAIR_TIMEOUT_SECONDS,
)


class GridZenoConfigFlow(ConfigFlow, domain=DOMAIN):
    """One entry per dongle; the dongle's own id is the unique id."""

    VERSION = 1

    def __init__(self) -> None:
        self._host: str | None = None
        self._port: int = DEFAULT_PORT
        self._device_id: str | None = None
        self._pairing: Pairing | None = None
        self._wait: asyncio.Task | None = None
        self._result: dict | None = None

    def _client(self) -> DongleClient:
        return DongleClient(async_get_clientsession(self.hass), self._host, self._port)

    async def _read_info(self) -> dict:
        """The dongle's /info, or raises a flow-ending reason."""
        info = await self._client().info()
        if info.get("api") != p.API_VERSION:
            raise _Abort("unsupported_api")
        return info

    # --- finding the dongle -------------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host, self._port = user_input[CONF_HOST].strip(), user_input[CONF_PORT]
            try:
                info = await self._read_info()
            except CannotConnect:
                errors["base"] = "cannot_connect"
            except BadAnswer:
                errors["base"] = "not_a_dongle"
            except _Abort as abort:
                return self.async_abort(reason=abort.reason)
            else:
                self._device_id = info["device_id"]
                await self.async_set_unique_id(self._device_id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})
                return await self.async_step_pair()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=self._host or ""): str,
                 vol.Required(CONF_PORT, default=self._port): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))}
            ),
            errors=errors,
        )

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        device_id = discovery_info.properties.get("id")
        if not device_id:
            return self.async_abort(reason="not_a_dongle")
        self._host, self._port, self._device_id = discovery_info.host, discovery_info.port or DEFAULT_PORT, device_id
        await self.async_set_unique_id(device_id)
        # A dongle that moved to another address keeps working.
        self._abort_if_unique_id_configured(updates={CONF_HOST: self._host, CONF_PORT: self._port})
        self.context["title_placeholders"] = {"device_id": device_id}
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="zeroconf_confirm",
                description_placeholders={"device_id": self._device_id or "", "host": self._host or ""},
            )
        try:
            await self._read_info()
        except (CannotConnect, BadAnswer):
            return self.async_abort(reason="cannot_connect")
        except _Abort as abort:
            return self.async_abort(reason=abort.reason)
        return await self.async_step_pair()

    # --- pairing --------------------------------------------------------------------

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if self._pairing is None:
            name = self.hass.config.location_name or "Home Assistant"
            try:
                self._pairing = await self._client().start_pairing(name)
            except CannotConnect:
                return self.async_abort(reason="cannot_connect")
            except CommandRefused as err:
                return self.async_abort(reason="no_room" if err.code == "no_room" else "too_many")
            except BadAnswer:
                return self.async_abort(reason="pairing_failed")
        if self._wait is None:
            self._wait = self.hass.async_create_task(self._wait_for_answer())
        if not self._wait.done():
            return self.async_show_progress(
                step_id="pair",
                progress_action="wait_for_approval",
                description_placeholders={"code": f"{self._pairing.code[:3]} {self._pairing.code[3:]}"},
                progress_task=self._wait,
            )
        self._result = self._wait.result()
        status = (self._result or {}).get("status")
        if status == "approved":
            return self.async_show_progress_done(next_step_id="finish")
        return self.async_show_progress_done(next_step_id="denied" if status == "denied" else "expired")

    async def _wait_for_answer(self) -> dict:
        assert self._pairing is not None
        client = self._client()
        loop = asyncio.get_running_loop()
        deadline = loop.time() + PAIR_TIMEOUT_SECONDS
        while loop.time() < deadline:
            try:
                answer = await client.pairing_status(self._pairing.request_id)
            except (CannotConnect, BadAnswer):
                answer = {"status": "pending"}  # a hiccup on the network: ask again
            if answer.get("status") in ("approved", "denied", "expired"):
                return answer
            await asyncio.sleep(PAIR_POLL_SECONDS)
        return {"status": "expired"}

    async def async_step_finish(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        assert self._pairing is not None and self._result is not None
        data = {
            CONF_HOST: self._host,
            CONF_PORT: self._port,
            CONF_DEVICE_ID: self._device_id,
            CONF_CLIENT_ID: self._result.get("client_id") or self._pairing.request_id,
            CONF_KEY: self._pairing.key.hex(),
            CONF_SCOPE: self._result.get("scope") or "read",
        }
        if self.source == "reauth":
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data=data)
        return self.async_create_entry(title=f"GridZeno {self._device_id}", data=data)

    async def async_step_denied(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="denied")

    async def async_step_expired(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_abort(reason="expired")

    # --- the dongle forgot us, or it moved --------------------------------------------

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._host, self._port = entry_data[CONF_HOST], entry_data[CONF_PORT]
        self._device_id = entry_data[CONF_DEVICE_ID]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(
                step_id="reauth_confirm", description_placeholders={"device_id": self._device_id or ""}
            )
        return await self.async_step_pair()

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            self._host, self._port = user_input[CONF_HOST].strip(), user_input[CONF_PORT]
            try:
                info = await self._read_info()
            except (CannotConnect, BadAnswer):
                errors["base"] = "cannot_connect"
            except _Abort as abort:
                return self.async_abort(reason=abort.reason)
            else:
                await self.async_set_unique_id(info["device_id"])
                self._abort_if_unique_id_mismatch(reason="another_dongle")
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_HOST: self._host, CONF_PORT: self._port}
                )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {vol.Required(CONF_HOST, default=entry.data[CONF_HOST]): str,
                 vol.Required(CONF_PORT, default=entry.data[CONF_PORT]): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535))}
            ),
            errors=errors,
        )


class _Abort(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
