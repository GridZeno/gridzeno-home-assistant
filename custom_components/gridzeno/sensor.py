"""Sensors: power, energy, the battery, prices, the solar forecast and the plan."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from .const import STALE_READING
from .coordinator import GridZenoConfigEntry, GridZenoCoordinator, GridZenoData
from .entity import GridZenoEntity

PRICE_UNIT = "DKK/kWh"
QUARTER = timedelta(minutes=15)
ACTIONS = [
    "charge_solar", "charge_grid", "hold", "export", "discharge_load", "discharge_export", "import", "idle",
]


def _ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    return dt_util.parse_datetime(value)


def _fresh(data: GridZenoData) -> bool:
    measured = _ts(data.state.get("measured_at"))
    return measured is not None and dt_util.utcnow() - measured <= STALE_READING


def _power(key: str) -> Callable[[GridZenoData], Any]:
    return lambda d: (d.state.get("power_w") or {}).get(key)


def _battery_part(sign: int) -> Callable[[GridZenoData], Any]:
    def value(d: GridZenoData) -> Any:
        battery = (d.state.get("power_w") or {}).get("battery")
        return None if battery is None else max(sign * battery, 0)

    return value


def _quarter_now(d: GridZenoData) -> dict | None:
    now = dt_util.utcnow()
    for q in d.outlook.get("quarters") or []:
        start = _ts(q.get("start"))
        if start is not None and start <= now < start + QUARTER:
            return q
    return None


def _today_quarters(d: GridZenoData, day_offset: int = 0) -> list[dict]:
    target = dt_util.now().date().toordinal() + day_offset
    out = []
    for q in d.outlook.get("quarters") or []:
        start = _ts(q.get("start"))
        if start is not None and dt_util.as_local(start).date().toordinal() == target:
            out.append(q)
    return out


def _extreme_quarter(d: GridZenoData, field: str, pick: Callable, day: int) -> dict | None:
    quarters = [q for q in _today_quarters(d, day) if q.get(field) is not None]
    if not quarters:
        return None
    # The first of equal prices: the earliest quarter that is as cheap (or dear).
    return pick(quarters, key=lambda q: q[field])


def _extreme(field: str, pick: Callable, day: int = 0) -> Callable[[GridZenoData], Any]:
    def value(d: GridZenoData) -> Any:
        quarter = _extreme_quarter(d, field, pick, day)
        return _ts(quarter["start"]) if quarter else None

    return value


def _extreme_attrs(field: str, pick: Callable, day: int = 0) -> Callable[[GridZenoData], dict]:
    def attrs(d: GridZenoData) -> dict:
        quarter = _extreme_quarter(d, field, pick, day)
        return {"price": quarter[field] if quarter else None}

    return attrs


def _pv_day(offset: int) -> Callable[[GridZenoData], Any]:
    def value(d: GridZenoData) -> Any:
        target = (dt_util.now().date().toordinal() + offset)
        for day in d.outlook.get("days") or []:
            try:
                if date.fromisoformat(day["date"]).toordinal() == target:
                    return day.get("pv_kwh")
            except (KeyError, ValueError):
                continue
        return None

    return value


def _pv_rest_of_today(d: GridZenoData) -> Any:
    now = dt_util.utcnow()
    quarters = [q for q in _today_quarters(d) if q.get("pv_w") is not None]
    if not quarters:
        return None
    total = 0.0
    for q in quarters:
        start = _ts(q["start"])
        if start is not None and start + QUARTER > now:
            total += q["pv_w"] * 0.25 / 1000
    return round(total, 2)


def _plan_block(which: str, field: str) -> Callable[[GridZenoData], Any]:
    def value(d: GridZenoData) -> Any:
        block = (d.state.get("plan") or {}).get(which) or {}
        raw = block.get(field)
        if field in ("start", "end"):
            return _ts(raw)
        if field == "action":
            return raw if raw in ACTIONS else None
        return raw

    return value


@dataclass(frozen=True, kw_only=True)
class GridZenoSensorDescription(SensorEntityDescription):
    value_fn: Callable[[GridZenoData], Any]
    # A measurement that is unavailable once the reading is old.
    live: bool = False
    # Needs the prices or the forecast (Smart and Pro).
    outlook: bool = False
    attrs_fn: Callable[[GridZenoData], dict] | None = None


def _w(key: str, name_key: str, **kw) -> GridZenoSensorDescription:
    return GridZenoSensorDescription(
        key=key, translation_key=name_key, native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER, state_class=SensorStateClass.MEASUREMENT, live=True, **kw,
    )


def _kwh_total(key: str) -> GridZenoSensorDescription:
    return GridZenoSensorDescription(
        key=f"energy_{key}", translation_key=f"energy_{key}", native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY, state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda d, k=key: (d.state.get("energy_kwh") or {}).get(k),
    )


def _forecast_attrs(d: GridZenoData) -> dict:
    return {
        "prices": [
            {"start": q.get("start"), "buy": q.get("buy"), "sell": q.get("sell"), "spot": q.get("spot")}
            for q in d.outlook.get("quarters") or []
            if q.get("spot") is not None
        ]
    }


SENSORS: tuple[GridZenoSensorDescription, ...] = (
    _w("pv_power", "pv_power", value_fn=_power("pv")),
    _w("grid_import_power", "grid_import_power", value_fn=_power("grid_import")),
    _w("grid_export_power", "grid_export_power", value_fn=_power("grid_export")),
    _w("load_power", "load_power", value_fn=_power("load")),
    _w("battery_power", "battery_power", value_fn=_power("battery")),
    _w("battery_charge_power", "battery_charge_power", value_fn=_battery_part(1)),
    _w("battery_discharge_power", "battery_discharge_power", value_fn=_battery_part(-1)),
    _w("load_register_power", "load_register_power", value_fn=_power("load_register"),
       entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False),
    GridZenoSensorDescription(
        key="battery_soc", translation_key="battery_soc", native_unit_of_measurement=PERCENTAGE,
        device_class=SensorDeviceClass.BATTERY, state_class=SensorStateClass.MEASUREMENT, live=True,
        value_fn=lambda d: d.state.get("battery_soc"),
    ),
    *(
        GridZenoSensorDescription(
            key=f"temperature_{part}", translation_key=f"temperature_{part}",
            native_unit_of_measurement=UnitOfTemperature.CELSIUS, device_class=SensorDeviceClass.TEMPERATURE,
            state_class=SensorStateClass.MEASUREMENT, entity_category=EntityCategory.DIAGNOSTIC, live=True,
            value_fn=lambda d, part=part: (d.state.get("temperatures_c") or {}).get(part),
        )
        for part in ("inverter", "ipm", "boost", "battery")
    ),
    *(_kwh_total(k) for k in ("pv", "grid_import", "grid_export", "battery_charge", "battery_discharge", "load")),
    GridZenoSensorDescription(
        key="buy_price", translation_key="buy_price", native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2, outlook=True,
        value_fn=lambda d: (_quarter_now(d) or {}).get("buy"), attrs_fn=_forecast_attrs,
    ),
    GridZenoSensorDescription(
        key="sell_price", translation_key="sell_price", native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2, outlook=True,
        value_fn=lambda d: (_quarter_now(d) or {}).get("sell"),
    ),
    GridZenoSensorDescription(
        key="spot_price", translation_key="spot_price", native_unit_of_measurement=PRICE_UNIT,
        state_class=SensorStateClass.MEASUREMENT, suggested_display_precision=2, outlook=True,
        value_fn=lambda d: (_quarter_now(d) or {}).get("spot"),
    ),
    *(
        GridZenoSensorDescription(
            key=f"{name}_{when}", translation_key=f"{name}_{when}", device_class=SensorDeviceClass.TIMESTAMP,
            outlook=True, value_fn=_extreme("buy", pick, day), attrs_fn=_extreme_attrs("buy", pick, day),
        )
        for when, day in (("today", 0), ("tomorrow", 1))
        for name, pick in (("cheapest", min), ("dearest", max))
    ),
    GridZenoSensorDescription(
        key="solar_forecast_today", translation_key="solar_forecast_today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY,
        outlook=True, value_fn=_pv_day(0),
    ),
    GridZenoSensorDescription(
        key="solar_forecast_tomorrow", translation_key="solar_forecast_tomorrow",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY,
        outlook=True, value_fn=_pv_day(1),
    ),
    GridZenoSensorDescription(
        key="solar_forecast_rest_of_today", translation_key="solar_forecast_rest_of_today",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, device_class=SensorDeviceClass.ENERGY,
        outlook=True, value_fn=_pv_rest_of_today,
    ),
    GridZenoSensorDescription(
        key="plan_now", translation_key="plan_now", device_class=SensorDeviceClass.ENUM, options=ACTIONS,
        value_fn=_plan_block("now", "action"),
    ),
    GridZenoSensorDescription(
        key="plan_now_until", translation_key="plan_now_until", device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_plan_block("now", "end"),
    ),
    GridZenoSensorDescription(
        key="plan_next", translation_key="plan_next", device_class=SensorDeviceClass.ENUM, options=ACTIONS,
        value_fn=_plan_block("next", "action"),
    ),
    GridZenoSensorDescription(
        key="plan_next_start", translation_key="plan_next_start", device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_plan_block("next", "start"),
    ),
    GridZenoSensorDescription(
        key="control_mode", translation_key="control_mode", device_class=SensorDeviceClass.ENUM,
        options=["auto", "manual"], value_fn=lambda d: (d.state.get("control") or {}).get("mode"),
    ),
    GridZenoSensorDescription(
        key="writer", translation_key="writer", device_class=SensorDeviceClass.ENUM,
        options=["gridzeno", "home_assistant", "none"],
        value_fn=lambda d: (d.state.get("control") or {}).get("writer"),
        attrs_fn=lambda d: {"command": (d.state.get("control") or {}).get("local_command")},
    ),
    GridZenoSensorDescription(
        key="measured_at", translation_key="measured_at", device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC, value_fn=lambda d: _ts(d.state.get("measured_at")),
    ),
    GridZenoSensorDescription(
        key="last_cloud_contact", translation_key="last_cloud_contact", device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _ts((d.state.get("health") or {}).get("last_cloud_contact")),
    ),
    GridZenoSensorDescription(
        key="wifi_rssi", translation_key="wifi_rssi", native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        device_class=SensorDeviceClass.SIGNAL_STRENGTH, state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC, entity_registry_enabled_default=False,
        value_fn=lambda d: (d.state.get("health") or {}).get("wifi_rssi"),
    ),
    GridZenoSensorDescription(
        key="uptime", translation_key="uptime", native_unit_of_measurement=UnitOfTime.SECONDS,
        device_class=SensorDeviceClass.DURATION, entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False, value_fn=lambda d: (d.state.get("health") or {}).get("uptime_s"),
    ),
    GridZenoSensorDescription(
        key="command_check", translation_key="command_check", device_class=SensorDeviceClass.ENUM,
        options=["pending", "confirmed", "unconfirmed", "at_limit", "ended"],
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: (d.state.get("health") or {}).get("command_check"),
    ),
)

TODAY = ("pv", "grid_import", "grid_export", "battery_charge", "battery_discharge", "load")


async def async_setup_entry(
    hass: HomeAssistant, entry: GridZenoConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [GridZenoSensor(coordinator, d) for d in SENSORS]
    strings = (coordinator.data.state.get("power_w") or {}).get("mppt") or []
    entities += [GridZenoStringSensor(coordinator, i) for i in range(len(strings))]
    entities += [GridZenoTodaySensor(coordinator, k) for k in TODAY]
    async_add_entities(entities)


class GridZenoSensor(GridZenoEntity, SensorEntity):
    entity_description: GridZenoSensorDescription

    def __init__(self, coordinator: GridZenoCoordinator, description: GridZenoSensorDescription) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        if not super().available or self.coordinator.data is None:
            return False
        d = self.coordinator.data
        if self.entity_description.live and not _fresh(d):
            return False
        if self.entity_description.outlook and not d.outlook.get("available"):
            return False
        return True

    @property
    def native_value(self) -> Any:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict | None:
        if self.entity_description.attrs_fn is None or self.coordinator.data is None:
            return None
        return self.entity_description.attrs_fn(self.coordinator.data)


class GridZenoStringSensor(GridZenoEntity, SensorEntity):
    """One string of panels (MPPT)."""

    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_translation_key = "string_power"

    def __init__(self, coordinator: GridZenoCoordinator, index: int) -> None:
        super().__init__(coordinator, f"string_{index + 1}_power")
        self._index = index
        self._attr_translation_placeholders = {"number": str(index + 1)}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None and _fresh(self.coordinator.data)

    @property
    def native_value(self) -> Any:
        strings = (self.coordinator.data.state.get("power_w") or {}).get("mppt") or []
        return strings[self._index] if self._index < len(strings) else None


@dataclass
class _TodayStart(ExtraStoredData):
    """The day and the counter at its start, kept over a restart."""

    day: str | None
    start: float | None

    def as_dict(self) -> dict[str, Any]:
        return {"day": self.day, "start": self.start}


class GridZenoTodaySensor(GridZenoEntity, RestoreEntity, SensorEntity):
    """Today's energy, local time, from the dongle's own counters: the
    counter now minus the counter at the first reading after midnight. The
    day's start survives a restart of Home Assistant. Goes to 0 at midnight,
    which a total_increasing sensor is allowed to do."""

    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: GridZenoCoordinator, field: str) -> None:
        super().__init__(coordinator, f"today_{field}")
        self._field = field
        self._attr_translation_key = f"today_{field}"
        self._day: date | None = None
        self._start: float | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_extra_data()
        if last is not None:
            saved = last.as_dict()
            try:
                self._day = date.fromisoformat(saved["day"])
                self._start = float(saved["start"])
            except (KeyError, TypeError, ValueError):
                self._day, self._start = None, None
        self._roll()

    @property
    def extra_restore_state_data(self) -> _TodayStart:
        return _TodayStart(self._day.isoformat() if self._day else None, self._start)

    def _total(self) -> float | None:
        if self.coordinator.data is None:
            return None
        value = (self.coordinator.data.state.get("energy_kwh") or {}).get(self._field)
        return float(value) if isinstance(value, (int, float)) else None

    def _roll(self) -> None:
        total = self._total()
        if total is None:
            return
        today = dt_util.now().date()
        # A new day, or counters that went down (a new dongle): start again.
        if self._day != today or self._start is None or total < self._start:
            self._day, self._start = today, total

    @callback
    def _handle_coordinator_update(self) -> None:
        self._roll()
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float | None:
        total = self._total()
        if total is None or self._start is None:
            return None
        return round(total - self._start, 3)
