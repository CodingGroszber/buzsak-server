"""Tests for buzsak_pi3_server.dispatcher.pulse (DEV-10, ARC-02).

matter_client.send_pulse is monkeypatched: its own wire-protocol
correctness is covered by test_poller_matter_client.py, so these tests
focus only on claiming/expiry/outcome-recording behavior.
"""

from __future__ import annotations

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.config import (
    AppConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.dispatcher import pulse
from buzsak_pi3_server.poller.errors import PollTransportError


@pytest.fixture
def connection(tmp_path):
    config = DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3"))
    conn = db.connect(config)
    schema.apply_migrations(conn)
    conn.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://127.0.0.1:1/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )
    yield conn
    conn.close()


def _app_config(*, address: str = "ws://127.0.0.1:1/ws#node_id=1") -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(),
        polling=PollingConfig(request_timeout_seconds=2.0),
        devices=(
            DeviceConfig(id="sonoff-1", kind="sonoff-minid",
                         address=address, enabled=True),
        ),
    )


def _insert_pending(connection, *, requested_at="2026-01-01T00:00:00Z", expires_at="2026-01-01T00:01:00Z"):
    connection.execute(
        "INSERT INTO pulse_requests (device_id, status, requested_at, expires_at) "
        "VALUES ('sonoff-1', 'pending', ?, ?)",
        (requested_at, expires_at),
    )
    return connection.execute("SELECT last_insert_rowid()").fetchone()[0]


def test_execute_pending_pulse_returns_false_when_queue_is_empty(connection):
    assert pulse.execute_pending_pulse(connection, _app_config()) is False


def test_execute_pending_pulse_succeeds_and_records_outcome(connection, monkeypatch):
    request_id = _insert_pending(
        connection, expires_at="2999-01-01T00:00:00Z")
    calls = []
    monkeypatch.setattr(
        pulse.matter_client, "send_pulse",
        lambda ws_url, node_id, **kwargs: calls.append(
            (ws_url, node_id, kwargs)),
    )

    processed = pulse.execute_pending_pulse(connection, _app_config())

    assert processed is True
    assert calls == [
        ("ws://127.0.0.1:1/ws", 1, {
            "on_time_deciseconds": 5, "off_wait_deciseconds": 5, "timeout_seconds": 2.0}),
    ]
    status, executed_at, error = connection.execute(
        "SELECT status, executed_at, error FROM pulse_requests WHERE id = ?",
        (request_id,),
    ).fetchone()
    assert status == "succeeded"
    assert executed_at is not None
    assert error is None


def test_execute_pending_pulse_records_failure_without_retrying(connection, monkeypatch):
    request_id = _insert_pending(
        connection, expires_at="2999-01-01T00:00:00Z")

    def _raise(*args, **kwargs):
        raise PollTransportError("device unreachable")
    monkeypatch.setattr(pulse.matter_client, "send_pulse", _raise)

    processed = pulse.execute_pending_pulse(connection, _app_config())

    assert processed is True
    status, error = connection.execute(
        "SELECT status, error FROM pulse_requests WHERE id = ?", (request_id,)
    ).fetchone()
    assert status == "failed"
    assert "device unreachable" in error

    # A second call must not find anything to retry: the failed row is terminal.
    assert pulse.execute_pending_pulse(connection, _app_config()) is False


def test_execute_pending_pulse_expires_stale_requests_instead_of_sending(connection, monkeypatch):
    request_id = _insert_pending(
        connection, expires_at="2000-01-01T00:00:00Z")
    monkeypatch.setattr(
        pulse.matter_client, "send_pulse",
        lambda *a, **k: pytest.fail("must not send an expired pulse"),
    )

    processed = pulse.execute_pending_pulse(connection, _app_config())

    assert processed is False
    status = connection.execute(
        "SELECT status FROM pulse_requests WHERE id = ?", (request_id,)
    ).fetchone()[0]
    assert status == "expired"


def test_execute_pending_pulse_fails_when_device_no_longer_configured(connection):
    request_id = _insert_pending(
        connection, expires_at="2999-01-01T00:00:00Z")
    empty_config = AppConfig(
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(),
        polling=PollingConfig(),
        devices=(),
    )

    processed = pulse.execute_pending_pulse(connection, empty_config)

    assert processed is True
    status, error = connection.execute(
        "SELECT status, error FROM pulse_requests WHERE id = ?", (request_id,)
    ).fetchone()
    assert status == "failed"
    assert "no longer configured" in error
