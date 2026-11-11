"""GridZeno's local API, version 1: the protocol's pure core.

Both ends of the protocol share this module: the dongle uses its half and the
integration the client's half. The dongle's firmware implements the same
functions in C++ against the same test vectors, so the two cannot drift apart
unnoticed. The code matches the firmware's reference module; only comments and
docstrings differ (internal file references are left out of this public copy).

Nothing here does I/O, keeps state or reads a clock, so all of it can be
tested with fixed inputs. The only dependency is `cryptography` (the P-256
key exchange), which Home Assistant ships.

Pairing (a 6-digit code compared in GridZeno, and a key that never crosses
the network):

    client                                   dongle
    POST /pair {name, client_pub}  ------->  new key pair, nonce Nb
                                   <-------  {request_id, dongle_pub, commitment(Nb)}
    POST /pair/{id}/nonce {Na}     ------->
                                   <-------  {dongle_nonce: Nb}
    checks the commitment; both compute the same code from (PKa, PKb, Na, Nb)
    and the same key from ECDH(a, PKb) == ECDH(b, PKa)

The commitment is what makes the code worth comparing. Without it, someone in
the middle could try new key pairs toward Home Assistant, after learning the
dongle's code, until the two codes matched (a million tries take seconds).
Committed to Nb before seeing Na, neither side can steer the code (the same
idea as Bluetooth's numeric comparison).
"""

from __future__ import annotations

import hashlib
import hmac

from cryptography.hazmat.primitives.asymmetric import ec

API_VERSION = 1

# Domain-separation labels. Changing one is a new protocol version.
CODE_LABEL = b"gridzeno-pairing-code-v1"
KEY_INFO_LABEL = b"gridzeno-local-api-v1"
RESPONSE_LABEL = "response"

NONCE_BYTES = 16
KEY_BYTES = 32
PUBLIC_KEY_HEX_LENGTH = 130  # an uncompressed SEC1 point: 04 || X || Y

SCOPES = ("read", "control")

# The largest counter a client may use: JavaScript's safe integer, so a
# counter never loses precision anywhere it is parsed.
MAX_COUNTER = 2**53 - 1


# --- keys ----------------------------------------------------------------


def generate_private_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def private_key_from_hex(scalar_hex: str) -> ec.EllipticCurvePrivateKey:
    """A private key from its scalar. Only the test vectors need this."""
    return ec.derive_private_key(int(scalar_hex, 16), ec.SECP256R1())


def public_key_hex(private_key: ec.EllipticCurvePrivateKey) -> str:
    numbers = private_key.public_key().public_numbers()
    return "04" + numbers.x.to_bytes(32, "big").hex() + numbers.y.to_bytes(32, "big").hex()


def is_public_key_hex(value: object) -> bool:
    """A well-formed point on P-256. Checked before anything uses a key a
    client sent: a point off the curve is the classic way to leak a
    private key through ECDH."""
    if not isinstance(value, str) or len(value) != PUBLIC_KEY_HEX_LENGTH:
        return False
    try:
        _load_public_key(value)
    except ValueError:
        return False
    return True


def _load_public_key(public_hex: str) -> ec.EllipticCurvePublicKey:
    return ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), bytes.fromhex(public_hex))


def shared_secret(private_key: ec.EllipticCurvePrivateKey, peer_public_hex: str) -> bytes:
    """The ECDH secret: the X coordinate of the shared point, 32 bytes."""
    return private_key.exchange(ec.ECDH(), _load_public_key(peer_public_hex))


# --- pairing ---------------------------------------------------------------


def commitment(dongle_nonce: bytes, dongle_public_hex: str, client_public_hex: str) -> str:
    """The dongle's promise of its nonce, sent before it has seen the client's."""
    return hmac.new(
        dongle_nonce, bytes.fromhex(dongle_public_hex) + bytes.fromhex(client_public_hex), hashlib.sha256
    ).hexdigest()


def commitment_matches(
    expected_hex: str, dongle_nonce: bytes, dongle_public_hex: str, client_public_hex: str
) -> bool:
    return hmac.compare_digest(expected_hex, commitment(dongle_nonce, dongle_public_hex, client_public_hex))


def pairing_code(
    client_public_hex: str, dongle_public_hex: str, client_nonce: bytes, dongle_nonce: bytes
) -> str:
    """The six digits both ends show, and the owner compares in GridZeno."""
    digest = hashlib.sha256(
        CODE_LABEL
        + bytes.fromhex(client_public_hex)
        + bytes.fromhex(dongle_public_hex)
        + client_nonce
        + dongle_nonce
    ).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 1_000_000:06d}"


def hkdf_sha256(ikm: bytes, salt: bytes, info: bytes, length: int = KEY_BYTES) -> bytes:
    """RFC 5869, written out so the firmware can do exactly the same with
    only HMAC-SHA256 (no HKDF module needed on the ESP32)."""
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def derive_key(
    secret: bytes, client_public_hex: str, dongle_public_hex: str, client_nonce: bytes, dongle_nonce: bytes
) -> bytes:
    """The key every later request is signed with. Bound to both public keys
    and both nonces, so a key from one pairing never works in another."""
    return hkdf_sha256(
        secret,
        salt=client_nonce + dongle_nonce,
        info=KEY_INFO_LABEL + bytes.fromhex(client_public_hex) + bytes.fromhex(dongle_public_hex),
    )


# --- signed requests and responses ----------------------------------------


def counter_proof(counter: int) -> str:
    return f"c:{counter}"


def nonce_proof(nonce_hex: str) -> str:
    return f"n:{nonce_hex}"


def parse_proof(proof: str) -> tuple[str, int | str] | None:
    """("c", counter) or ("n", nonce_hex), or None for anything else."""
    if not isinstance(proof, str) or len(proof) > 64:
        return None
    if proof.startswith("c:"):
        digits = proof[2:]
        if not digits.isdigit() or (len(digits) > 1 and digits[0] == "0"):
            return None
        counter = int(digits)
        if counter < 1 or counter > MAX_COUNTER:
            return None
        return "c", counter
    if proof.startswith("n:"):
        nonce = proof[2:]
        if len(nonce) != 2 * NONCE_BYTES or any(ch not in "0123456789abcdef" for ch in nonce):
            return None
        return "n", nonce
    return None


def _body_hash(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_request(method: str, path: str, proof: str, body: bytes) -> bytes:
    """What a request's signature covers. `path` includes the query string
    exactly as sent; `body` is the raw bytes (empty for a GET)."""
    return f"{method.upper()}\n{path}\n{proof}\n{_body_hash(body)}".encode()


def sign_request(key: bytes, method: str, path: str, proof: str, body: bytes = b"") -> str:
    return hmac.new(key, canonical_request(method, path, proof, body), hashlib.sha256).hexdigest()


def request_signature_ok(key: bytes, method: str, path: str, proof: str, body: bytes, signature: str) -> bool:
    if not isinstance(signature, str):
        return False
    return hmac.compare_digest(signature, sign_request(key, method, path, proof, body))


def canonical_response(status: int, proof: str, body: bytes) -> bytes:
    """What the dongle's answer is signed over: the request's own proof, so
    an answer cannot be replayed as the answer to another request."""
    return f"{RESPONSE_LABEL}\n{status}\n{proof}\n{_body_hash(body)}".encode()


def sign_response(key: bytes, status: int, proof: str, body: bytes) -> str:
    return hmac.new(key, canonical_response(status, proof, body), hashlib.sha256).hexdigest()


def response_signature_ok(key: bytes, status: int, proof: str, body: bytes, signature: str) -> bool:
    if not isinstance(signature, str):
        return False
    return hmac.compare_digest(signature, sign_response(key, status, proof, body))


def counter_accepted(last_counter: int, counter: int) -> bool:
    """A read's counter must be higher than the last one this boot."""
    return last_counter < counter <= MAX_COUNTER


# --- commands -------------------------------------------------------------

MODES = ("inverter", "charge_grid", "hold", "sell")
COMMAND_FIELDS = {"mode", "target_soc", "power_w", "floor_soc", "duration_min", "export_limit_percent"}
MIN_DURATION_MIN = 15
MAX_DURATION_MIN = 720
MIN_SELL_W = 100
# The cap when the dongle does not know the inverter's own rating yet.
MAX_SELL_W_UNKNOWN_RATING = 30_000

# Each error and the HTTP status it is answered with.
COMMAND_ERRORS = {
    "scope": 403,
    "not_paired": 403,
    "auto_mode": 409,
    "unexpected_field": 400,
    "unknown_mode": 400,
    "no_battery": 409,
    "duration_out_of_range": 400,
    "target_soc_out_of_range": 400,
    "power_out_of_range": 400,
    "floor_out_of_range": 400,
    "export_limit_not_writable": 409,
    "export_limit_out_of_range": 400,
    "nothing_to_do": 400,
    # A command the dongle will not run: no right to control, or not the
    # inverter model chosen; or a mode the inverter's control cannot do.
    "control_not_allowed": 409,
    "mode_not_supported": 409,
}


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_command(command: object, context: dict) -> str | None:
    """None when the command may run, else one of COMMAND_ERRORS.

    `context` is the dongle's own view, never the client's:
        scope                  "read" | "control"   (the connection's)
        paired                 bool                 (the dongle has an owner)
        control_mode           "auto" | "manual"    (from the last plan)
        has_battery            bool
        min_soc, max_soc       int                  (the owner's limits)
        rated_w                int | None           (the inverter's own rating)
        export_limit_writable  bool                 (GridZeno may set the export rate here: 123 or 30201, never 122/30200)
        control_allowed        bool, default True   (the write right and the model chosen)
        can_charge, can_hold, can_sell  bool, default True  (what the inverter's control can do)

    The order is the order a client should learn things in: who may ask
    before what is asked, and the mode before its numbers. A command
    outside the owner's limits is refused, never quietly clamped: Home
    Assistant must know that what it asked for is not what happens.
    """
    if context.get("scope") != "control":
        return "scope"
    if not context.get("paired"):
        return "not_paired"
    if context.get("control_mode") != "manual":
        return "auto_mode"
    if not context.get("control_allowed", True):
        # The dongle would not run it: accepted and dropped would leave Home
        # Assistant believing it asked for something that is happening.
        return "control_not_allowed"
    if not isinstance(command, dict) or not set(command) <= COMMAND_FIELDS:
        return "unexpected_field"
    mode = command.get("mode")
    if mode not in MODES:
        return "unknown_mode"

    min_soc = int(context.get("min_soc", 10))
    max_soc = int(context.get("max_soc", 100))
    limit = command.get("export_limit_percent")
    has_limit = "export_limit_percent" in command

    allowed = {"mode", "duration_min", "export_limit_percent"}
    if mode == "charge_grid":
        allowed |= {"target_soc"}
    elif mode == "sell":
        allowed |= {"power_w", "floor_soc"}
    if not set(command) <= allowed:
        return "unexpected_field"

    if mode == "inverter" and not has_limit:
        # The inverter's own settings with nothing else: that is a release,
        # DELETE /api/v1/command, not a command with a duration.
        return "nothing_to_do"

    if mode != "inverter" and not context.get("has_battery"):
        return "no_battery"

    # A mode the inverter's control cannot carry out (a Growatt family without
    # battery control; a battery that is only read).
    able = {"charge_grid": "can_charge", "hold": "can_hold", "sell": "can_sell"}.get(mode)
    if able is not None and not context.get(able, True):
        return "mode_not_supported"

    duration = command.get("duration_min")
    if not _is_int(duration) or not MIN_DURATION_MIN <= duration <= MAX_DURATION_MIN:
        return "duration_out_of_range"

    if mode == "charge_grid":
        target = command.get("target_soc")
        if not _is_int(target) or not min_soc < target <= max_soc:
            return "target_soc_out_of_range"
    elif mode == "sell":
        power = command.get("power_w")
        rated = context.get("rated_w")
        cap = rated if _is_int(rated) and rated > 0 else MAX_SELL_W_UNKNOWN_RATING
        if not _is_int(power) or not MIN_SELL_W <= power <= cap:
            return "power_out_of_range"
        floor = command.get("floor_soc", min_soc)
        if not _is_int(floor) or not min_soc <= floor < max_soc:
            return "floor_out_of_range"

    if has_limit:
        if not context.get("export_limit_writable"):
            return "export_limit_not_writable"
        if not _is_int(limit) or not 0 <= limit <= 100:
            return "export_limit_out_of_range"
    return None


def command_block(command: dict, start_epoch: int, min_soc: int) -> dict:
    """The one-block plan a valid command runs as (the same shape as a block
    of GridZeno's own plan), so the dongle runs it through exactly the code
    and the guards a block from GridZeno takes.

    "inverter" with an export limit is the inverter's own battery behaviour
    (Load First) with only the export limit set, which is what the plan's
    discharge_load blocks are.
    """
    mode = command["mode"]
    has_limit = "export_limit_percent" in command
    block = {
        "start_epoch": start_epoch,
        "end_epoch": start_epoch + 60 * command["duration_min"],
        "action": {"inverter": "discharge_load", "charge_grid": "charge_grid", "hold": "hold", "sell": "discharge_export"}[mode],
        "target_export_limit_enabled": has_limit,
        "target_export_limit_percent": command["export_limit_percent"] if has_limit else 100,
        "battery_power_w": None,
        "target_soc_percent": None,
        "sell_floor_percent": None,
    }
    if mode == "charge_grid":
        block["target_soc_percent"] = command["target_soc"]
    elif mode == "sell":
        block["battery_power_w"] = -command["power_w"]
        block["sell_floor_percent"] = command.get("floor_soc", min_soc)
    return block
