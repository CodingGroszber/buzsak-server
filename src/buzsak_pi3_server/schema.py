"""Versioned SQLite schema migrations (DB-07).

Migrations are plain SQL files named ``NNNN_description.sql`` under
migrations/, applied in ascending numeric order inside one manually
controlled transaction each, and tracked in ``schema_migrations``.
Re-running :func:`apply_migrations` is a no-op once a migration has been
recorded as applied. Connections must use ``isolation_level=None``
(see db.py) so the explicit BEGIN/COMMIT below is the only transaction
control in effect.

Statement splitting strips ``--`` line comments, then does a naive ``;``
split: migration files must not contain a ``;`` inside a string literal.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class SchemaError(RuntimeError):
    """Raised when the database schema is missing, ahead of, or incompatible
    with the versions this codebase knows how to apply."""


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _version_of(path: Path) -> int:
    return int(path.name.split("_", 1)[0])


def _strip_line_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _statements(sql: str) -> list[str]:
    stripped = _strip_line_comments(sql)
    return [s.strip() for s in stripped.split(";") if s.strip()]


def current_version(connection: sqlite3.Connection) -> int:
    """Returns the highest applied migration version, creating the tracking
    table if it does not exist yet (0 means an empty database)."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    row = connection.execute(
        "SELECT MAX(version) FROM schema_migrations").fetchone()
    return row[0] or 0


def latest_available_version() -> int:
    files = _migration_files()
    return _version_of(files[-1]) if files else 0


def apply_migrations(connection: sqlite3.Connection) -> int:
    """Applies any pending migrations in ascending order. Returns the
    resulting schema version. Each migration runs in its own transaction;
    a failure rolls back only that migration."""
    applied = current_version(connection)
    for path in _migration_files():
        version = _version_of(path)
        if version <= applied:
            continue
        connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in _statements(path.read_text(encoding="utf-8")):
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, datetime('now'))",
                (version,),
            )
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
        applied = version
    return applied


def require_supported_version(connection: sqlite3.Connection) -> None:
    """DB-07/OPS-05: reject clearly if the database is on a schema version
    newer than this codebase knows how to run against."""
    applied = current_version(connection)
    latest = latest_available_version()
    if applied > latest:
        raise SchemaError(
            f"database schema version {applied} is newer than the highest "
            f"version this application supports ({latest})"
        )
