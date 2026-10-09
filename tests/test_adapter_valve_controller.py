from __future__ import annotations

import json
from pathlib import Path

import pytest

from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.adapters.valve_controller import parse_state

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "valve_controller"


def _load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_parse_state_automatic():
    state = parse_state(_load("state_automatic.json"))

    assert state.mode == "automatic"
    assert state.automation_is_live is True
    assert state.output("relay1").controllable is True
    assert state.output("status_led").controllable is False

    sensor_a = state.sensors[0]
    assert sensor_a.ok is True
    assert sensor_a.temperature_c == pytest.approx(27.5)

    sensor_b = state.sensors[1]
    assert sensor_b.ok is False
    assert sensor_b.temperature_c is None
    assert sensor_b.humidity_pct is None
    assert sensor_b.last_error == "timeout"


def test_parse_state_manual_automation_not_live():
    """DEV-05: automation block is always present but stale/frozen while
    mode=manual — it must be readable, but flagged not-live."""
    state = parse_state(_load("state_manual.json"))

    assert state.mode == "manual"
    assert state.automation_is_live is False
    assert state.automation.duty == pytest.approx(0.0)


def test_parse_state_nested_v09_automation_and_always_manual_output():
    state = parse_state(_load("state_nested_v09.json"))

    assert state.mode == "automatic"
    assert state.automation is not None
    assert state.automation.period_s == 300
    assert state.rain_automation is not None
    assert state.rain_automation.start_hour == 6
    assert state.rain_automation.start_minute == 30
    assert state.rain_automation.has_last_run is False
    assert state.rain_automation.last_run_duration_s is None
    assert state.output("relay4").always_manual is True


def test_parse_state_keeps_valid_sections_when_automation_is_malformed():
    payload = _load("state_nested_v09.json")
    payload["automation"]["rain"]["start_hour"] = 24

    state = parse_state(payload)

    assert state.mode == "automatic"
    assert state.output("relay1").state is False
    assert state.automation is not None
    assert state.rain_automation is None
    assert "automation_rain" in state.section_errors


def test_negative_temperature_value_passes_through():
    """The firmware decodes the signed Modbus register before it ever
    reaches JSON (see docs/adapters/valve_controller.md); this adapter only
    needs to accept a negative float without misparsing or clamping it."""
    state = parse_state(_load("state_manual.json"))

    sensor_a = state.sensors[0]
    assert sensor_a.temperature_c == pytest.approx(-9.7)


def test_malformed_missing_mode_raises():
    state = parse_state(_load("malformed_missing_mode.json"))

    assert state.mode is None
    assert "mode" in state.section_errors


def test_unknown_output_name_raises_keyerror():
    state = parse_state(_load("state_automatic.json"))
    with pytest.raises(KeyError):
        state.output("does_not_exist")
