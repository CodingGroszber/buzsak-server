"""Tests for buzsak_pi3_server.poller.extraction (POL-07, DEV-05).

Reuses the adapter test fixtures (tests/fixtures/*) rather than duplicating
payload shapes, so these tests stay in sync with the verified contracts in
docs/adapters/*.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.poller.extraction import extract_observations

GARDEN_PLC_FIXTURES = Path(__file__).parent / "fixtures" / "garden_plc"
VALVE_CONTROLLER_FIXTURES = Path(__file__).parent / \
    "fixtures" / "valve_controller"


def _load(directory: Path, name: str):
    return json.loads((directory / name).read_text(encoding="utf-8"))


def _by_parameter_id(observations):
    return {item.parameter_id: item for item in observations}


def test_garden_plc_extracts_all_catalog_parameters():
    payload = _load(GARDEN_PLC_FIXTURES, "state_ok.json")

    observations = extract_observations("garden-plc", payload)

    by_id = _by_parameter_id(observations)
    assert set(by_id) == {
        "well_pump", "tank_pump", "right_sw", "left_sw",
        "switch_led", "wifi_led", "pressure_bar", "water_level_liters",
    }
    assert by_id["well_pump"].value == "true"
    assert by_id["well_pump"].value_type == "bool"
    assert by_id["well_pump"].quality == "good"
    assert by_id["tank_pump"].value == "false"
    assert by_id["left_sw"].value == "false"
    assert by_id["switch_led"].value == "true"
    assert by_id["wifi_led"].value == "true"
    assert by_id["pressure_bar"].value == "3.0"
    assert by_id["pressure_bar"].quality == "good"
    assert by_id["water_level_liters"].value == "750.0"


def test_garden_plc_accepts_the_canonical_underscored_kind_too():
    payload = _load(GARDEN_PLC_FIXTURES, "state_ok.json")

    observations = extract_observations("garden_plc", payload)

    assert len(observations) == 8


def test_garden_plc_pressure_invalid_signal_is_reported_as_invalid_null():
    payload = _load(GARDEN_PLC_FIXTURES, "state_pressure_invalid.json")

    by_id = _by_parameter_id(extract_observations("garden-plc", payload))

    assert by_id["pressure_bar"].value is None
    assert by_id["pressure_bar"].value_type == "null"
    assert by_id["pressure_bar"].quality == "invalid"


def test_garden_plc_water_level_disconnected_overrides_the_firmwares_fake_zero():
    payload = _load(GARDEN_PLC_FIXTURES,
                    "state_water_level_disconnected.json")

    by_id = _by_parameter_id(extract_observations("garden-plc", payload))

    assert by_id["water_level_liters"].value is None
    assert by_id["water_level_liters"].value_type == "null"
    assert by_id["water_level_liters"].quality == "invalid"


def test_garden_plc_malformed_payload_raises_adapter_parse_error():
    payload = _load(GARDEN_PLC_FIXTURES, "malformed_missing_outputs.json")

    with pytest.raises(AdapterParseError):
        extract_observations("garden-plc", payload)


def test_valve_controller_extracts_all_catalog_parameters():
    payload = _load(VALVE_CONTROLLER_FIXTURES, "state_manual.json")

    by_id = _by_parameter_id(extract_observations(
        "valve-controller", payload))

    assert set(by_id) == {
        "relay1_mist", "relay2_rain", "relay3_drip", "relay4_light", "mode",
        "relay1_controllable", "relay2_controllable", "relay3_controllable", "relay4_controllable",
        "relay1_always_manual", "relay2_always_manual", "relay3_always_manual", "relay4_always_manual",
        "sensor_a_temperature_c", "sensor_a_humidity_pct",
        "sensor_a_ok", "sensor_a_last_error", "sensor_a_age_s",
        "sensor_b_temperature_c", "sensor_b_humidity_pct",
        "sensor_b_ok", "sensor_b_last_error", "sensor_b_age_s",
        "firmware", "ip",
        "automation_valve_on", "automation_target_pct", "automation_duty",
        "automation_period_s", "automation_elapsed_s",
        "automation_sensor_valid", "automation_time_synced",
        "rain_valve_on", "rain_start_hour", "rain_start_minute", "rain_duration_s",
        "rain_has_last_run", "rain_last_run_duration_s", "rain_time_synced",
    }
    assert by_id["relay1_mist"].value == "true"
    assert by_id["relay2_rain"].value == "false"
    assert by_id["relay3_drip"].value == "false"
    assert by_id["relay4_light"].value == "false"
    assert by_id["mode"].value == "manual"
    assert by_id["mode"].value_type == "string"
    assert by_id["sensor_a_temperature_c"].value == "-9.7"
    assert by_id["sensor_a_humidity_pct"].value == "61.2"
    assert by_id["sensor_b_temperature_c"].value == "22.1"
    assert by_id["sensor_b_humidity_pct"].value == "55.0"
    assert by_id["automation_valve_on"].value == "false"
    assert by_id["automation_target_pct"].value == "90.0"
    assert by_id["automation_sensor_valid"].value == "true"
    assert by_id["automation_time_synced"].value == "true"


def test_valve_controller_malformed_payload_raises_adapter_parse_error():
    payload = _load(VALVE_CONTROLLER_FIXTURES, "malformed_missing_mode.json")

    by_id = _by_parameter_id(extract_observations("valve-controller", payload))

    assert by_id["mode"].quality == "invalid"
    assert by_id["relay1_mist"].quality == "invalid"


def test_valve_controller_extracts_nested_v09_data_and_always_manual():
    payload = _load(VALVE_CONTROLLER_FIXTURES, "state_nested_v09.json")

    by_id = _by_parameter_id(extract_observations("valve-controller", payload))

    assert by_id["firmware"].value == "v0.9"
    assert by_id["ip"].value == "192.168.1.109"
    assert by_id["relay4_controllable"].value == "true"
    assert by_id["relay4_always_manual"].value == "true"
    assert by_id["sensor_b_ok"].value == "false"
    assert by_id["sensor_b_last_error"].value == "timeout"
    assert by_id["sensor_b_age_s"].value == "91"
    assert by_id["sensor_b_temperature_c"].quality == "invalid"
    assert by_id["automation_period_s"].value == "300"
    assert by_id["rain_start_hour"].value == "6"
    assert by_id["rain_start_minute"].value == "30"
    assert by_id["rain_duration_s"].value == "180"
    assert by_id["rain_last_run_duration_s"].quality == "unavailable"


def test_legacy_valve_payload_marks_unreported_rain_fields_unavailable():
    payload = _load(VALVE_CONTROLLER_FIXTURES, "state_manual.json")

    by_id = _by_parameter_id(extract_observations("valve-controller", payload))

    assert by_id["automation_period_s"].value == "600"
    assert by_id["rain_start_hour"].quality == "unavailable"
    assert by_id["rain_valve_on"].quality == "unavailable"


def test_malformed_rain_automation_does_not_invalidate_relays_or_mist():
    payload = _load(VALVE_CONTROLLER_FIXTURES, "state_nested_v09.json")
    payload["automation"]["rain"]["start_hour"] = 24

    by_id = _by_parameter_id(extract_observations("valve-controller", payload))

    assert by_id["relay1_mist"].quality == "good"
    assert by_id["mode"].quality == "good"
    assert by_id["automation_period_s"].quality == "good"
    assert by_id["rain_start_hour"].quality == "invalid"
    assert by_id["rain_valve_on"].quality == "invalid"
