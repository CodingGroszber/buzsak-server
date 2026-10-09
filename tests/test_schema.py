from __future__ import annotations

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.config import DatabaseConfig


@pytest.fixture
def connection(tmp_path):
    config = DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3"))
    connection = db.connect(config)
    try:
        yield connection
    finally:
        connection.close()


def test_fresh_database_has_version_zero(connection):
    assert schema.current_version(connection) == 0


def test_apply_migrations_creates_expected_tables(connection):
    version = schema.apply_migrations(connection)

    assert version == schema.latest_available_version()
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {
        "devices", "parameters", "capabilities",
        "observations_latest", "observations_history", "device_health",
        "api_credentials", "commands", "command_idempotency",
        "command_attempts", "command_audit_events",
    } <= tables


def test_apply_migrations_is_idempotent(connection):
    schema.apply_migrations(connection)
    schema.apply_migrations(connection)

    rows = connection.execute(
        "SELECT version FROM schema_migrations").fetchall()
    assert [row[0] for row in rows] == list(range(1, len(rows) + 1))


def test_require_supported_version_accepts_current_schema(connection):
    schema.apply_migrations(connection)
    schema.require_supported_version(connection)  # must not raise


def test_require_supported_version_rejects_future_schema(connection):
    schema.apply_migrations(connection)
    connection.execute(
        "INSERT INTO schema_migrations (version, applied_at) VALUES (999, datetime('now'))"
    )

    with pytest.raises(schema.SchemaError, match="999"):
        schema.require_supported_version(connection)


def test_foreign_key_violation_is_rejected(connection):
    schema.apply_migrations(connection)

    with pytest.raises(Exception):
        connection.execute(
            "INSERT INTO parameters (device_id, parameter_id, category) "
            "VALUES ('missing-device', 'foo', 'boolean')"
        )


def test_insert_roundtrip_across_core_tables(connection):
    schema.apply_migrations(connection)

    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 0, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO parameters (device_id, parameter_id, category, unit) "
        "VALUES ('garden-plc', 'well_pump', 'boolean', NULL)"
    )
    connection.execute(
        "INSERT INTO capabilities (device_id, action_id, params_schema) "
        "VALUES ('garden-plc', 'set_output', '{\"name\": \"well_pump\", \"state\": \"bool\"}')"
    )
    connection.execute(
        "INSERT INTO observations_latest "
        "(device_id, parameter_id, value, value_type, quality, observed_at, revision) "
        "VALUES ('garden-plc', 'well_pump', 'true', 'bool', 'good', datetime('now'), 1)"
    )
    connection.execute(
        "INSERT INTO observations_history "
        "(device_id, parameter_id, value, value_type, quality, observed_at) "
        "VALUES ('garden-plc', 'well_pump', 'true', 'bool', 'good', datetime('now'))"
    )
    connection.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES ('garden-plc', 'healthy', datetime('now'), 0, datetime('now'))"
    )

    latest = connection.execute(
        "SELECT value, revision FROM observations_latest WHERE device_id = 'garden-plc'"
    ).fetchone()
    assert latest == ("true", 1)

    history_count = connection.execute(
        "SELECT COUNT(*) FROM observations_history WHERE device_id = 'garden-plc'"
    ).fetchone()[0]
    assert history_count == 1
