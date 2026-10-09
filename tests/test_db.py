from __future__ import annotations

from buzsak_pi3_server import db
from buzsak_pi3_server.config import DatabaseConfig


def test_connect_applies_required_pragmas(tmp_path):
    db_path = tmp_path / "nested" / "buzsak.sqlite3"
    config = DatabaseConfig(
        path=str(db_path), busy_timeout_ms=2500, synchronous="FULL")

    connection = db.connect(config)
    try:
        assert db_path.exists()
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        synchronous = connection.execute("PRAGMA synchronous").fetchone()[0]

        assert journal_mode.lower() == "wal"
        assert foreign_keys == 1
        assert synchronous == 2  # FULL == 2 in SQLite's pragma encoding
    finally:
        connection.close()
