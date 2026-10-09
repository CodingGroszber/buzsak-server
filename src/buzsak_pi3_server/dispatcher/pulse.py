"""Minimal scoped command-execution exception for the Sonoff MiniD "pulse"
capability (DEV-10, ARC-02).

This intentionally does NOT implement the general command lifecycle
(Section 9 / CMD-01..19): no idempotency keys, no authenticated-actor
tracking, no client-supplied precondition revisions, and above all no
automatic retry. A pulse is claimed and attempted exactly once; a failure
or an ambiguous outcome is terminal (CMD-09, CMD-10) and requires a new,
distinct pulse_requests row (created only by a human clicking the
dashboard button again), never a resend of this one.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from buzsak_pi3_server.config import AppConfig
from buzsak_pi3_server.poller import matter_client
from buzsak_pi3_server.poller.errors import PollTransportError

logger = logging.getLogger(__name__)

# "Itching" pulse: 0.5s on, then a 0.5s device-side re-arm guard before it
# will accept another On command (DEV-10, requirements.md's "one
# activation is 0.5s high and then again low").
_ON_TIME_DECISECONDS = 5
_OFF_WAIT_DECISECONDS = 5


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def claim_one_pending(connection: sqlite3.Connection) -> tuple[int, str] | None:
    """Atomically claims the oldest still-pending, unexpired pulse request,
    marking it 'sent' before any network I/O (ARC-04; CMD-05 spirit: durably
    record an attempt before transmission). Also expires any pending rows
    whose deadline has already passed (CMD-04, CMD-12): an expired request
    is never sent, no matter how long it has been queued.
    """
    now = _iso_now()
    connection.execute("BEGIN IMMEDIATE")
    try:
        row = connection.execute(
            "SELECT id, device_id FROM pulse_requests "
            "WHERE status = 'pending' AND expires_at > ? "
            "ORDER BY requested_at LIMIT 1",
            (now,),
        ).fetchone()
        connection.execute(
            "UPDATE pulse_requests SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at <= ?",
            (now,),
        )
        if row is None:
            connection.execute("COMMIT")
            return None
        request_id, device_id = row
        connection.execute(
            "UPDATE pulse_requests SET status = 'sent' WHERE id = ?",
            (request_id,),
        )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
    return request_id, device_id


def _finish(
    connection: sqlite3.Connection,
    request_id: int,
    status: str,
    *,
    error: str | None = None,
) -> None:
    connection.execute(
        "UPDATE pulse_requests SET status = ?, executed_at = ?, error = ? WHERE id = ?",
        (status, _iso_now(), error, request_id),
    )


def execute_pending_pulse(connection: sqlite3.Connection, app_config: AppConfig) -> bool:
    """Claims and executes at most one pending pulse request.

    Returns True if a request was claimed (regardless of outcome), False if
    the queue was empty, so callers can immediately check for more without
    an idle sleep. Never retries within this call (CMD-09, CMD-10).
    """
    claimed = claim_one_pending(connection)
    if claimed is None:
        return False
    request_id, device_id = claimed

    device = next((d for d in app_config.devices if d.id == device_id), None)
    if device is None:
        logger.error(
            "pulse request %d for device %s failed: device no longer configured",
            request_id, device_id,
        )
        _finish(connection, request_id, "failed",
                error="device no longer configured")
        return True

    try:
        ws_url, node_id = matter_client.parse_device_address(device.address)
        matter_client.send_pulse(
            ws_url,
            node_id,
            on_time_deciseconds=_ON_TIME_DECISECONDS,
            off_wait_deciseconds=_OFF_WAIT_DECISECONDS,
            timeout_seconds=app_config.polling.request_timeout_seconds,
        )
    except PollTransportError as exc:
        logger.error("pulse request %d for device %s failed: %s",
                     request_id, device_id, exc)
        _finish(connection, request_id, "failed", error=str(exc))
        return True

    logger.info("pulse request %d for device %s succeeded",
                request_id, device_id)
    _finish(connection, request_id, "succeeded")
    return True
