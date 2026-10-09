"""Flattens each adapter's typed state into generic catalog parameter rows.

Adapters (DEV-01) stay pure parsers that know nothing about persistence or
parameter_id naming (ARC-07); this module is the one place that maps each
adapter's dataclass fields onto the `adapters.catalog` parameter_ids so the
scheduler only ever deals with generic `ParsedObservation` rows.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from buzsak_pi3_server.adapters import garden_plc, matter_server, valve_controller
from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.adapters.registry import canonical_kind


@dataclasses.dataclass(frozen=True)
class ParsedObservation:
    parameter_id: str
    value: str | None
    value_type: str
    quality: str


def _bool_str(value: bool) -> str:
    return "true" if value else "false"


def extract_garden_plc(payload: Any) -> list[ParsedObservation]:
    state = garden_plc.parse_state(payload)
    try:
        well_pump = state.output("well_pump")
        tank_pump = state.output("tank_pump")
        switch_led = state.output("switch_led")
        wifi_led = state.output("wifi_led")
        right_sw = next(i for i in state.inputs if i.name == "right_sw")
        left_sw = next(i for i in state.inputs if i.name == "left_sw")
        pressure = state.analog_channel("pressure")
        water_level = state.analog_channel("water_level")
    except (KeyError, StopIteration) as exc:
        raise AdapterParseError(
            f"state: expected channel missing from payload: {exc}") from exc

    observations = [
        ParsedObservation("well_pump", _bool_str(
            well_pump.state), "bool", "good"),
        ParsedObservation("tank_pump", _bool_str(
            tank_pump.state), "bool", "good"),
        ParsedObservation("right_sw", _bool_str(
            right_sw.state), "bool", "good"),
        ParsedObservation("left_sw", _bool_str(
            left_sw.state), "bool", "good"),
        ParsedObservation("switch_led", _bool_str(
            switch_led.state), "bool", "good"),
        ParsedObservation("wifi_led", _bool_str(
            wifi_led.state), "bool", "good"),
    ]
    if pressure.signal and pressure.bar is not None:
        observations.append(ParsedObservation(
            "pressure_bar", str(pressure.bar), "float", "good"))
    else:
        observations.append(ParsedObservation(
            "pressure_bar", None, "null", "invalid"))
    if water_level.water_level_valid:
        observations.append(ParsedObservation(
            "water_level_liters", str(water_level.liters), "float", "good"))
    else:
        observations.append(ParsedObservation(
            "water_level_liters", None, "null", "invalid"))
    return observations


def extract_valve_controller(payload: Any) -> list[ParsedObservation]:
    state = valve_controller.parse_state(payload)
    observations: list[ParsedObservation] = []

    def invalid(parameter_ids: tuple[str, ...]) -> None:
        observations.extend(
            ParsedObservation(parameter_id, None, "null", "invalid")
            for parameter_id in parameter_ids
        )

    if state.mode is None:
        invalid(("mode",))
    else:
        observations.append(ParsedObservation(
            "mode", state.mode, "string", "good"))

    output_ids = {
        "relay1": ("relay1_mist", "relay1_controllable", "relay1_always_manual"),
        "relay2": ("relay2_rain", "relay2_controllable", "relay2_always_manual"),
        "relay3": ("relay3_drip", "relay3_controllable", "relay3_always_manual"),
        "relay4": ("relay4_light", "relay4_controllable", "relay4_always_manual"),
    }
    if "outputs" in state.section_errors:
        invalid(tuple(parameter_id for ids in output_ids.values()
                for parameter_id in ids))
    else:
        outputs_by_name = {output.name: output for output in state.outputs}
        for name, ids in output_ids.items():
            output = outputs_by_name.get(name)
            if output is None:
                invalid(ids)
                continue
            observations.extend((
                ParsedObservation(ids[0], _bool_str(
                    output.state), "bool", "good"),
                ParsedObservation(ids[1], _bool_str(
                    output.controllable), "bool", "good"),
                ParsedObservation(ids[2], _bool_str(
                    output.always_manual), "bool", "good"),
            ))

    sensor_ids = ("temperature_c", "humidity_pct", "ok", "last_error", "age_s")
    if "sensors" in state.section_errors:
        for sensor_name in ("sensor_a", "sensor_b"):
            invalid(tuple(f"{sensor_name}_{suffix}" for suffix in sensor_ids))
    else:
        sensors_by_name = {sensor.name: sensor for sensor in state.sensors}
        for sensor_name in ("sensor_a", "sensor_b"):
            sensor = sensors_by_name.get(sensor_name)
            if sensor is None:
                invalid(
                    tuple(f"{sensor_name}_{suffix}" for suffix in sensor_ids))
                continue
            observations.extend((
                ParsedObservation(f"{sensor_name}_ok", _bool_str(
                    sensor.ok), "bool", "good"),
                ParsedObservation(f"{sensor_name}_last_error",
                                  sensor.last_error, "string", "good"),
                ParsedObservation(f"{sensor_name}_age_s",
                                  str(sensor.age_s), "int", "good"),
            ))
            for suffix, value in (
                ("temperature_c", sensor.temperature_c),
                ("humidity_pct", sensor.humidity_pct),
            ):
                observations.append(
                    ParsedObservation(
                        f"{sensor_name}_{suffix}",
                        str(value) if sensor.ok and value is not None else None,
                        "float" if sensor.ok and value is not None else "null",
                        "good" if sensor.ok and value is not None else "invalid",
                    )
                )

    for parameter_id, value in (("firmware", state.firmware), ("ip", state.ip)):
        if value is None:
            invalid((parameter_id,))
        else:
            observations.append(ParsedObservation(
                parameter_id, value, "string", "good"))

    mist = state.automation
    if mist is None:
        invalid((
            "automation_valve_on", "automation_target_pct", "automation_duty",
            "automation_period_s", "automation_elapsed_s",
            "automation_sensor_valid", "automation_time_synced",
        ))
    else:
        observations.extend((
            ParsedObservation("automation_valve_on", _bool_str(
                mist.valve_on), "bool", "good"),
            ParsedObservation("automation_target_pct", str(
                mist.target_pct), "float", "good"),
            ParsedObservation("automation_duty", str(
                mist.duty), "float", "good"),
            ParsedObservation("automation_period_s", str(
                mist.period_s), "int", "good"),
            ParsedObservation("automation_elapsed_s", str(
                mist.elapsed_s), "int", "good"),
            ParsedObservation("automation_sensor_valid", _bool_str(
                mist.sensor_valid), "bool", "good"),
            ParsedObservation("automation_time_synced", _bool_str(
                mist.time_synced), "bool", "good"),
        ))

    rain = state.rain_automation
    if rain is None and "automation_rain" in state.section_errors:
        invalid((
            "rain_valve_on", "rain_start_hour", "rain_start_minute", "rain_duration_s",
            "rain_has_last_run", "rain_last_run_duration_s", "rain_time_synced",
        ))
    elif rain is None:
        observations.extend(
            ParsedObservation(parameter_id, None, "null", "unavailable")
            for parameter_id in (
                "rain_valve_on", "rain_start_hour", "rain_start_minute", "rain_duration_s",
                "rain_has_last_run", "rain_last_run_duration_s", "rain_time_synced",
            )
        )
    else:
        observations.extend((
            ParsedObservation("rain_valve_on", _bool_str(
                rain.valve_on), "bool", "good"),
            ParsedObservation("rain_start_hour", str(
                rain.start_hour), "int", "good"),
            ParsedObservation("rain_start_minute", str(
                rain.start_minute), "int", "good"),
            ParsedObservation("rain_duration_s", str(
                rain.duration_s), "int", "good"),
            ParsedObservation("rain_has_last_run", _bool_str(
                rain.has_last_run), "bool", "good"),
            ParsedObservation(
                "rain_last_run_duration_s",
                str(rain.last_run_duration_s) if rain.last_run_duration_s is not None else None,
                "int" if rain.last_run_duration_s is not None else "null",
                "good" if rain.last_run_duration_s is not None else "unavailable",
            ),
            ParsedObservation("rain_time_synced", _bool_str(
                rain.time_synced), "bool", "good"),
        ))
    return observations


def extract_sonoff_minid(payload: Any) -> list[ParsedObservation]:
    """Flattens one already-node-id-filtered Matter node dict (DEV-09).

    `payload` is a single node dict picked out of a `get_nodes` result by
    `adapters.matter_server.find_node()` — not the whole result list —
    keeping the "one payload = one device state" contract used by every
    other extractor (poller/scheduler.py).
    """
    state = matter_server.parse_node(payload)
    if state.on_off is None:
        return [ParsedObservation("on_off", None, "null", "invalid")]
    return [ParsedObservation("on_off", _bool_str(state.on_off), "bool", "good")]


_EXTRACTORS: dict[str, Callable[[Any], list[ParsedObservation]]] = {
    garden_plc.DEVICE_KIND: extract_garden_plc,
    valve_controller.DEVICE_KIND: extract_valve_controller,
    matter_server.DEVICE_KIND: extract_sonoff_minid,
}


def extract_observations(kind: str, payload: Any) -> list[ParsedObservation]:
    """Parses `payload` and flattens it via the extractor for `kind`.

    `kind` may be either spelling `registry.canonical_kind()` accepts.
    """
    canonical = canonical_kind(kind)
    try:
        extractor = _EXTRACTORS[canonical]
    except KeyError:
        raise AdapterParseError(
            f"no extractor registered for kind {canonical!r}") from None
    return extractor(payload)
