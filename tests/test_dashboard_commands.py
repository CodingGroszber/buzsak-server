"""Tests for buzsak_pi3_server.dashboard.commands (DEV-10, ARC-03).

Exercises request_pulse() directly against a migrated SQLite file,
independent of Flask, mirroring test_dashboard_queries.py's approach.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.config import (
    AppConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.dashboard.commands import PulseRejected, request_pulse


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def utc_now(self) -> datetime:
        return self._now


_NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def connection(tmp_path):
    config = DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3"))
    conn = db.connect(config)
    schema.apply_migrations(conn)
    yield conn
    conn.close()


def _app_config(*, enabled: bool = True) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(),
        polling=PollingConfig(),
        devices=(
            DeviceConfig(id="sonoff-1", kind="sonoff-minid",
                         address="ws://192.168.1.95:5580/ws#node_id=1", enabled=enabled),
        ),
    )


def _register_healthy_device(connection, *, device_id="sonoff-1", status="healthy", consecutive_failures=0):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES (?, 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))",
        (device_id,),
    )
    connection.execute(
        "INSERT INTO capabilities (device_id, action_id, params_schema) "
        "VALUES (?, 'pulse', '{}')",
        (device_id,),
    )
    connection.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (device_id, status, _NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
         consecutive_failures, _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )
    connection.execute(
        "INSERT INTO parameters (device_id, parameter_id, category) "
        "VALUES (?, 'on_off', 'boolean')",
        (device_id,),
    )
    connection.execute(
        "INSERT INTO observations_latest "
        "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
        "VALUES (?, 'on_off', 'false', 'bool', 'good', ?, ?, 1)",
        (device_id, _NOW.strftime("%Y-%m-%dT%H:%M:%SZ"),
         _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")),
    )


def test_request_pulse_succeeds_for_a_healthy_registered_device(connection):
    _register_healthy_device(connection)

    request_id = request_pulse(
        connection, _app_config(), "sonoff-1", clock=_FixedClock(_NOW))

    row = connection.execute(
        "SELECT device_id, status FROM pulse_requests WHERE id = ?", (
            request_id,)
    ).fetchone()
    assert row == ("sonoff-1", "pending")


def test_request_pulse_rejects_an_unknown_device(connection):
    with pytest.raises(PulseRejected):
        request_pulse(connection, _app_config(),
                      "does-not-exist", clock=_FixedClock(_NOW))


def test_request_pulse_rejects_a_disabled_device(connection):
    _register_healthy_device(connection)

    with pytest.raises(PulseRejected):
        request_pulse(connection, _app_config(enabled=False),
                      "sonoff-1", clock=_FixedClock(_NOW))


def test_request_pulse_rejects_a_device_without_the_pulse_capability(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO device_health (device_id, status, consecutive_failures, updated_at) "
        "VALUES ('sonoff-1', 'healthy', 0, datetime('now'))"
    )

    with pytest.raises(PulseRejected, match="capability"):
        request_pulse(connection, _app_config(),
                      "sonoff-1", clock=_FixedClock(_NOW))


def test_request_pulse_rejects_an_unhealthy_device(connection):
    _register_healthy_device(connection, status="offline",
                             consecutive_failures=3)

    with pytest.raises(PulseRejected, match="healthy"):
        request_pulse(connection, _app_config(),
                      "sonoff-1", clock=_FixedClock(_NOW))


def test_request_pulse_rejects_a_second_request_in_flight(connection):
    _register_healthy_device(connection)
    request_pulse(connection, _app_config(),
                  "sonoff-1", clock=_FixedClock(_NOW))

    with pytest.raises(PulseRejected, match="in flight|recently"):
        request_pulse(connection, _app_config(),
                      "sonoff-1", clock=_FixedClock(_NOW))


def test_request_pulse_rejects_within_the_cooldown_window_after_completion(connection):
    _register_healthy_device(connection)
    first_id = request_pulse(
        connection, _app_config(), "sonoff-1", clock=_FixedClock(_NOW))
    connection.execute(
        "UPDATE pulse_requests SET status = 'succeeded' WHERE id = ?", (first_id,))

    with pytest.raises(PulseRejected, match="recently"):
        request_pulse(connection, _app_config(),
                      "sonoff-1", clock=_FixedClock(_NOW))


def test_request_pulse_allows_a_new_request_after_the_cooldown_window(connection):
    from datetime import timedelta

    _register_healthy_device(connection)
    first_id = request_pulse(
        connection, _app_config(), "sonoff-1", clock=_FixedClock(_NOW))
    connection.execute(
        "UPDATE pulse_requests SET status = 'succeeded' WHERE id = ?", (first_id,))

    later = _FixedClock(_NOW + timedelta(seconds=30))
    later_text = later.utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
    connection.execute(
        "UPDATE device_health SET last_success_at = ?, updated_at = ? WHERE device_id = 'sonoff-1'",
        (later_text, later_text),
    )
    connection.execute(
        "UPDATE observations_latest SET observed_at = ? "
        "WHERE device_id = 'sonoff-1' AND parameter_id = 'on_off'",
        (later_text,),
    )
    second_id = request_pulse(
        connection, _app_config(), "sonoff-1", clock=later)

    assert second_id != first_id
