"""Write path for the Sonoff MiniD "pulse" capability (DEV-10, ARC-03).

This is the one deliberate exception to dashboard/queries.py's read-only
rule: it validates a request and atomically enqueues a pulse_requests row.
It never contacts a device itself -- only the dispatcher process does that
(dispatcher/pulse.py) -- preserving ARC-03's "a web/API process ... shall
not directly contact devices on behalf of a browser request" boundary.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from buzsak_pi3_server.adapters.registry import canonical_kind
from buzsak_pi3_server.clock import Clock
from buzsak_pi3_server.config import AppConfig

_MATTER_KIND = "sonoff_minid"
_PULSE_ACTION_ID = "pulse"

# CMD-04 spirit: a bounded, server-authoritative deadline -- a queued pulse
# older than this is expired by the dispatcher, never sent late.
_REQUEST_LIFETIME_SECONDS = 10.0
# Minimum gap between accepted requests for the same device, guarding
# against an accidental double-click firing two pulses back to back.
_COOLDOWN_SECONDS = 3.0


class PulseRejected(Exception):
    """Raised for any validation failure; carries a human-readable reason."""


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def request_pulse(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    device_id: str,
    *,
    clock: Clock | None = None,
) -> int:
    """Validates and enqueues one pulse request (CMD-01 spirit: capability,
    device state, and conflict checks before acceptance). Raises
    PulseRejected for any validation failure. Returns the new
    pulse_requests.id on success.
    """
    clock = clock or Clock()
    now = clock.utc_now()

    device_config = next(
        (d for d in app_config.devices if d.id == device_id), None)
    if device_config is None or not device_config.enabled:
        raise PulseRejected("unknown or disabled device")
    if canonical_kind(device_config.kind) != _MATTER_KIND:
        raise PulseRejected("device does not support the pulse capability")

    connection.execute("BEGIN IMMEDIATE")
    try:
        device_row = connection.execute(
            "SELECT enabled FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
        if device_row is None or not device_row[0]:
            raise PulseRejected("unknown or disabled device")

        has_capability = connection.execute(
            "SELECT 1 FROM capabilities WHERE device_id = ? AND action_id = ?",
            (device_id, _PULSE_ACTION_ID),
        ).fetchone()
        if has_capability is None:
            raise PulseRejected(
                "device does not have the pulse capability registered")

        health_row = connection.execute(
            "SELECT status, last_success_at, consecutive_failures FROM device_health WHERE device_id = ?",
            (device_id,),
        ).fetchone()
        health_status = health_row[0] if health_row else "unknown"
        last_success_at = health_row[1] if health_row else None
        consecutive_failures = health_row[2] if health_row else 0
        interval = device_config.poll_interval_seconds or app_config.polling.default_interval_seconds
        stale_after = interval * app_config.polling.stale_multiplier
        health_age = (
            (now - _parse_iso(last_success_at)).total_seconds()
            if last_success_at else None
        )
        if (
            health_status != "healthy"
            or consecutive_failures > 0
            or health_age is None
            or health_age < -2.0
            or health_age > stale_after
        ):
            raise PulseRejected(
                f"device is not currently healthy with fresh data (status={health_status}); refusing to pulse")

        observation = connection.execute(
            "SELECT quality, observed_at FROM observations_latest "
            "WHERE device_id = ? AND parameter_id = 'on_off'",
            (device_id,),
        ).fetchone()
        if (
            observation is None
            or observation[0] != "good"
            or (now - _parse_iso(observation[1])).total_seconds() < -2.0
            or (now - _parse_iso(observation[1])).total_seconds() > stale_after
        ):
            raise PulseRejected(
                "device telemetry is stale or unavailable; refusing to pulse")

        cutoff = _iso(now - timedelta(seconds=_COOLDOWN_SECONDS))
        conflict = connection.execute(
            "SELECT 1 FROM pulse_requests WHERE "
            "(device_id = ? AND status IN ('pending', 'sent')) OR "
            "(device_id = ? AND requested_at > ?) LIMIT 1",
            (device_id, device_id, cutoff),
        ).fetchone()
        if conflict is not None:
            raise PulseRejected(
                "a pulse is already in flight or was requested too recently for this device")

        requested_at = _iso(now)
        expires_at = _iso(now + timedelta(seconds=_REQUEST_LIFETIME_SECONDS))
        connection.execute(
            "INSERT INTO pulse_requests (device_id, status, requested_at, expires_at) "
            "VALUES (?, 'pending', ?, ?)",
            (device_id, requested_at, expires_at),
        )
        request_id = connection.execute(
            "SELECT last_insert_rowid()").fetchone()[0]
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
    return request_id
