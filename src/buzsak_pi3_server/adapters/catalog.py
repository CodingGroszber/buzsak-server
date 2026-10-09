"""Static parameter/capability catalog per adapter kind (DEV-01, DEV-04).

This is the single source of truth for which telemetry parameters and
control capabilities `device_registration.sync_devices()` registers for a
device, keyed by the canonical underscored kind from `registry.py`. Values
mirror docs/adapters/garden_plc.md and docs/adapters/valve_controller.md's
capability summary tables; only firmware-confirmed controllable actions are
listed (DEV-04).
"""

from __future__ import annotations

import dataclasses

from buzsak_pi3_server.adapters import garden_plc, matter_server, valve_controller


@dataclasses.dataclass(frozen=True)
class ParameterEntry:
    parameter_id: str
    category: str
    unit: str | None = None


@dataclasses.dataclass(frozen=True)
class CapabilityEntry:
    action_id: str
    params_schema: dict[str, object]


@dataclasses.dataclass(frozen=True)
class DeviceCatalog:
    parameters: tuple[ParameterEntry, ...]
    capabilities: tuple[CapabilityEntry, ...]


CATALOG: dict[str, DeviceCatalog] = {
    garden_plc.DEVICE_KIND: DeviceCatalog(
        parameters=(
            ParameterEntry("well_pump", "boolean"),
            ParameterEntry("tank_pump", "boolean"),
            ParameterEntry("right_sw", "boolean"),
            ParameterEntry("left_sw", "boolean"),
            ParameterEntry("switch_led", "boolean"),
            ParameterEntry("wifi_led", "boolean"),
            ParameterEntry("pressure_bar", "continuous", "bar"),
            ParameterEntry("water_level_liters", "continuous", "L"),
        ),
        capabilities=(
            CapabilityEntry(
                "set_output",
                {"name": ["well_pump", "tank_pump"], "state": "bool"},
            ),
        ),
    ),
    valve_controller.DEVICE_KIND: DeviceCatalog(
        parameters=(
            ParameterEntry("relay1_mist", "boolean"),
            ParameterEntry("relay2_rain", "boolean"),
            ParameterEntry("relay3_drip", "boolean"),
            ParameterEntry("relay4_light", "boolean"),
            ParameterEntry("relay1_controllable", "configuration"),
            ParameterEntry("relay2_controllable", "configuration"),
            ParameterEntry("relay3_controllable", "configuration"),
            ParameterEntry("relay4_controllable", "configuration"),
            ParameterEntry("relay1_always_manual", "configuration"),
            ParameterEntry("relay2_always_manual", "configuration"),
            ParameterEntry("relay3_always_manual", "configuration"),
            ParameterEntry("relay4_always_manual", "configuration"),
            ParameterEntry("mode", "configuration"),
            ParameterEntry("firmware", "identity"),
            ParameterEntry("ip", "identity"),
            ParameterEntry("sensor_a_temperature_c", "continuous", "\u00b0C"),
            ParameterEntry("sensor_a_humidity_pct", "continuous", "%"),
            ParameterEntry("sensor_b_temperature_c", "continuous", "\u00b0C"),
            ParameterEntry("sensor_b_humidity_pct", "continuous", "%"),
            ParameterEntry("sensor_a_ok", "boolean"),
            ParameterEntry("sensor_b_ok", "boolean"),
            ParameterEntry("sensor_a_last_error", "diagnostic"),
            ParameterEntry("sensor_b_last_error", "diagnostic"),
            ParameterEntry("sensor_a_age_s", "diagnostic", "s"),
            ParameterEntry("sensor_b_age_s", "diagnostic", "s"),
            ParameterEntry("automation_valve_on", "boolean"),
            ParameterEntry("automation_target_pct", "continuous", "%"),
            ParameterEntry("automation_duty", "continuous", "ratio"),
            ParameterEntry("automation_period_s", "configuration", "s"),
            ParameterEntry("automation_elapsed_s", "continuous", "s"),
            ParameterEntry("automation_sensor_valid", "boolean"),
            ParameterEntry("automation_time_synced", "boolean"),
            ParameterEntry("rain_valve_on", "boolean"),
            ParameterEntry("rain_start_hour", "configuration"),
            ParameterEntry("rain_start_minute", "configuration"),
            ParameterEntry("rain_duration_s", "configuration", "s"),
            ParameterEntry("rain_has_last_run", "boolean"),
            ParameterEntry("rain_last_run_duration_s", "continuous", "s"),
            ParameterEntry("rain_time_synced", "boolean"),
        ),
        capabilities=(
            CapabilityEntry(
                "set_output",
                {"name": ["relay1", "relay2", "relay3",
                          "relay4"], "state": "bool"},
            ),
            CapabilityEntry(
                "set_mode",
                {"value": ["manual", "automatic"]},
            ),
        ),
    ),
    # DEV-10: one narrow, verified capability -- a fixed 0.5s OnOff pulse
    # ("OnWithTimedOff") for the two commissioned garage-relay switches.
    # This is a minimal scoped exception, not the general command
    # dispatcher (Section 9); no other Matter command is advertised here.
    matter_server.DEVICE_KIND: DeviceCatalog(
        parameters=(
            ParameterEntry("on_off", "boolean"),
        ),
        capabilities=(
            CapabilityEntry("pulse", {}),
        ),
    ),
}
