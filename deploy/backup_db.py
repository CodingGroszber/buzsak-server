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

app_config = config.load_config_from_env()
source_path = Path(app_config.database.path)

if not source_path.exists():
    print(f"no existing database at {source_path}, skipping backup")
    raise SystemExit(0)

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
