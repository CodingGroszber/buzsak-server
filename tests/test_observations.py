from __future__ import annotations

import sqlite3

import pytest

from buzsak_pi3_server import db, observations, schema
from buzsak_pi3_server.config import DatabaseConfig


@pytest.fixture
def connection(tmp_path):
    config = DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3"))
    connection = db.connect(config)
    schema.apply_migrations(connection)
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 0, "
        "datetime('now'), datetime('now'))"
    )
    for parameter_id, category in (
        ("well_pump", "boolean"),
        ("pressure_bar", "continuous"),
    ):
        connection.execute(
            "INSERT INTO parameters (device_id, parameter_id, category) "
            "VALUES ('garden-plc', ?, ?)",
            (parameter_id, category),
        )
    try:
        yield connection
    finally:
        connection.close()


def _latest(connection, parameter_id):
    return connection.execute(
        "SELECT value, value_type, quality, observed_at, last_changed_at, revision "
        "FROM observations_latest WHERE device_id = 'garden-plc' AND parameter_id = ?",
        (parameter_id,),
    ).fetchone()


def _history_count(connection, parameter_id):
    return connection.execute(
        "SELECT COUNT(*) FROM observations_history "
        "WHERE device_id = 'garden-plc' AND parameter_id = ?",
        (parameter_id,),
    ).fetchone()[0]


def test_first_valid_observation_writes_latest_and_history(connection):
    result = observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="true", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:00Z",
    )

    assert result == observations.ObservationResult(
        value_changed=True, history_appended=True, revision=1)
    latest = _latest(connection, "well_pump")
    assert latest == ("true", "bool", "good",
                      "2026-09-30T00:00:00Z", "2026-09-30T00:00:00Z", 1)
    assert _history_count(connection, "well_pump") == 1


def test_boolean_unchanged_poll_refreshes_latest_without_new_history_row(connection):
    observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="true", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:00Z",
    )

    result = observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="true", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:01Z",
    )

    assert result == observations.ObservationResult(
        value_changed=False, history_appended=False, revision=1)
    latest = _latest(connection, "well_pump")
    # observed_at refreshes (POL-07); last_changed_at/revision do not.
    assert latest == ("true", "bool", "good",
                      "2026-09-30T00:00:01Z", "2026-09-30T00:00:00Z", 1)
    assert _history_count(connection, "well_pump") == 1


def test_boolean_changed_poll_appends_history_and_bumps_revision(connection):
    observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="true", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:00Z",
    )

    result = observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="false", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:01Z",
    )

    assert result == observations.ObservationResult(
        value_changed=True, history_appended=True, revision=2)
    assert _history_count(connection, "well_pump") == 2


def test_invalid_poll_retains_last_valid_value_but_refreshes_quality(connection):
    observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="true", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:00Z",
    )

    result = observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value=None, value_type="null",
        quality="unavailable", observed_at="2026-09-30T00:00:05Z",
    )

    assert result == observations.ObservationResult(
        value_changed=False, history_appended=False, revision=1)
    latest = _latest(connection, "well_pump")
    # value/value_type/last_changed_at/revision retained; quality+observed_at refreshed.
    assert latest == ("true", "bool", "unavailable",
                      "2026-09-30T00:00:05Z", "2026-09-30T00:00:00Z", 1)
    assert _history_count(connection, "well_pump") == 1


def test_first_observation_invalid_stores_no_value(connection):
    result = observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value=None, value_type="null",
        quality="unavailable", observed_at="2026-09-30T00:00:00Z",
    )

    assert result == observations.ObservationResult(
        value_changed=False, history_appended=False, revision=0)
    latest = _latest(connection, "well_pump")
    assert latest == (None, "null", "unavailable",
                      "2026-09-30T00:00:00Z", None, 0)
    assert _history_count(connection, "well_pump") == 0


def test_recovery_after_outage_does_not_duplicate_history_row(connection):
    """POL-12: re-observing the same value after an outage refreshes
    freshness without a duplicate state-history entry."""
    observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="true", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:00Z",
    )
    observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value=None, value_type="null",
        quality="unavailable", observed_at="2026-09-30T00:00:05Z",
    )

    result = observations.record_observation(
        connection, device_id="garden-plc", parameter_id="well_pump",
        category="boolean", value="true", value_type="bool",
        quality="good", observed_at="2026-09-30T00:00:10Z",
    )

    assert result == observations.ObservationResult(
        value_changed=False, history_appended=False, revision=1)
    assert _history_count(connection, "well_pump") == 1


def test_continuous_category_appends_history_every_call_when_unchanged(connection):
    """Cadence gating is the caller's job; this module appends on every
    valid call for a continuous metric (POL-09)."""
    for i in range(3):
        observations.record_observation(
            connection, device_id="garden-plc", parameter_id="pressure_bar",
            category="continuous", value="3.0", value_type="float",
            quality="good", observed_at=f"2026-09-30T00:00:0{i}Z",
        )

    assert _history_count(connection, "pressure_bar") == 3
    latest = _latest(connection, "pressure_bar")
    assert latest[5] == 1  # revision only bumped on the first (initial) write


def test_continuous_invalid_poll_does_not_append_history(connection):
    result = observations.record_observation(
        connection, device_id="garden-plc", parameter_id="pressure_bar",
        category="continuous", value=None, value_type="null",
        quality="invalid", observed_at="2026-09-30T00:00:00Z",
    )

    assert result.history_appended is False
    assert _history_count(connection, "pressure_bar") == 0


def test_unwritable_quality_raises(connection):
    with pytest.raises(observations.ObservationError):
        observations.record_observation(
            connection, device_id="garden-plc", parameter_id="well_pump",
            category="boolean", value="true", value_type="bool",
            quality="stale", observed_at="2026-09-30T00:00:00Z",
        )


class _RaisingProxy:
    """Forwards execute() to a real connection, except raising once for
    SQL containing `trigger` — used to verify record_observation() rolls
    back cleanly if it fails partway through."""

    def __init__(self, real, trigger):
        self._real = real
        self._trigger = trigger

    def execute(self, sql, params=()):
        if self._trigger in sql:
            raise sqlite3.OperationalError("simulated failure")
        return self._real.execute(sql, params)


def test_failure_during_history_insert_rolls_back_latest_update(connection):
    proxy = _RaisingProxy(connection, "INSERT INTO observations_history")

    with pytest.raises(sqlite3.OperationalError):
        observations.record_observation(
            proxy, device_id="garden-plc", parameter_id="well_pump",
            category="boolean", value="true", value_type="bool",
            quality="good", observed_at="2026-09-30T00:00:00Z",
        )

    assert _latest(connection, "well_pump") is None
    assert _history_count(connection, "well_pump") == 0
