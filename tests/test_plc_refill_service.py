from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.commands.service import CommandRejected, submit_command
from buzsak_pi3_server.config import (
    AppConfig,
    ControlConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.device_registration import sync_devices
from buzsak_pi3_server.security.credentials import Principal

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
_OPERATOR = Principal("cred-1", "garden-operator", "operator")


class _Clock:
    def __init__(self, now=_NOW):
        self.now = now

    def utc_now(self):
        return self.now


def _config(*, enabled=True):
    return AppConfig(
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(),
        polling=PollingConfig(default_interval_seconds=1, stale_multiplier=3),
        devices=(DeviceConfig(
            id="garden-plc", kind="garden-plc",
            address="http://127.0.0.1:1/", enabled=True),),
        control=ControlConfig(plc_refill_enabled=enabled),
    )


@pytest.fixture
def connection(tmp_path):
    conn = db.connect(DatabaseConfig(
        path=str(tmp_path / "plc-refill.sqlite3")))
    schema.apply_migrations(conn)
    config = _config()
    sync_devices(conn, config)
    timestamp = _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO device_health "
        "(device_id,status,last_success_at,last_error,consecutive_failures,updated_at) "
        "VALUES ('garden-plc','healthy',?,NULL,0,?)", (timestamp, timestamp))
    observations = {
        "switch_mode_code": ("1", "int"),
        "refill_button": ("false", "bool"),
        "refill_active": ("false", "bool"),
        "water_level_signal": ("true", "bool"),
        "water_level_liters": ("750.0", "float"),
    }
    for parameter_id, (value, value_type) in observations.items():
        conn.execute(
            "INSERT INTO observations_latest "
            "(device_id,parameter_id,value,value_type,quality,observed_at,last_changed_at,revision) "
            "VALUES ('garden-plc',?,?,?,'good',?,?,1)",
            (parameter_id, value, value_type, timestamp, timestamp))
    yield conn
    conn.close()


def _submit(connection, *, params=None, config=None, clock=None, key="refill-1"):
    return submit_command(
        connection,
        config or _config(),
        _OPERATOR,
        device_id="garden-plc",
        action_id="refill",
        params={} if params is None else params,
        idempotency_key=key,
        client_origin="android-app",
        clock=clock or _Clock(),
    )


def test_accepts_refill_with_fresh_auto_mode_and_valid_level(connection):
    accepted = _submit(connection)

    row = connection.execute(
        "SELECT state,action_id,device_id,expires_at FROM commands WHERE command_id=?",
        (accepted.command_id,),
    ).fetchone()
    audit = connection.execute(
        "SELECT event_type FROM command_audit_events WHERE command_id=?",
        (accepted.command_id,),
    ).fetchone()[0]
    assert row == (
        "pending", "refill", "garden-plc", "2026-10-21T12:00:12Z")
    assert audit == "accepted"


def test_refill_gate_is_off_by_default(connection):
    with pytest.raises(CommandRejected) as exc:
        _submit(connection, config=_config(enabled=False))
    assert exc.value.code == "control_disabled"


def test_refill_requires_auto_mode(connection):
    connection.execute(
        "UPDATE observations_latest SET value='2' "
        "WHERE device_id='garden-plc' AND parameter_id='switch_mode_code'")
    with pytest.raises(CommandRejected) as exc:
        _submit(connection)
    assert exc.value.code == "precondition_failed"


def test_refill_rejects_existing_request_or_active_fill(connection):
    connection.execute(
        "UPDATE observations_latest SET value='true' "
        "WHERE device_id='garden-plc' AND parameter_id='refill_active'")
    with pytest.raises(CommandRejected) as exc:
        _submit(connection)
    assert exc.value.code == "conflict"


def test_refill_requires_valid_water_level_below_stop_threshold(connection):
    connection.execute(
        "UPDATE observations_latest SET quality='invalid' "
        "WHERE device_id='garden-plc' AND parameter_id='water_level_signal'")
    with pytest.raises(CommandRejected) as exc:
        _submit(connection)
    assert exc.value.code == "stale_telemetry"

    connection.execute(
        "UPDATE observations_latest SET quality='good',value='true' "
        "WHERE device_id='garden-plc' AND parameter_id='water_level_signal'")
    connection.execute(
        "UPDATE observations_latest SET quality='good',value='1000.0' "
        "WHERE device_id='garden-plc' AND parameter_id='water_level_liters'")
    with pytest.raises(CommandRejected) as high_level:
        _submit(connection)
    assert high_level.value.code == "precondition_failed"


def test_refill_rejects_stale_telemetry(connection):
    old = (_NOW - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    connection.execute(
        "UPDATE observations_latest SET observed_at=? "
        "WHERE device_id='garden-plc' AND parameter_id='switch_mode_code'", (old,))
    with pytest.raises(CommandRejected) as exc:
        _submit(connection)
    assert exc.value.code == "stale_telemetry"


def test_refill_takes_no_parameters(connection):
    with pytest.raises(CommandRejected) as exc:
        _submit(connection, params={"state": True})
    assert exc.value.code == "invalid_request"
