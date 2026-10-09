"""Multi-connection lock-contention tests (DB-06).

Unlike test_observations.py (single connection, sequential calls), these
tests open two real connections to the same SQLite file and force them to
overlap, to check the actual behavior of BEGIN IMMEDIATE + busy_timeout
under contention rather than assuming it from documentation.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from buzsak_pi3_server import db, observations, schema
from buzsak_pi3_server.config import DatabaseConfig


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "buzsak.sqlite3"
    config = DatabaseConfig(path=str(path))
    setup = db.connect(config)
    schema.apply_migrations(setup)
    setup.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 0, "
        "datetime('now'), datetime('now'))"
    )
    setup.execute(
        "INSERT INTO parameters (device_id, parameter_id, category) "
        "VALUES ('garden-plc', 'well_pump', 'boolean')"
    )
    setup.close()
    return path


def test_second_writer_blocks_then_succeeds_after_first_commits(db_path):
    """A writer holding the row lock delays, but does not lose, a
    concurrent record_observation() call from another connection.

    Each connection is opened inside the thread that uses it, matching
    the real deployment shape (db.connect() is not shared across
    threads/processes; only the underlying file is shared).
    """
    holder_config = DatabaseConfig(path=str(db_path), busy_timeout_ms=2000)
    writer_config = DatabaseConfig(path=str(db_path), busy_timeout_ms=2000)
    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_write_lock():
        conn = db.connect(holder_config)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE devices SET updated_at = datetime('now') WHERE id = 'garden-plc'"
        )
        holder_ready.set()
        release_holder.wait(timeout=2)
        conn.execute("COMMIT")
        conn.close()

    holder = threading.Thread(target=hold_write_lock)
    holder.start()
    assert holder_ready.wait(
        timeout=2), "holder thread never acquired the lock"

    result_box: dict[str, observations.ObservationResult] = {}

    def write_observation():
        conn = db.connect(writer_config)
        result_box["result"] = observations.record_observation(
            conn, device_id="garden-plc", parameter_id="well_pump",
            category="boolean", value="true", value_type="bool",
            quality="good", observed_at="2026-09-30T00:00:00Z",
        )
        conn.close()

    writer = threading.Thread(target=write_observation)
    writer.start()
    time.sleep(0.2)
    assert writer.is_alive(), "writer should still be blocked on the held lock"

    release_holder.set()
    holder.join(timeout=2)
    writer.join(timeout=2)

    assert not writer.is_alive()
    assert result_box["result"].revision == 1


def test_second_writer_surfaces_a_clear_error_beyond_busy_timeout(db_path):
    """A wait longer than busy_timeout must fail visibly, not hang."""
    holder_config = DatabaseConfig(path=str(db_path), busy_timeout_ms=2000)
    writer_config = DatabaseConfig(path=str(db_path), busy_timeout_ms=200)
    holder_ready = threading.Event()

    def hold_write_lock():
        conn = db.connect(holder_config)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE devices SET updated_at = datetime('now') WHERE id = 'garden-plc'"
        )
        holder_ready.set()
        time.sleep(1)
        conn.execute("COMMIT")
        conn.close()

    holder = threading.Thread(target=hold_write_lock)
    holder.start()
    assert holder_ready.wait(
        timeout=2), "holder thread never acquired the lock"

    conn = db.connect(writer_config)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        observations.record_observation(
            conn, device_id="garden-plc", parameter_id="well_pump",
            category="boolean", value="true", value_type="bool",
            quality="good", observed_at="2026-09-30T00:00:00Z",
        )
    conn.close()

    holder.join(timeout=2)
