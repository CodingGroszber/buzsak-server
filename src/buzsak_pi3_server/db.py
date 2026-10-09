"""SQLite connection factory with required durability pragmas (DB-01).

Each caller should open a short-lived connection per unit of work; no
connection here is shared across threads/processes, and none should be kept
open during network I/O (ARC-04).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from buzsak_pi3_server.config import DatabaseConfig


def connect(config: DatabaseConfig) -> sqlite3.Connection:
    db_path = Path(config.path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(db_path, isolation_level=None)
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {config.busy_timeout_ms}")
    connection.execute(f"PRAGMA synchronous = {config.synchronous}")
    return connection
