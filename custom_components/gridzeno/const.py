"""Constants for the GridZeno integration."""

from datetime import timedelta

DOMAIN = "gridzeno"
MANUFACTURER = "GridZeno"

CONF_DEVICE_ID = "device_id"
CONF_CLIENT_ID = "client_id"
CONF_KEY = "key"
CONF_SCOPE = "scope"

DEFAULT_PORT = 80
SCAN_INTERVAL = timedelta(seconds=5)
# The plan and the prices change rarely; they are fetched again when their
# version in /api/v1/state changes, and at least this often.
SLOW_REFRESH = timedelta(minutes=5)
# A reading older than this is shown as unavailable: the dongle answers, but
# it cannot read the inverter.
STALE_READING = timedelta(seconds=60)

PAIR_POLL_SECONDS = 2
PAIR_TIMEOUT_SECONDS = 600

SCOPE_READ = "read"
SCOPE_CONTROL = "control"

# Actions (services).
SERVICE_CHARGE = "charge_from_grid"
SERVICE_HOLD = "hold_battery"
SERVICE_SELL = "sell_from_battery"
SERVICE_LIMIT_EXPORT = "limit_export"
SERVICE_RELEASE = "release"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"
ATTR_TARGET_SOC = "target_soc"
ATTR_POWER = "power"
ATTR_FLOOR_SOC = "floor_soc"
ATTR_DURATION = "duration"
ATTR_EXPORT_LIMIT = "export_limit"

# The control entities (select and numbers): what the next command asks for.
# The numbers are kept in Home Assistant (restored over a restart) and sent
# with the command when the select is changed; changing one does not change
# a running command.
SETTING_TARGET_SOC = "charge_target"
SETTING_POWER = "sell_power"
SETTING_FLOOR = "sell_floor"
SETTING_DURATION = "command_duration"
SETTING_EXPORT_LIMIT = "command_export_limit"
SETTING_DEFAULTS: dict[str, float | None] = {
    SETTING_TARGET_SOC: 80,
    SETTING_POWER: 3000,
    SETTING_FLOOR: None,  # none: the owner's minimum in GridZeno
    SETTING_DURATION: 60,
    SETTING_EXPORT_LIMIT: 0,
}
# The select's options, and the dongle command mode each one shows.
COMMAND_NONE = "none"
COMMAND_OPTIONS = [COMMAND_NONE, "charge_grid", "hold", "sell", "limit_export"]
COMMAND_MODES = {"charge_grid": "charge_grid", "hold": "hold", "sell": "sell", "inverter": "limit_export"}
