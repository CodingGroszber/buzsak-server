"""Writes poll outcomes to `device_health` (POL-04, ARC-01).

Separate from `observations.py`: this table tracks polling health itself
(success/failure timing, consecutive failures, sanitized last error), not
telemetry values. Each function is one short transaction, matching
`observations.record_observation()`'s pattern (connections use
`isolation_level=None`; see db.py).
"""

from __future__ import annotations

import sqlite3

# After this many consecutive failures a device is reported "offline"
# rather than merely "degraded" (POL-04); tune once real failure patterns
# are observed on the Pi.
OFFLINE_AFTER_CONSECUTIVE_FAILURES = 5

# POL-04 "sanitized last error": cap length so a verbose exception message
# (e.g. a long URL or stack-adjacent text) never grows the row unbounded.
_MAX_ERROR_LENGTH = 300


def _sanitize_error(message: str) -> str:
    return message[:_MAX_ERROR_LENGTH]


def record_poll_success(connection: sqlite3.Connection, device_id: str, *, observed_at: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "INSERT INTO device_health "
            "(device_id, status, last_success_at, last_error, consecutive_failures, updated_at) "
            "VALUES (?, 'healthy', ?, NULL, 0, ?) "
            "ON CONFLICT (device_id) DO UPDATE SET "
            "status = 'healthy', last_success_at = excluded.last_success_at, "
            "last_error = NULL, consecutive_failures = 0, updated_at = excluded.updated_at",
            (device_id, observed_at, observed_at),
        )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def record_poll_failure(
    connection: sqlite3.Connection, device_id: str, *, observed_at: str, error: str
) -> int:
    """Increments the device's failure streak and returns the new count.

    The caller uses the returned count to compute the next backoff delay,
    so backoff magnitude survives a poller restart mid-outage instead of
    resetting to the base delay.
    """
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT consecutive_failures FROM device_health WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        consecutive_failures = (row[0] if row else 0) + 1
        status = (
            "offline"
            if consecutive_failures >= OFFLINE_AFTER_CONSECUTIVE_FAILURES
            else "degraded"
        )
        sanitized = _sanitize_error(error)
        connection.execute(
            "INSERT INTO device_health "
            "(device_id, status, last_success_at, last_error, consecutive_failures, updated_at) "
            "VALUES (?, ?, NULL, ?, ?, ?) "
            "ON CONFLICT (device_id) DO UPDATE SET "
            "status = excluded.status, last_error = excluded.last_error, "
            "consecutive_failures = excluded.consecutive_failures, updated_at = excluded.updated_at",
            (device_id, status, sanitized, consecutive_failures, observed_at),
        )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
    return consecutive_failures
