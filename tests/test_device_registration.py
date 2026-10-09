"""Tests for buzsak_pi3_server.device_registration (DEV-02, DEV-04).

Exercises sync_devices() against a migrated SQLite file, independent of the
dashboard/poller, to isolate the config->DB sync step.
"""

from __future__ import annotations

import json

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.adapters.registry import UnknownDeviceKindError
from buzsak_pi3_server.config import (
    AppConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.device_registration import sync_devices


@pytest.fixture
def connection(tmp_path):
    config = DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3"))
    conn = db.connect(config)
    schema.apply_migrations(conn)
    yield conn
    conn.close()


def _app_config(devices: tuple[DeviceConfig, ...]) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(),
        polling=PollingConfig(
            default_interval_seconds=1.0, stale_multiplier=3.0),
        devices=devices,
    )


def test_sync_registers_devices_with_canonical_underscored_kind(connection):
    app_config = _app_config((
        DeviceConfig(id="garden-plc", kind="garden-plc",
                     address="http://192.168.1.94/", enabled=False),
        DeviceConfig(id="valve-controller", kind="valve-controller",
                     address="http://192.168.1.109/", enabled=True),
    ))

    sync_devices(connection, app_config)

    rows = connection.execute(
        "SELECT id, kind, address, enabled FROM devices ORDER BY id"
    ).fetchall()
    assert rows == [
        ("garden-plc", "garden_plc", "http://192.168.1.94/", 0),
        ("valve-controller", "valve_controller", "http://192.168.1.109/", 1),
    ]


def test_sync_registers_catalog_parameters_and_capabilities(connection):
    app_config = _app_config((
        DeviceConfig(id="garden-plc", kind="garden-plc",
                     address="http://192.168.1.94/", enabled=False),
    ))

    sync_devices(connection, app_config)

    parameter_ids = {
        row[0]
        for row in connection.execute(
            "SELECT parameter_id FROM parameters WHERE device_id = 'garden-plc'"
        ).fetchall()
    }
    assert parameter_ids == {
        "well_pump", "tank_pump", "right_sw", "left_sw",
        "switch_led", "wifi_led", "pressure_bar", "water_level_liters",
    }

    action_id, params_schema = connection.execute(
        "SELECT action_id, params_schema FROM capabilities WHERE device_id = 'garden-plc'"
    ).fetchone()
    assert action_id == "set_output"
    assert json.loads(params_schema) == {
        "name": ["well_pump", "tank_pump"], "state": "bool"}


def test_sync_registers_expanded_valve_telemetry_without_schema_changes(connection):
    app_config = _app_config((
        DeviceConfig(id="valve-controller", kind="valve-controller",
                     address="http://192.168.1.109/", enabled=True),
    ))

    sync_devices(connection, app_config)

    parameter_ids = {
        row[0]
        for row in connection.execute(
            "SELECT parameter_id FROM parameters WHERE device_id = 'valve-controller'"
        ).fetchall()
    }
    assert {
        "rain_valve_on", "rain_start_hour", "rain_start_minute", "rain_duration_s",
        "rain_has_last_run", "rain_last_run_duration_s", "rain_time_synced",
        "relay4_controllable", "relay4_always_manual", "sensor_a_ok",
        "sensor_a_last_error", "sensor_a_age_s", "firmware", "ip",
    } <= parameter_ids

    capabilities = {
        row[0] for row in connection.execute(
            "SELECT action_id FROM capabilities WHERE device_id = 'valve-controller'"
        ).fetchall()
    }
    assert capabilities == {"set_output", "set_mode"}


def test_sync_is_idempotent_and_updates_changed_fields(connection):
    app_config = _app_config((
        DeviceConfig(id="garden-plc", kind="garden-plc",
                     address="http://192.168.1.94/", enabled=False),
    ))
    sync_devices(connection, app_config)

    updated_config = _app_config((
        DeviceConfig(id="garden-plc", kind="garden-plc",
                     address="http://192.168.1.94/", enabled=True),
    ))
    sync_devices(connection, updated_config)

    device_count = connection.execute(
        "SELECT COUNT(*) FROM devices").fetchone()[0]
    parameter_count = connection.execute(
        "SELECT COUNT(*) FROM parameters WHERE device_id = 'garden-plc'"
    ).fetchone()[0]
    enabled = connection.execute(
        "SELECT enabled FROM devices WHERE id = 'garden-plc'"
    ).fetchone()[0]
    assert device_count == 1
    assert parameter_count == 8
    assert enabled == 1


def test_unknown_kind_raises_and_rolls_back_the_whole_sync(connection):
    app_config = _app_config((
        DeviceConfig(id="garden-plc", kind="garden-plc",
                     address="http://192.168.1.94/", enabled=False),
        DeviceConfig(id="mystery-device", kind="not-a-real-kind",
                     address="http://192.168.1.200/", enabled=False),
    ))

    with pytest.raises(UnknownDeviceKindError):
        sync_devices(connection, app_config)

    device_count = connection.execute(
        "SELECT COUNT(*) FROM devices").fetchone()[0]
    assert device_count == 0
