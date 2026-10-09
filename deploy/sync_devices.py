"""Sync the target config's `devices` list into the database (OPS-06).

Run on the target with `BUZSAK_CONFIG` already set, after `migrate.py`.
Safe to re-run: upserts by device id (device_registration.sync_devices); an
unrecognized device `kind` fails the whole sync rather than partially
registering devices.
"""
from __future__ import annotations

from buzsak_pi3_server import config, db
from buzsak_pi3_server.device_registration import sync_devices

app_config = config.load_config_from_env()
connection = db.connect(app_config.database)
sync_devices(connection, app_config)
connection.close()
print("devices synced")
