from __future__ import annotations

import json
from pathlib import Path

import pytest

from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.adapters.garden_plc import parse_state

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "garden_plc"


def _load(name: str):
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def test_parse_state_ok():
    state = parse_state(_load("state_ok.json"))

    assert state.firmware == "v4.0"
    assert state.output("well_pump").state is True
    assert state.output("well_pump").controllable is True
    assert state.output("switch_led").controllable is False

    pressure = state.analog_channel("pressure")
    assert pressure.signal is True
    assert pressure.bar == pytest.approx(3.0)

    water_level = state.analog_channel("water_level")
    assert water_level.water_level_valid is True
    assert water_level.liters == pytest.approx(750)


def test_pressure_invalid_omits_bar():
    state = parse_state(_load("state_pressure_invalid.json"))

    pressure = state.analog_channel("pressure")
    assert pressure.signal is False
    assert pressure.bar is None


def test_water_level_disconnected_is_flagged_invalid_despite_present_fields():
    """DEV-05: firmware still emits liters/low when signal=false; the
    adapter must not treat that as a valid reading."""
    state = parse_state(_load("state_water_level_disconnected.json"))

    water_level = state.analog_channel("water_level")
    assert water_level.signal is False
    # The firmware quirk: liters/low keys are present anyway.
    assert water_level.liters == pytest.approx(0)
    assert water_level.low is True
    # But the adapter's validity gate must reject them.
    assert water_level.water_level_valid is False


def test_malformed_missing_outputs_raises():
    with pytest.raises(AdapterParseError, match="outputs"):
        parse_state(_load("malformed_missing_outputs.json"))


def test_unknown_output_name_raises_keyerror():
    state = parse_state(_load("state_ok.json"))
    with pytest.raises(KeyError):
        state.output("does_not_exist")
