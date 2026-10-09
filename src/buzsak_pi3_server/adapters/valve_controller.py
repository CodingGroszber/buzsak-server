"""Valve controller adapter contract: parsing for ``GET /api/state`` (DEV-01).

Ground truth verified directly against firmware source; see
docs/adapters/valve_controller.md for the inspected files, endpoints, JSON
shapes, and firmware quirks this module encodes. Only parsing and
capability metadata live here — no HTTP client, retry/backoff, or dispatch
(ARC-07).
"""

from __future__ import annotations

import dataclasses
from typing import Any, Literal

from buzsak_pi3_server.adapters.errors import AdapterParseError

DEVICE_KIND = "valve_controller"

ControlMode = Literal["manual", "automatic"]

# DEV-04: only these outputs are firmware-confirmed controllable relays.
# ``status_led`` is read-only and must never be advertised or executed as a
# command target. RS485 commissioning routes have no capability schema at
# all (docs/adapters/valve_controller.md) and are out of scope here.
CONTROLLABLE_OUTPUTS: tuple[str, ...] = (
    "relay1", "relay2", "relay3", "relay4")


@dataclasses.dataclass(frozen=True)
class ValveOutput:
    name: str
    label: str
    state: bool
    controllable: bool
    always_manual: bool


@dataclasses.dataclass(frozen=True)
class SensorReading:
    name: str
    label: str
    address: int
    ok: bool
    errors: int
    last_error: str
    raw: str
    age_s: int
    temperature_c: float | None = None
    humidity_pct: float | None = None


@dataclasses.dataclass(frozen=True)
class AutomationStatus:
    valve: str
    target_pct: float
    duty: float
    valve_on: bool
    period_s: int
    elapsed_s: int
    sensor_valid: bool
    time_synced: bool


@dataclasses.dataclass(frozen=True)
class ValveControllerState:
    firmware: str | None
    ip: str | None
    mode: ControlMode | None
    outputs: tuple[ValveOutput, ...]
    sensors: tuple[SensorReading, ...]
    automation: AutomationStatus | None
    rain_automation: RainAutomationStatus | None
    section_errors: dict[str, str]

    def output(self, name: str) -> ValveOutput:
        for output in self.outputs:
            if output.name == name:
                return output
        raise KeyError(name)

    @property
    def automation_is_live(self) -> bool:
        """DEV-05: ``automation`` is present in every payload regardless of
        mode, but only actively driven while ``mode == "automatic"``. While
        in manual mode its fields are stale/frozen, not a suspended-neutral
        state — see docs/adapters/valve_controller.md."""
        return self.mode == "automatic"


@dataclasses.dataclass(frozen=True)
class RainAutomationStatus:
    valve: str
    valve_on: bool
    start_hour: int
    start_minute: int
    duration_s: int
    has_last_run: bool
    last_run_duration_s: int | None
    time_synced: bool


def _get_dict(data: Any, key: str, where: str) -> Any:
    if not isinstance(data, dict) or key not in data:
        raise AdapterParseError(f"{where}: missing required field {key!r}")
    return data[key]


def _get_str(data: dict[str, Any], key: str, where: str) -> str:
    value = _get_dict(data, key, where)
    if not isinstance(value, str):
        raise AdapterParseError(f"{where}: field {key!r} must be a string")
    return value


def _get_bool(data: dict[str, Any], key: str, where: str) -> bool:
    value = _get_dict(data, key, where)
    if not isinstance(value, bool):
        raise AdapterParseError(f"{where}: field {key!r} must be a boolean")
    return value


def _get_number(data: dict[str, Any], key: str, where: str) -> float:
    value = _get_dict(data, key, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterParseError(f"{where}: field {key!r} must be a number")
    return value


def _get_list(data: dict[str, Any], key: str, where: str) -> list[Any]:
    value = _get_dict(data, key, where)
    if not isinstance(value, list):
        raise AdapterParseError(f"{where}: field {key!r} must be a list")
    return value


def _parse_mode(payload: dict[str, Any]) -> ControlMode:
    value = _get_str(payload, "mode", "state")
    if value not in ("manual", "automatic"):
        raise AdapterParseError(
            f"state: field 'mode' must be 'manual' or 'automatic', got {value!r}")
    return value  # type: ignore[return-value]


def _parse_output(item: Any, index: int) -> ValveOutput:
    where = f"outputs[{index}]"
    if not isinstance(item, dict):
        raise AdapterParseError(f"{where}: expected an object")
    return ValveOutput(
        name=_get_str(item, "name", where),
        label=_get_str(item, "label", where),
        state=_get_bool(item, "state", where),
        controllable=_get_bool(item, "controllable", where),
        always_manual=(
            _get_bool(item, "always_manual", where)
            if "always_manual" in item else False
        ),
    )


def _parse_sensor(item: Any, index: int) -> SensorReading:
    where = f"sensors[{index}]"
    if not isinstance(item, dict):
        raise AdapterParseError(f"{where}: expected an object")
    ok = _get_bool(item, "ok", where)
    # DEV-05: temperature_c/humidity_pct are omitted by firmware when ok is
    # false. Do not default them to 0.0 — a missing key means "unavailable."
    temperature_c = float(_get_number(
        item, "temperature_c", where)) if "temperature_c" in item else None
    humidity_pct = float(_get_number(item, "humidity_pct",
                         where)) if "humidity_pct" in item else None
    if ok and (temperature_c is None or humidity_pct is None):
        raise AdapterParseError(
            f"{where}: ok=true but temperature_c/humidity_pct missing")
    return SensorReading(
        name=_get_str(item, "name", where),
        label=_get_str(item, "label", where),
        address=int(_get_number(item, "address", where)),
        ok=ok,
        errors=int(_get_number(item, "errors", where)),
        last_error=_get_str(item, "last_error", where),
        raw=_get_str(item, "raw", where),
        age_s=int(_get_number(item, "age_s", where)),
        temperature_c=temperature_c,
        humidity_pct=humidity_pct,
    )


def _parse_mist_automation(item: Any) -> AutomationStatus:
    where = "automation.mist"
    if not isinstance(item, dict):
        raise AdapterParseError(f"{where}: expected an object")
    return AutomationStatus(
        valve=_get_str(item, "valve", where),
        target_pct=float(_get_number(item, "target_pct", where)),
        duty=float(_get_number(item, "duty", where)),
        valve_on=_get_bool(item, "valve_on", where),
        period_s=int(_get_number(item, "period_s", where)),
        elapsed_s=int(_get_number(item, "elapsed_s", where)),
        sensor_valid=_get_bool(item, "sensor_valid", where),
        time_synced=_get_bool(item, "time_synced", where),
    )


def _parse_rain_automation(item: Any) -> RainAutomationStatus:
    where = "automation.rain"
    if not isinstance(item, dict):
        raise AdapterParseError(f"{where}: expected an object")
    has_last_run = _get_bool(item, "has_last_run", where)
    last_run_duration_s = (
        int(_get_number(item, "last_run_duration_s", where))
        if has_last_run else None
    )
    start_hour = int(_get_number(item, "start_hour", where))
    start_minute = int(_get_number(item, "start_minute", where))
    if not 0 <= start_hour <= 23:
        raise AdapterParseError(
            f"{where}: field 'start_hour' must be between 0 and 23")
    if not 0 <= start_minute <= 59:
        raise AdapterParseError(
            f"{where}: field 'start_minute' must be between 0 and 59")
    return RainAutomationStatus(
        valve=_get_str(item, "valve", where),
        valve_on=_get_bool(item, "valve_on", where),
        start_hour=start_hour,
        start_minute=start_minute,
        duration_s=int(_get_number(item, "duration_s", where)),
        has_last_run=has_last_run,
        last_run_duration_s=last_run_duration_s,
        time_synced=_get_bool(item, "time_synced", where),
    )


def parse_state(payload: Any) -> ValveControllerState:
    """Parse a ``GET /api/state`` JSON body into a :class:`ValveControllerState`.

    Top-level non-object payloads raise :class:`AdapterParseError`. Individual
    sections retain independent parse errors so extraction can mark only the
    affected parameters invalid (DEV-05, DEV-06).
    """
    if not isinstance(payload, dict):
        raise AdapterParseError("state: expected a JSON object")

    section_errors: dict[str, str] = {}

    def parse_section(name: str, parser, fallback):
        try:
            return parser()
        except AdapterParseError as exc:
            section_errors[name] = str(exc)
            return fallback

    firmware = parse_section(
        "firmware", lambda: _get_str(payload, "firmware", "state"), None)
    ip = parse_section("ip", lambda: _get_str(payload, "ip", "state"), None)
    mode = parse_section("mode", lambda: _parse_mode(payload), None)
    outputs = parse_section(
        "outputs",
        lambda: tuple(
            _parse_output(item, i)
            for i, item in enumerate(_get_list(payload, "outputs", "state"))
        ),
        (),
    )
    sensors = parse_section(
        "sensors",
        lambda: tuple(
            _parse_sensor(item, i)
            for i, item in enumerate(_get_list(payload, "sensors", "state"))
        ),
        (),
    )

    automation = payload.get("automation")
    if isinstance(automation, dict) and "mist" in automation:
        mist_item = automation.get("mist")
        rain_item = automation.get("rain")
    else:
        mist_item = automation
        rain_item = None
    mist = parse_section(
        "automation_mist", lambda: _parse_mist_automation(mist_item), None)
    rain = (
        parse_section("automation_rain",
                      lambda: _parse_rain_automation(rain_item), None)
        if rain_item is not None else None
    )
    return ValveControllerState(
        firmware=firmware,
        ip=ip,
        mode=mode,
        outputs=outputs,
        sensors=sensors,
        automation=mist,
        rain_automation=rain,
        section_errors=section_errors,
    )
