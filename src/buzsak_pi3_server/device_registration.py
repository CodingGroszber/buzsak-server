"""Syncs config.yaml's `devices` list into the devices/parameters/capabilities
tables (DEV-02, DEV-04).

Nothing else currently copies config into the database (backlog.md "No
config->DB sync"): without this, the dashboard and any future poller/
dispatcher code only see whatever rows a test or operator inserted by hand.
This module owns that one job and nothing else -- it never touches
observations or device_health, and it never contacts a device.
"""

from __future__ import annotations

import json
import sqlite3

from buzsak_pi3_server.adapters import catalog
from buzsak_pi3_server.adapters.registry import canonical_kind
from buzsak_pi3_server.config import AppConfig, DeviceConfig


def sync_devices(connection: sqlite3.Connection, app_config: AppConfig) -> None:
    """Upserts every configured device and its catalog parameters/
    capabilities in one transaction, so a device with an unrecognized kind
    fails the whole sync rather than partially registering devices."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        for device in app_config.devices:
            _sync_device(connection, device)
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def _sync_device(connection: sqlite3.Connection, device: DeviceConfig) -> None:
    kind = canonical_kind(device.kind)
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, datetime('now'), datetime('now')) "
        "ON CONFLICT (id) DO UPDATE SET "
        "kind = excluded.kind, address = excluded.address, "
        "enabled = excluded.enabled, updated_at = datetime('now')",
        (device.id, kind, device.address, int(device.enabled)),
    )
    entry = catalog.CATALOG[kind]
    for parameter in entry.parameters:
        connection.execute(
            "INSERT INTO parameters (device_id, parameter_id, category, unit) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT (device_id, parameter_id) DO UPDATE SET "
            "category = excluded.category, unit = excluded.unit",
            (device.id, parameter.parameter_id,
             parameter.category, parameter.unit),
        )
    for capability in entry.capabilities:
        connection.execute(
            "INSERT INTO capabilities (device_id, action_id, params_schema) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT (device_id, action_id) DO UPDATE SET "
            "params_schema = excluded.params_schema",
            (device.id, capability.action_id, json.dumps(capability.params_schema)),
        )
