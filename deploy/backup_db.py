"""Minimal local SQLite backup, run before migrations (partial OPS-10).

Uses sqlite3's online backup API so it is safe to run against a database a
running service still has open. Writes a timestamped copy under
`<data_dir>/backups/`, which stays inside the writable, persisted `data/`
directory -- never inside the replaceable release directory (OPS-07).

This is a minimal safety net only: no retention policy, no off-device
copy, and no restore drill exist yet (see backlog.md, OPS-10/OPS-11).
"""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

from buzsak_pi3_server import config
from buzsak_pi3_server.schema import latest_available_version


def _schema_needs_backup(source_path: Path, latest_version: int) -> bool:
    """Return whether the existing DB will need a schema migration."""
    if not source_path.exists():
        return False
    uri = f"file:{source_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        has_migrations = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone()
        if has_migrations is None:
            current_version = 0
        else:
            current_version = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        return current_version < latest_version
    finally:
        connection.close()


def main() -> None:
    app_config = config.load_config_from_env()
    source_path = Path(app_config.database.path)
    if not source_path.exists():
        print(f"no existing database at {source_path}, skipping backup")
        return
    if not _schema_needs_backup(source_path, latest_available_version()):
        print("database schema is current; no pre-migration backup needed")
        return

    backups_dir = source_path.parent / "backups"
    backups_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.datetime.now(
        datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_path = backups_dir / f"{source_path.stem}-{timestamp}.sqlite3"

    source_conn = sqlite3.connect(str(source_path))
    dest_conn = sqlite3.connect(str(dest_path))
    try:
        with dest_conn:
            source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()

    print(f"backed up {source_path} -> {dest_path}")


if __name__ == "__main__":
    main()
