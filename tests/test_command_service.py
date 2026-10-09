from __future__ import annotations

import json
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

_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
_PRINCIPAL = Principal("credential-1", "android-owner", "operator")


class _FixedClock:
    def utc_now(self):
        return _NOW


def _app_config(*, valve_enabled=True, queue_depth=3):
    return AppConfig(
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(),
        polling=PollingConfig(
            default_interval_seconds=1.0, stale_multiplier=3.0),
        devices=(DeviceConfig(
            id="valve-controller", kind="valve-controller",
            address="http://127.0.0.1:1/", enabled=True),),
        control=ControlConfig(
            valve_enabled=valve_enabled, per_device_queue_depth=queue_depth),
    )


@pytest.fixture
def connection(tmp_path):
    connection = db.connect(DatabaseConfig(
        path=str(tmp_path / "commands.sqlite3")))
    schema.apply_migrations(connection)
    config = _app_config()
    sync_devices(connection, config)
    connection.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES ('valve-controller', 'healthy', ?, 0, ?)",
        (_NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    values = {
        "mode": ("manual", "string"),
        "relay1_mist": ("false", "bool"),
        "relay1_controllable": ("true", "bool"),
        "relay1_always_manual": ("false", "bool"),
        "relay2_rain": ("false", "bool"),
        "relay2_controllable": ("true", "bool"),
        "relay2_always_manual": ("false", "bool"),
        "relay3_drip": ("false", "bool"),
        "relay3_controllable": ("true", "bool"),
        "relay3_always_manual": ("false", "bool"),
        "relay4_light": ("false", "bool"),
        "relay4_controllable": ("true", "bool"),
        "relay4_always_manual": ("true", "bool"),
    }
    observed_at = _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    for parameter_id, (value, value_type) in values.items():
        connection.execute(
            "INSERT INTO observations_latest "
            "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
            "VALUES ('valve-controller', ?, ?, ?, 'good', ?, ?, 1)",
            (parameter_id, value, value_type, observed_at, observed_at),
        )
    yield connection
    connection.close()


def _submit(connection, *, key="request-1", action="set_output", params=None,
            config=None, expected_revisions=None):
    return submit_command(
        connection,
        config or _app_config(),
        _PRINCIPAL,
        device_id="valve-controller",
        action_id=action,
        params=params or {"name": "relay1", "state": True},
        idempotency_key=key,
        client_origin="android-app",
        expected_revisions=expected_revisions,
        clock=_FixedClock(),
    )


def test_submit_persists_command_idempotency_and_audit_before_return(connection):
    accepted = _submit(connection)

    row = connection.execute(
        "SELECT state, actor_id, device_id, action_id, expires_at "
        "FROM commands WHERE command_id = ?", (accepted.command_id,),
    ).fetchone()
    audit = connection.execute(
        "SELECT event_type, actor_id FROM command_audit_events WHERE command_id = ?",
        (accepted.command_id,),
    ).fetchone()
    idempotency = connection.execute(
        "SELECT command_id FROM command_idempotency WHERE actor_id = ? AND idempotency_key = ?",
        ("android-owner", "request-1"),
    ).fetchone()[0]

    assert row == (
        "pending", "android-owner", "valve-controller", "set_output",
        accepted.expires_at,
    )
    assert audit == ("accepted", "android-owner")
    assert idempotency == accepted.command_id
    assert accepted.expires_at == "2026-10-07T12:00:12Z"


def test_same_idempotency_key_returns_original_command(connection):
    first = _submit(connection)
    second = _submit(connection)

    assert second.command_id == first.command_id
    assert second.deduplicated is True
    assert connection.execute(
        "SELECT COUNT(*) FROM commands").fetchone()[0] == 1


def test_same_idempotency_key_with_different_request_conflicts(connection):
    _submit(connection)

    with pytest.raises(CommandRejected) as exc_info:
        _submit(connection, params={"name": "relay1", "state": False})

    assert exc_info.value.code == "idempotency_conflict"


def test_set_output_requires_manual_mode_except_always_manual_relay(connection):
    connection.execute(
        "UPDATE observations_latest SET value = 'automatic' "
        "WHERE device_id = 'valve-controller' AND parameter_id = 'mode'"
    )

    with pytest.raises(CommandRejected) as exc_info:
        _submit(connection)
    assert exc_info.value.code == "precondition_failed"

    accepted = _submit(
        connection,
        key="light-1",
        params={"name": "relay4", "state": True},
    )
    assert accepted.state == "pending"


def test_stale_telemetry_blocks_acceptance(connection):
    old = (_NOW - timedelta(seconds=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    connection.execute(
        "UPDATE observations_latest SET observed_at = ? "
        "WHERE device_id = 'valve-controller' AND parameter_id = 'mode'", (
            old,)
    )

    with pytest.raises(CommandRejected) as exc_info:
        _submit(connection)

    assert exc_info.value.code == "stale_telemetry"


def test_expected_revision_mismatch_blocks_acceptance(connection):
    with pytest.raises(CommandRejected) as exc_info:
        _submit(connection, expected_revisions={"mode": 2})

    assert exc_info.value.code == "stale_revision"


def test_set_mode_requires_fresh_state_for_every_relay(connection):
    accepted = _submit(
        connection,
        action="set_mode",
        params={"value": "automatic"},
    )

    preconditions = json.loads(connection.execute(
        "SELECT preconditions_json FROM commands WHERE command_id = ?",
        (accepted.command_id,),
    ).fetchone()[0])
    assert set(preconditions["observed_revisions"]) == {
        "mode", "relay1_mist", "relay2_rain", "relay3_drip", "relay4_light",
    }


def test_valve_command_is_disabled_by_default(connection):
    config = _app_config(valve_enabled=False)

    with pytest.raises(CommandRejected) as exc_info:
        _submit(connection, config=config)

    assert exc_info.value.code == "control_disabled"


def test_pending_queue_is_bounded(connection):
    config = _app_config(queue_depth=1)
    _submit(connection, config=config)

    with pytest.raises(CommandRejected) as exc_info:
        _submit(connection, key="request-2", config=config)

    assert exc_info.value.code == "queue_full"
