"""Apply pending schema migrations to the target's database (OPS-06).

Run on the target with `BUZSAK_CONFIG` already set, after `backup_db.py`
and before `sync_devices.py`. Safe to re-run: migrations are versioned and
idempotent (schema.apply_migrations).
"""
from __future__ import annotations

from buzsak_pi3_server import config, db, schema

app_config = config.load_config_from_env()
connection = db.connect(app_config.database)
version = schema.apply_migrations(connection)
connection.close()
print("migrated to version", version)
