"""The client for a GridZeno dongle's local API, version 1.

The protocol's pure core (the key exchange, the code, the signatures) is in
protocol.py.

Everything here talks to the dongle on the home network and nothing else.
"""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any

import aiohttp

from . import protocol as p

TIMEOUT = aiohttp.ClientTimeout(total=10)


class GridZenoError(Exception):
    """Something went wrong talking to the dongle."""


class CannotConnect(GridZenoError):
    """The dongle did not answer."""


class NotAuthorized(GridZenoError):
    """The dongle does not know this Home Assistant (any more)."""


class BadAnswer(GridZenoError):
    """An answer that is not what the protocol promises (also a bad signature)."""


class CommandRefused(GridZenoError):
    """The dongle refused a command; `code` is the protocol's error."""

    def __init__(self, status: int, code: str):
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


@dataclass
class Pairing:
    """A pairing on its way: what the integration keeps until it is approved."""

    request_id: str
    code: str
    key: bytes


class DongleClient:
    """One dongle, at host:port. Without client_id and key it can only read
    /info and pair."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        client_id: str | None = None,
        key: bytes | None = None,
    ) -> None:
        self._session = session
        self.host = host
        self.port = port
        self.client_id = client_id
        self.key = key
        self._counter = 0
        self._lock = asyncio.Lock()

    @property
    def base(self) -> str:
        return f"http://{self.host}:{self.port}"

    async def _raw(self, method: str, path: str, body: bytes = b"", headers: dict | None = None) -> tuple[int, dict, bytes]:
        try:
            async with self._session.request(
                method, self.base + path, data=body or None, headers=headers or {}, timeout=TIMEOUT
            ) as response:
                return response.status, dict(response.headers), await response.read()
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as err:
            raise CannotConnect(str(err)) from err

    @staticmethod
    def _json(raw: bytes) -> dict:
        try:
            data = json.loads(raw)
        except ValueError as err:
            raise BadAnswer("not JSON") from err
        if not isinstance(data, dict):
            raise BadAnswer("not an object")
        return data

    # --- unauthenticated ----------------------------------------------------

    async def info(self) -> dict:
        status, _headers, raw = await self._raw("GET", "/api/v1/info")
        if status != 200:
            raise BadAnswer(f"/info answered {status}")
        data = self._json(raw)
        if not isinstance(data.get("device_id"), str) or not isinstance(data.get("api"), int):
            raise BadAnswer("/info without device_id or api")
        return data

    async def start_pairing(self, name: str) -> Pairing:
        """Asks the dongle for access and does the key exchange. Returns the
        six-digit code to show; the owner approves it in GridZeno."""
        private = p.generate_private_key()
        client_public = p.public_key_hex(private)
        body = json.dumps({"name": name[:40], "client_public": client_public}).encode()
        status, _h, raw = await self._raw("POST", "/api/v1/pair", body, {"Content-Type": "application/json"})
        data = self._json(raw)
        if status != 201:
            raise CommandRefused(status, str(data.get("error")))
        request_id, dongle_public, commitment = data.get("request_id"), data.get("dongle_public"), data.get("commitment")
        if not isinstance(request_id, str) or not p.is_public_key_hex(dongle_public) or not isinstance(commitment, str):
            raise BadAnswer("a pairing answer without its parts")
        client_nonce = secrets.token_bytes(p.NONCE_BYTES)
        body = json.dumps({"client_nonce": client_nonce.hex()}).encode()
        status, _h, raw = await self._raw(
            "POST", f"/api/v1/pair/{request_id}/nonce", body, {"Content-Type": "application/json"}
        )
        data = self._json(raw)
        if status != 200:
            raise CommandRefused(status, str(data.get("error")))
        try:
            dongle_nonce = bytes.fromhex(str(data.get("dongle_nonce")))
        except ValueError as err:
            raise BadAnswer("dongle_nonce is not hex") from err
        if not p.commitment_matches(commitment, dongle_nonce, dongle_public, client_public):
            # Someone between us and the dongle: never go on.
            raise BadAnswer("the dongle's nonce does not match its commitment")
        secret = p.shared_secret(private, dongle_public)
        return Pairing(
            request_id=request_id,
            code=p.pairing_code(client_public, dongle_public, client_nonce, dongle_nonce),
            key=p.derive_key(secret, client_public, dongle_public, client_nonce, dongle_nonce),
        )

    async def pairing_status(self, request_id: str) -> dict:
        status, _h, raw = await self._raw("GET", f"/api/v1/pair/{request_id}")
        if status != 200:
            raise BadAnswer(f"/pair answered {status}")
        return self._json(raw)

    # --- signed ---------------------------------------------------------------

    def _next_counter(self) -> int:
        self._counter = max(int(time.time() * 1000), self._counter + 1)
        return self._counter

    async def _signed(self, method: str, path: str, data: Any = None, proof: str | None = None) -> tuple[int, dict]:
        if self.client_id is None or self.key is None:
            raise NotAuthorized("not paired")
        body = json.dumps(data, separators=(",", ":")).encode() if data is not None else b""
        for _attempt in range(2):
            this_proof = proof or p.counter_proof(self._next_counter())
            headers = {
                "X-GZ-Client": self.client_id,
                "X-GZ-Proof": this_proof,
                "X-GZ-Signature": p.sign_request(self.key, method, path, this_proof, body),
            }
            if body:
                headers["Content-Type"] = "application/json"
            status, response_headers, raw = await self._raw(method, path, body, headers)
            answer = self._json(raw)
            if status == 401 and answer.get("error") == "unknown_client":
                raise NotAuthorized("the dongle does not know this Home Assistant")
            signature = response_headers.get("X-GZ-Signature") or response_headers.get("x-gz-signature")
            if not p.response_signature_ok(self.key, status, this_proof, raw, signature or ""):
                raise BadAnswer("an answer without a valid signature")
            if status == 401 and answer.get("error") == "stale_counter" and proof is None:
                # The dongle has seen a higher counter (a clock that went back).
                last = answer.get("last_counter")
                if isinstance(last, int):
                    self._counter = max(self._counter, last)
                continue
            if status == 401:
                raise NotAuthorized(str(answer.get("error")))
            return status, answer
        raise NotAuthorized("stale_counter")

    async def state(self) -> dict:
        status, data = await self._signed("GET", "/api/v1/state")
        if status != 200:
            raise BadAnswer(f"/state answered {status}")
        return data

    async def plan(self) -> dict:
        status, data = await self._signed("GET", "/api/v1/plan")
        if status != 200:
            raise BadAnswer(f"/plan answered {status}")
        return data

    async def outlook(self) -> dict:
        status, data = await self._signed("GET", "/api/v1/outlook")
        if status != 200:
            raise BadAnswer(f"/outlook answered {status}")
        return data

    async def _nonce(self) -> str:
        status, data = await self._signed("GET", "/api/v1/nonce")
        if status != 200:
            raise CommandRefused(status, str(data.get("error")))
        nonce = data.get("nonce")
        if not isinstance(nonce, str):
            raise BadAnswer("a nonce answer without a nonce")
        return nonce

    async def command(self, command: dict) -> dict:
        """Sends a command to the dongle. Raises
        CommandRefused with the dongle's own error code."""
        async with self._lock:
            nonce = await self._nonce()
            status, data = await self._signed("POST", "/api/v1/command", command, proof=p.nonce_proof(nonce))
        if status != 200:
            raise CommandRefused(status, str(data.get("error")))
        return data.get("command") or {}

    async def release(self) -> None:
        async with self._lock:
            nonce = await self._nonce()
            status, data = await self._signed("DELETE", "/api/v1/command", proof=p.nonce_proof(nonce))
        if status != 200:
            raise CommandRefused(status, str(data.get("error")))

    async def remove_self(self) -> None:
        await self._signed("DELETE", "/api/v1/pair/self")
