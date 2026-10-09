"""Tests for buzsak_pi3_server.poller.health (POL-04)."""

from __future__ import annotations

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.config import DatabaseConfig
from buzsak_pi3_server.poller.health import (
    OFFLINE_AFTER_CONSECUTIVE_FAILURES,
    record_poll_failure,
    record_poll_success,
)


@pytest.fixture
def connection(tmp_path):
    config = DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3"))
    conn = db.connect(config)
    schema.apply_migrations(conn)
    conn.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )
    yield conn
    conn.close()


def test_first_success_creates_a_healthy_row(connection):
    record_poll_success(connection, "garden-plc",
                        observed_at="2026-09-30T00:00:00Z")

    row = connection.execute(
        "SELECT status, last_success_at, last_error, consecutive_failures "
        "FROM device_health WHERE device_id = 'garden-plc'"
    ).fetchone()
    assert row == ("healthy", "2026-09-30T00:00:00Z", None, 0)


def test_failure_then_success_clears_the_error_and_streak(connection):
    record_poll_failure(connection, "garden-plc",
                        observed_at="2026-09-30T00:00:00Z", error="boom")
    record_poll_success(connection, "garden-plc",
                        observed_at="2026-09-30T00:00:05Z")

    row = connection.execute(
        "SELECT status, last_success_at, last_error, consecutive_failures "
        "FROM device_health WHERE device_id = 'garden-plc'"
    ).fetchone()
    assert row == ("healthy", "2026-09-30T00:00:05Z", None, 0)


def test_failures_increment_and_stay_degraded_below_the_offline_threshold(connection):
    for i in range(OFFLINE_AFTER_CONSECUTIVE_FAILURES - 1):
        count = record_poll_failure(
            connection, "garden-plc", observed_at=f"2026-09-30T00:00:0{i}Z", error="timeout")

    assert count == OFFLINE_AFTER_CONSECUTIVE_FAILURES - 1
    status, last_success_at, last_error, consecutive_failures = connection.execute(
        "SELECT status, last_success_at, last_error, consecutive_failures "
        "FROM device_health WHERE device_id = 'garden-plc'"
    ).fetchone()
    assert status == "degraded"
    assert last_success_at is None
    assert last_error == "timeout"
    assert consecutive_failures == OFFLINE_AFTER_CONSECUTIVE_FAILURES - 1


def test_reaching_the_threshold_reports_offline(connection):
    count = 0
    for i in range(OFFLINE_AFTER_CONSECUTIVE_FAILURES):
        count = record_poll_failure(
            connection, "garden-plc", observed_at=f"2026-09-30T00:00:0{i}Z", error="timeout")

    assert count == OFFLINE_AFTER_CONSECUTIVE_FAILURES
    status = connection.execute(
        "SELECT status FROM device_health WHERE device_id = 'garden-plc'"
    ).fetchone()[0]
    assert status == "offline"


def test_error_message_is_truncated(connection):
    record_poll_failure(connection, "garden-plc",
                        observed_at="2026-09-30T00:00:00Z", error="x" * 1000)

    last_error = connection.execute(
        "SELECT last_error FROM device_health WHERE device_id = 'garden-plc'"
    ).fetchone()[0]
    assert len(last_error) == 300
