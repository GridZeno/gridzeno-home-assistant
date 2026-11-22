"""Tests of signals.py: "Cheap hour now", "Surplus now" and "Negative price
now". signals.py has no Home Assistant imports, so it is loaded on its own:
python -m pytest tests"""

import importlib.util
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "gridzeno"
_spec = importlib.util.spec_from_file_location("gridzeno_signals", COMPONENT / "signals.py")
signals = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = signals
_spec.loader.exec_module(signals)

CPH = ZoneInfo("Europe/Copenhagen")
UTC = timezone.utc


def quarters_for(day_local: datetime, hourly: list[float | None], field: str = "buy", spot: float | None = None) -> list[dict]:
    """Four quarters per local hour, from local midnight, with the hour's price."""
    out = []
    start = day_local.replace(hour=0, minute=0, second=0, microsecond=0)
    for h, price in enumerate(hourly):
        for m in range(4):
            t = (start + timedelta(hours=h, minutes=15 * m)).astimezone(UTC)
            q = {"start": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "spot": spot, "buy": None, "sell": None, "pv_w": 0}
            q[field] = price
            out.append(q)
    return out


DAY = datetime(2026, 11, 22, tzinfo=CPH)
# 24 hours: cheapest are 03 (0.5), 04 (0.6), 13 (0.7), 02 (0.8); 14 has 0.8 too, later.
PRICES = [2.0, 1.5, 0.8, 0.5, 0.6, 1.0, 2.5, 3.0, 3.0, 2.2, 2.0, 1.8, 1.1, 0.7, 0.8, 1.9,
          2.6, 3.5, 3.9, 3.2, 2.4, 2.1, 1.7, 1.6]


def at(hour: int, minute: int = 0, day: datetime = DAY) -> datetime:
    return day.replace(hour=hour, minute=minute).astimezone(UTC)


# --- Cheap hour now -------------------------------------------------------------------


def test_the_n_cheapest_hours_of_the_day():
    q = quarters_for(DAY, PRICES)
    hours = signals.cheap_hours(q, 4, CPH)
    assert [h.start.astimezone(CPH).hour for h in hours] == [2, 3, 4, 13]
    assert signals.cheap_now(q, 4, at(3, 40), CPH) is True
    assert signals.cheap_now(q, 4, at(13, 0), CPH) is True
    assert signals.cheap_now(q, 4, at(14, 0), CPH) is False  # same price as 02, but later
    assert signals.cheap_now(q, 4, at(18, 0), CPH) is False
    assert [h.start.astimezone(CPH).hour for h in signals.cheap_hours(q, 1, CPH)] == [3]


def test_the_default_is_four_hours():
    assert signals.DEFAULT_CHEAP_HOURS == 4
    assert signals.DEFAULT_SURPLUS_W == 1000


def test_an_hours_price_is_the_average_of_its_quarters():
    q = quarters_for(DAY, [1.0] * 24)
    # Hour 10: one dear quarter makes it dearer than hour 11's even quarters.
    for item in q:
        start = datetime.fromisoformat(item["start"].replace("Z", "+00:00")).astimezone(CPH)
        if start.hour == 10:
            item["buy"] = 0.1 if start.minute else 2.0  # average 0.575
        if start.hour == 11:
            item["buy"] = 0.5
    assert [h.start.astimezone(CPH).hour for h in signals.cheap_hours(q, 2, CPH)] == [10, 11]
    assert [h.start.astimezone(CPH).hour for h in signals.cheap_hours(q, 1, CPH)] == [11]


def test_spot_when_the_day_has_no_buy_price():
    q = quarters_for(DAY, PRICES, field="spot")
    assert all(item["buy"] is None for item in q)
    assert [h.start.astimezone(CPH).hour for h in signals.cheap_hours(q, 4, CPH)] == [2, 3, 4, 13]


def test_buy_wins_over_spot():
    q = quarters_for(DAY, PRICES)
    for item in q:
        item["spot"] = 0.0 if item["start"].endswith("T22:00:00Z") else 9.0  # spot would pick local 23
    assert [h.start.astimezone(CPH).hour for h in signals.cheap_hours(q, 1, CPH)] == [3]


def test_each_day_gets_its_own_n_hours():
    tomorrow = DAY + timedelta(days=1)
    q = quarters_for(DAY, PRICES) + quarters_for(tomorrow, [5.0] * 20 + [0.1, 0.2, 0.3, 0.4])
    hours = signals.cheap_hours(q, 4, CPH)
    assert len(hours) == 8
    # Tomorrow's cheap evening does not take today's cheap night.
    assert signals.cheap_now(q, 4, at(3), CPH) is True
    assert signals.cheap_now(q, 4, at(21, day=tomorrow), CPH) is True


def test_only_the_known_window_when_the_day_is_not_whole():
    # The dongle has prices from 14:00 only: the 4 cheapest of what is known.
    q = [item for item in quarters_for(DAY, PRICES)
         if datetime.fromisoformat(item["start"].replace("Z", "+00:00")).astimezone(CPH).hour >= 14]
    assert [h.start.astimezone(CPH).hour for h in signals.cheap_hours(q, 4, CPH)] == [14, 15, 22, 23]


def test_unknown_without_prices_for_this_hour():
    assert signals.cheap_now([], 4, at(3), CPH) is None
    q = quarters_for(DAY, PRICES)
    assert signals.cheap_now(q, 4, at(3, day=DAY + timedelta(days=2)), CPH) is None


def test_a_dst_day_has_23_hours():
    spring = datetime(2026, 3, 29, tzinfo=CPH)  # 02:00 local does not exist
    q = []
    t = spring.replace(hour=0).astimezone(UTC)
    end = (spring + timedelta(days=1)).replace(hour=0).astimezone(UTC)
    i = 0
    while t < end:
        q.append({"start": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "buy": 1.0 + (i // 4) * 0.01, "spot": None})
        t += timedelta(minutes=15)
        i += 1
    days = signals.hour_prices(q, CPH)
    assert len(days[spring.date()]) == 23


def test_next_cheap_window_joins_following_hours():
    q = quarters_for(DAY, PRICES)
    start, end = signals.next_cheap_window(q, 4, at(0, 30), CPH)
    assert (start.astimezone(CPH).hour, end.astimezone(CPH).hour) == (2, 5)
    # While a cheap hour runs, its window.
    start, end = signals.next_cheap_window(q, 4, at(4, 10), CPH)
    assert (start.astimezone(CPH).hour, end.astimezone(CPH).hour) == (2, 5)
    start, end = signals.next_cheap_window(q, 4, at(5, 0), CPH)
    assert (start.astimezone(CPH).hour, end.astimezone(CPH).hour) == (13, 14)
    assert signals.next_cheap_window(q, 4, at(14, 0), CPH) is None


# --- Negative price now ---------------------------------------------------------------


def test_negative_price_is_the_buy_price_below_zero():
    assert signals.negative_now({"buy": -0.01, "spot": 0.5}) is True
    assert signals.negative_now({"buy": 0.0, "spot": -0.5}) is False
    assert signals.negative_now({"buy": None, "spot": -0.5}) is None
    assert signals.negative_now(None) is None


# --- Surplus now ----------------------------------------------------------------------


def state(n: int, export: float | None = 0, imported: float | None = 0, battery: float | None = 0,
          soc: float | None = 100, has_battery: bool | None = True, max_soc: float = 100) -> dict:
    return {
        "measured_at": f"2026-11-22T12:{n // 12:02d}:{(n % 12) * 5:02d}Z",
        "power_w": {"grid_export": export, "grid_import": imported, "battery": battery},
        "battery_soc": soc,
        "control": {"has_battery": has_battery, "limits": {"min_soc": 10, "max_soc": max_soc}},
    }


T0 = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def feed(tracker, readings, threshold=1000, start=0):
    out = []
    for i, s in enumerate(readings):
        out.append(tracker.update(s, threshold, T0 + timedelta(seconds=5 * (start + i))))
    return out


def test_surplus_needs_two_readings_in_a_row_over_the_threshold_and_a_full_battery():
    t = signals.SurplusTracker()
    assert feed(t, [state(0, export=1500), state(1, export=900), state(2, export=1500), state(3, export=1600)]) == [
        None, None, None, True]


def test_the_same_reading_twice_counts_once():
    t = signals.SurplusTracker()
    assert feed(t, [state(0, export=1500), state(0, export=1500), state(0, export=1500)]) == [None, None, None]
    assert feed(t, [state(1, export=1500)], start=3) == [True]


def test_not_while_the_battery_charges_below_its_ceiling():
    t = signals.SurplusTracker()
    assert feed(t, [state(i, export=3000, battery=2000, soc=90) for i in range(4)]) == [None, False, False, False]
    # Ceiling is the owner's max in GridZeno, with a small margin (a BMS that stops at 99).
    t = signals.SurplusTracker()
    assert feed(t, [state(i, export=3000, soc=79, max_soc=80) for i in range(2)]) == [None, True]


def test_no_battery():
    t = signals.SurplusTracker()
    assert feed(t, [state(i, export=1200, battery=None, soc=None, has_battery=False) for i in range(2)]) == [None, True]


def test_hysteresis_a_consumer_eating_the_surplus_keeps_it_on():
    t = signals.SurplusTracker()
    feed(t, [state(0, export=2000), state(1, export=2000)])
    assert t.on
    # The water heater takes 1800 W: little is sold, but nothing is bought.
    later = 70  # past the 5 minutes
    assert feed(t, [state(later + i, export=200) for i in range(3)], start=later) == [True] * 3


def test_off_after_two_readings_buying_and_at_least_five_minutes():
    t = signals.SurplusTracker()
    feed(t, [state(0, export=2000), state(1, export=2000)])
    # Buying at once: held for 5 minutes.
    assert feed(t, [state(2 + i, imported=1500) for i in range(3)], start=2) == [True] * 3
    # After 5 minutes, two readings in a row: a reading in between that
    # neither buys nor sells much starts the count again.
    later = 70
    assert feed(t, [state(later, export=50), state(later + 1, imported=1500), state(later + 2, export=50),
                    state(later + 3, imported=1500), state(later + 4, imported=1500)],
                start=later) == [True, True, True, True, False]


def test_off_when_the_battery_discharges_or_falls_below_its_ceiling():
    for drop in ({"battery": -800}, {"soc": 94}):
        t = signals.SurplusTracker()
        feed(t, [state(0, export=2000), state(1, export=2000)])
        later = 70
        assert feed(t, [state(later + i, export=1500, **drop) for i in range(2)], start=later) == [True, False]


def test_starts_in_the_saved_state_after_a_restart():
    # On before the restart: still on, and held 5 minutes, though the house buys.
    t = signals.SurplusTracker()
    t.restore(True, T0)
    assert feed(t, [state(i, imported=1500) for i in range(3)]) == [True, True, True]
    later = 70
    assert feed(t, [state(later, export=50)] + [state(later + 1 + i, imported=1500) for i in range(2)],
                start=later) == [True, True, False]
    # On, and the reading after the restart is in between: it stays on.
    t = signals.SurplusTracker()
    t.restore(True, T0)
    assert feed(t, [state(i, export=200) for i in range(3)]) == [True, True, True]
    # Off before the restart: off at once, not unknown.
    t = signals.SurplusTracker()
    t.restore(False, T0)
    assert feed(t, [state(0, export=200)]) == [False]


def test_nothing_saved_is_unknown_until_two_readings_agree():
    # Never a false "off" first: two readings that disagree leave it unknown.
    t = signals.SurplusTracker()
    assert feed(t, [state(0, export=1500), state(1, imported=500), state(2, export=1500),
                    state(3, export=1500)]) == [None, None, None, True]
    t = signals.SurplusTracker()
    assert feed(t, [state(0, export=200), state(1, export=200)]) == [None, False]


def test_unknown_without_a_grid_meter_or_the_battery_level():
    t = signals.SurplusTracker()
    assert feed(t, [state(0, export=None, imported=None)]) == [None]
    assert feed(t, [state(1, export=1500, soc=None)]) == [None]


# --- The texts ------------------------------------------------------------------------


def test_every_signal_and_option_has_its_english_name():
    for name in ("strings.json", "translations/en.json"):
        texts = json.loads((COMPONENT / name).read_text(encoding="utf-8"))
        for key in ("cheap_hour_now", "surplus_now", "negative_price_now"):
            assert texts["entity"]["binary_sensor"][key]["name"]
        assert texts["entity"]["sensor"]["next_cheap_hours"]["name"]
        data = texts["options"]["step"]["init"]["data"]
        assert set(data) == {signals.OPTION_CHEAP_HOURS, signals.OPTION_SURPLUS_W}
    assert (COMPONENT / "strings.json").read_text(encoding="utf-8") == (
        COMPONENT / "translations" / "en.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("key", ["cheap_hour_now", "surplus_now", "negative_price_now"])
def test_the_readme_names_the_signals(key):
    texts = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    assert texts["entity"]["binary_sensor"][key]["name"] in (ROOT / "README.md").read_text(encoding="utf-8")
