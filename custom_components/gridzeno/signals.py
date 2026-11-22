"""Signals for the owner's own automations: a cheap hour, a surplus and a
negative price, worked out here from what the dongle already sends.

Plain Python with no Home Assistant imports, so the tests can load it on
its own. Times are aware datetimes; the caller gives "now" and the local
time zone, so nothing here reads the clock.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any

HOUR = timedelta(hours=1)

# The options and their defaults (the integration's options).
OPTION_CHEAP_HOURS = "cheap_hours"
OPTION_SURPLUS_W = "surplus_w"
DEFAULT_CHEAP_HOURS = 4
DEFAULT_SURPLUS_W = 1000
CHEAP_HOURS_RANGE = (1, 12)
SURPLUS_W_RANGE = (100, 30000)

# "Surplus now": the battery counts as full this close to its ceiling (a BMS
# often stops at 99 %), and as no longer full this far below it.
FULL_MARGIN = 2
EMPTYING_MARGIN = 5
# Buying or discharging more than this is not a surplus any more; a few
# watts either way is the meters' noise.
NOISE_W = 100
# A change needs this many new readings in a row that agree, and the signal
# stays as it is at least this long, so a relay is not switched on and off.
READINGS_IN_A_ROW = 2
MIN_STATE_TIME = timedelta(minutes=5)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


# --- Cheap hours ------------------------------------------------------------------


@dataclass(frozen=True)
class Hour:
    start: datetime  # UTC, on the hour
    price: float


def hour_prices(quarters: list[dict], local: tzinfo) -> dict[Any, list[Hour]]:
    """The hours each local day has prices for, with the hour's average
    price. The buy price where the day has one, else the spot price (an owner
    who has not chosen a grid company has only spot)."""
    by_day: dict[Any, list[tuple[datetime, dict]]] = {}
    for q in quarters or []:
        start = _parse(q.get("start"))
        if start is None:
            continue
        start = start.astimezone(timezone.utc)
        by_day.setdefault(start.astimezone(local).date(), []).append((start, q))
    out: dict[Any, list[Hour]] = {}
    for day, items in by_day.items():
        field = "buy" if any(isinstance(q.get("buy"), (int, float)) for _, q in items) else "spot"
        sums: dict[datetime, list[float]] = {}
        for start, q in items:
            price = q.get(field)
            if isinstance(price, (int, float)):
                sums.setdefault(start.replace(minute=0, second=0, microsecond=0), []).append(float(price))
        hours = [Hour(h, sum(p) / len(p)) for h, p in sorted(sums.items())]
        if hours:
            out[day] = hours
    return out


def cheap_hours(quarters: list[dict], n: int, local: tzinfo) -> list[Hour]:
    """The N cheapest hours of each day with prices, in time order. Equal
    prices: the earlier hour first."""
    chosen: list[Hour] = []
    for hours in hour_prices(quarters, local).values():
        ranked = sorted(hours, key=lambda h: (h.price, h.start))
        chosen.extend(ranked[: max(n, 0)])
    return sorted(chosen, key=lambda h: h.start)


def cheap_now(quarters: list[dict], n: int, now: datetime, local: tzinfo) -> bool | None:
    """On in one of the day's N cheapest hours; None when there are no
    prices for this hour."""
    hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    today = hour_prices(quarters, local).get(now.astimezone(local).date(), [])
    if not any(h.start == hour for h in today):
        return None
    return any(h.start == hour for h in cheap_hours(quarters, n, local))


def next_cheap_window(quarters: list[dict], n: int, now: datetime, local: tzinfo) -> tuple[datetime, datetime] | None:
    """The cheap hours that run now or come next, joined where they follow
    each other: (start, end). None when no cheap hour is left in the known
    prices."""
    windows: list[list[datetime]] = []
    for h in cheap_hours(quarters, n, local):
        if windows and windows[-1][1] == h.start:
            windows[-1][1] = h.start + HOUR
        else:
            windows.append([h.start, h.start + HOUR])
    for start, end in windows:
        if end > now:
            return start, end
    return None


# --- Negative price ----------------------------------------------------------------


def negative_now(quarter: dict | None) -> bool | None:
    """On when the buy price now is below zero; None without a buy price."""
    buy = (quarter or {}).get("buy")
    if not isinstance(buy, (int, float)):
        return None
    return buy < 0


# --- Surplus -----------------------------------------------------------------------


class SurplusTracker:
    """ "Surplus now", with hysteresis.

    On after two new readings in a row that sell more than the threshold
    while the battery is at its ceiling (within FULL_MARGIN), or the plant
    has no battery. Off again after two readings in a row where the house
    buys from the grid, or the battery discharges, or the battery has fallen
    EMPTYING_MARGIN points below its ceiling. Between the two it stays as it
    is: a water heater that eats the surplus does not switch itself off as
    long as the sun and a full battery cover it. Either way it holds at least
    MIN_STATE_TIME.

    After a restart or a reload it starts in the state Home Assistant saved
    (restore), held as if it had just changed. With nothing saved it is
    unknown (None) until two readings in a row agree, so it never writes an
    "off" it does not know; a reading between the two conditions counts as
    off then.
    """

    def __init__(self) -> None:
        self.on: bool | None = None
        self._changed_at: datetime | None = None
        self._last_reading: str | None = None
        self._pending: bool | None = None
        self._streak = 0

    def restore(self, on: bool, now: datetime) -> None:
        """Start in the saved state, as if it had changed now."""
        self.on, self._changed_at, self._pending, self._streak = on, now, None, 0

    def update(self, state: dict, threshold_w: float, now: datetime) -> bool | None:
        """Feed the dongle's /state; returns the signal, or None when the
        reading cannot tell (no grid meter, the battery's level unknown) or
        it is not known yet."""
        reading = state.get("measured_at")
        verdict = self._verdict(state, threshold_w)
        if verdict is None:
            self._streak = 0
            return None
        if reading == self._last_reading:
            return self.on  # the same reading again: nothing new to count
        self._last_reading = reading
        if verdict == "keep":
            wants = bool(self.on)
        else:
            wants = verdict == "on"
        if wants == self.on:
            self._streak = 0
            return self.on
        if wants != self._pending:
            self._pending, self._streak = wants, 0
        self._streak += 1
        held = self._changed_at is None or now - self._changed_at >= MIN_STATE_TIME
        if self._streak >= READINGS_IN_A_ROW and held:
            self.on, self._changed_at, self._pending, self._streak = wants, now, None, 0
        return self.on

    @staticmethod
    def _verdict(state: dict, threshold_w: float) -> str | None:
        power = state.get("power_w") or {}
        export, imported = power.get("grid_export"), power.get("grid_import")
        if not isinstance(export, (int, float)) or not isinstance(imported, (int, float)):
            return None
        control = state.get("control") or {}
        has_battery = control.get("has_battery")
        ceiling = (control.get("limits") or {}).get("max_soc")
        if not isinstance(ceiling, (int, float)):
            ceiling = 100
        if has_battery is False:
            full = emptying = discharging = False
        else:
            soc, battery = state.get("battery_soc"), power.get("battery")
            if not isinstance(soc, (int, float)):
                return None
            full = soc >= ceiling - FULL_MARGIN
            emptying = soc < ceiling - EMPTYING_MARGIN
            discharging = isinstance(battery, (int, float)) and battery < -NOISE_W
        if imported > NOISE_W or discharging or emptying:
            return "off"
        if export > threshold_w and (full or has_battery is False):
            return "on"
        return "keep"
