"""One-attempt valve command dispatcher and telemetry reconciliation (CMD-05..10)."""

from __future__ import annotations

import json
import logging
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from buzsak_pi3_server import db
from buzsak_pi3_server.commands.service import (
    CommandRejected,
    _OUTPUT_PARAMETERS,
    _action_parameters,
    _validate_fresh_state,
)
from buzsak_pi3_server.clock import Clock
from buzsak_pi3_server.config import AppConfig

logger = logging.getLogger(__name__)

ConnectionFactory = Callable[[], sqlite3.Connection]
_MAX_ERROR_LENGTH = 300


@dataclass(frozen=True)
class ClaimedCommand:
    command_id: str
    actor_id: str
    device_id: str
    address: str
    action_id: str
    params: dict
    expires_at: str
    claimed_at: datetime


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit(connection: sqlite3.Connection, command_id: str, actor_id: str, event: str,
           now: str, details: dict) -> None:
    connection.execute(
        "INSERT INTO command_audit_events "
        "(command_id, actor_id, event_type, occurred_at, details_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (command_id, actor_id, event, now, json.dumps(details, sort_keys=True)),
    )


def claim_one_pending(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    *,
    clock: Clock | None = None,
) -> tuple[bool, ClaimedCommand | None]:
    """Durably claim one FIFO command and record its attempt before I/O."""
    now = (clock or Clock()).utc_now()
    now_text = _iso(now)
    processed_expired = False
    connection.execute("BEGIN IMMEDIATE")
    try:
        expired = connection.execute(
            "SELECT command_id, actor_id FROM commands "
            "WHERE state = 'pending' AND expires_at <= ?",
            (now_text,),
        ).fetchall()
        for command_id, actor_id in expired:
            connection.execute(
                "UPDATE commands SET state = 'expired', updated_at = ?, reason = 'deadline_expired' "
                "WHERE command_id = ? AND state = 'pending'",
                (now_text, command_id),
            )
            _audit(connection, command_id, actor_id, "expired",
                   now_text, {"reason": "deadline_expired"})
            processed_expired = True

        if not app_config.control.valve_enabled:
            connection.execute("COMMIT")
            return processed_expired, None

        row = connection.execute(
            "SELECT c.command_id, c.actor_id, c.device_id, d.address, c.action_id, "
            "c.params_json, c.preconditions_json, c.created_at, c.expires_at "
            "FROM commands c JOIN devices d ON d.id = c.device_id "
            "WHERE c.state = 'pending' AND NOT EXISTS ("
            "SELECT 1 FROM commands active WHERE active.device_id = c.device_id "
            "AND active.state IN ('dispatching', 'sent', 'acknowledged', 'uncertain')) "
            "ORDER BY c.acceptance_sequence LIMIT 1"
        ).fetchone()
        if row is None:
            connection.execute("COMMIT")
            return processed_expired, None

        command_id, actor_id, device_id, address, action_id, params_json, preconditions_json, created_at, expires_at = row
        if now < _parse_iso(created_at):
            connection.execute(
                "UPDATE commands SET state = 'failed', updated_at = ?, reason = 'clock_untrusted' "
                "WHERE command_id = ?",
                (now_text, command_id),
            )
            _audit(connection, command_id, actor_id, "failed",
                   now_text, {"reason": "clock_untrusted"})
            connection.execute("COMMIT")
            return True, None
        if _parse_iso(expires_at) <= now:
            connection.execute(
                "UPDATE commands SET state = 'expired', updated_at = ?, reason = 'deadline_expired' "
                "WHERE command_id = ?",
                (now_text, command_id),
            )
            _audit(connection, command_id, actor_id, "expired",
                   now_text, {"reason": "deadline_expired"})
            connection.execute("COMMIT")
            return True, None

        params = json.loads(params_json)
        required = _action_parameters(action_id, params)
        try:
            fresh = _validate_fresh_state(
                connection, app_config, device_id, action_id, params, {}, required, now)
            submitted = json.loads(preconditions_json)["observed_revisions"]
            if fresh["observed_revisions"] != submitted:
                raise CommandRejected(
                    "stale_revision", "device state changed after acceptance")
        except (CommandRejected, KeyError, ValueError) as exc:
            reason = exc.code if isinstance(
                exc, CommandRejected) else "precondition_failed"
            connection.execute(
                "UPDATE commands SET state = 'failed', updated_at = ?, reason = ? "
                "WHERE command_id = ?",
                (now_text, reason, command_id),
            )
            _audit(connection, command_id, actor_id,
                   "failed", now_text, {"reason": reason})
            connection.execute("COMMIT")
            return True, None

        connection.execute(
            "UPDATE commands SET state = 'dispatching', updated_at = ?, attempt_count = 1 "
            "WHERE command_id = ? AND state = 'pending'",
            (now_text, command_id),
        )
        request_json = json.dumps(
            {"action_id": action_id, "params": params}, sort_keys=True)
        connection.execute(
            "INSERT INTO command_attempts "
            "(command_id, attempt_number, state, started_at, request_json) "
            "VALUES (?, 1, 'started', ?, ?)",
            (command_id, now_text, request_json),
        )
        _audit(connection, command_id, actor_id,
               "dispatching", now_text, {"attempt": 1})
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
    return True, ClaimedCommand(
        command_id, actor_id, device_id, address, action_id, params, expires_at, now)


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _device_url(address: str, action_id: str, params: dict) -> str:
    if action_id == "set_output":
        query = urllib.parse.urlencode({
            "name": params["name"],
            "state": "1" if params["state"] else "0",
        })
        return urllib.parse.urljoin(address, "api/control") + "?" + query
    query = urllib.parse.urlencode({"value": params["value"]})
    return urllib.parse.urljoin(address, "api/mode") + "?" + query


def _send(claim: ClaimedCommand, timeout_seconds: float) -> tuple[int | None, str | None]:
    request = urllib.request.Request(
        _device_url(claim.address, claim.action_id, claim.params),
        data=b"",
        headers={"Content-Length": "0"},
        method="POST",
    )
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            return response.status, None
    except urllib.error.HTTPError as exc:
        return exc.code, str(exc.reason)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return None, str(exc)[:_MAX_ERROR_LENGTH]


def _finish_attempt(
    connection: sqlite3.Connection,
    claim: ClaimedCommand,
    *,
    state: str,
    reason: str | None,
    http_status: int | None,
    error: str | None,
    now: datetime,
    confirmation_timeout_seconds: float,
) -> None:
    now_text = _iso(now)
    confirmation_deadline = (
        _iso(now + timedelta(seconds=confirmation_timeout_seconds))
        if state == "acknowledged" else None
    )
    response_json = json.dumps(
        {"http_status": http_status}) if http_status is not None else None
    connection.execute("BEGIN IMMEDIATE")
    try:
        connection.execute(
            "UPDATE commands SET state = ?, updated_at = ?, reason = ?, "
            "confirmation_deadline = ? WHERE command_id = ?",
            (state, now_text, reason, confirmation_deadline, claim.command_id),
        )
        connection.execute(
            "UPDATE command_attempts SET state = ?, completed_at = ?, "
            "response_json = ?, error = ? WHERE command_id = ? AND attempt_number = 1",
            (
                "failed" if state == "expired" else state,
                now_text,
                response_json,
                error[:_MAX_ERROR_LENGTH] if error else None,
                claim.command_id,
            ),
        )
        _audit(connection, claim.command_id, claim.actor_id, state, now_text, {
            "http_status": http_status,
            "reason": reason,
        })
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


def execute_one_pending(
    app_config: AppConfig,
    *,
    connection_factory: ConnectionFactory | None = None,
    clock: Clock | None = None,
) -> bool:
    """Claims at most one command, then performs one device transmission."""
    connection_factory = connection_factory or (
        lambda: db.connect(app_config.database))
    connection = connection_factory()
    try:
        processed, claim = claim_one_pending(
            connection, app_config, clock=clock)
    finally:
        connection.close()
    if claim is None:
        return processed

    now = (clock or Clock()).utc_now()
    if now < claim.claimed_at:
        status, error, state, reason = None, None, "failed", "clock_untrusted"
    elif now >= _parse_iso(claim.expires_at):
        status, error, state, reason = None, None, "expired", "deadline_expired"
    else:
        try:
            status, error = _send(
                claim, app_config.polling.request_timeout_seconds)
            if status == 200:
                state, reason = "acknowledged", None
            elif status == 409:
                state, reason = "failed", "device_interlock"
            elif status is not None and 400 <= status < 500:
                state, reason = "failed", "device_rejected"
            elif status is not None:
                state, reason = "uncertain", "ambiguous_device_response"
            else:
                state, reason = "uncertain", "transport_outcome_ambiguous"
        except Exception as exc:
            logger.exception(
                "unexpected dispatcher error for command %s", claim.command_id)
            status, error, state, reason = None, str(
                exc), "uncertain", "transport_outcome_ambiguous"

    connection = connection_factory()
    try:
        _finish_attempt(
            connection,
            claim,
            state=state,
            reason=reason,
            http_status=status,
            error=error,
            now=now,
            confirmation_timeout_seconds=app_config.control.confirmation_timeout_seconds,
        )
    finally:
        connection.close()
    return True


def recover_interrupted_commands(
    connection: sqlite3.Connection,
    *,
    clock: Clock | None = None,
) -> int:
    """Quarantine claims that may have transmitted before a process restart."""
    now_text = _iso((clock or Clock()).utc_now())
    rows = connection.execute(
        "SELECT command_id, actor_id FROM commands "
        "WHERE state IN ('dispatching', 'sent')"
    ).fetchall()
    if not rows:
        return 0
    connection.execute("BEGIN IMMEDIATE")
    try:
        for command_id, actor_id in rows:
            connection.execute(
                "UPDATE commands SET state = 'uncertain', updated_at = ?, "
                "reason = 'interrupted_dispatch' WHERE command_id = ?",
                (now_text, command_id),
            )
            connection.execute(
                "UPDATE command_attempts SET state = 'uncertain', completed_at = ?, "
                "error = 'dispatcher restarted during possible transmission' "
                "WHERE command_id = ? AND state = 'started'",
                (now_text, command_id),
            )
            _audit(connection, command_id, actor_id, "uncertain", now_text, {
                "reason": "interrupted_dispatch",
            })
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
    return len(rows)


def _confirmation_snapshot(
    connection: sqlite3.Connection,
    device_id: str,
    parameter_ids: tuple[str, ...],
    attempt_started_at: str,
    *,
    now: datetime,
    stale_after_seconds: float,
) -> dict[str, object] | None:
    rows = connection.execute(
        "SELECT parameter_id, value, quality, observed_at FROM observations_latest "
        "WHERE device_id = ? AND parameter_id IN (" +
        ",".join("?" for _ in parameter_ids) + ")",
        (device_id, *parameter_ids),
    ).fetchall()
    values = {row[0]: row[1] for row in rows}
    if len(rows) != len(parameter_ids) or any(
        row[2] != "good"
        or row[1] is None
        or row[3] <= attempt_started_at
        or (now - _parse_iso(row[3])).total_seconds() < -2.0
        or (now - _parse_iso(row[3])).total_seconds() > stale_after_seconds
        for row in rows
    ):
        return None
    return {"observed_at": max(row[3] for row in rows), "values": values}


def reconcile_acknowledged(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    *,
    clock: Clock | None = None,
) -> int:
    """Confirm from post-attempt poller observations or mark timed-out work uncertain."""
    now = (clock or Clock()).utc_now()
    now_text = _iso(now)
    commands = connection.execute(
        "SELECT c.command_id, c.actor_id, c.device_id, c.action_id, c.params_json, "
        "c.confirmation_deadline, a.started_at "
        "FROM commands c JOIN command_attempts a ON a.command_id = c.command_id "
        "WHERE c.state = 'acknowledged' AND a.attempt_number = 1"
    ).fetchall()
    changed = 0
    for command_id, actor_id, device_id, action_id, params_json, deadline, started_at in commands:
        device_config = next(
            d for d in app_config.devices if d.id == device_id)
        interval = device_config.poll_interval_seconds or app_config.polling.default_interval_seconds
        stale_after = interval * app_config.polling.stale_multiplier
        params = json.loads(params_json)
        if action_id == "set_mode":
            parameter_ids = ("mode", "relay1_mist",
                             "relay2_rain", "relay3_drip", "relay4_light")
        else:
            output = _OUTPUT_PARAMETERS[params["name"]]
            parameter_ids = ("mode", output[0], output[2])
        snapshot = _confirmation_snapshot(
            connection, device_id, parameter_ids, started_at,
            now=now, stale_after_seconds=stale_after)
        confirmed = False
        if snapshot is not None:
            values = snapshot["values"]
            if action_id == "set_mode":
                confirmed = (
                    values["mode"] == params["value"]
                    and all(values[key] == "false" for key in parameter_ids[1:])
                )
            else:
                expected_state = "true" if params["state"] else "false"
                confirmed = values[parameter_ids[1]] == expected_state and (
                    values["mode"] == "manual" or values[parameter_ids[2]] == "true"
                )
        if confirmed:
            state, reason = "confirmed", None
        elif _parse_iso(deadline) <= now:
            state, reason = "uncertain", "confirmation_timeout"
        else:
            continue

        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "UPDATE commands SET state = ?, updated_at = ?, reason = ?, "
                "confirmation_json = ? WHERE command_id = ? AND state = 'acknowledged'",
                (
                    state,
                    now_text,
                    reason,
                    json.dumps(
                        snapshot, sort_keys=True) if snapshot is not None else None,
                    command_id,
                ),
            )
            connection.execute(
                "UPDATE command_attempts SET state = ?, completed_at = ? "
                "WHERE command_id = ? AND attempt_number = 1",
                ("reconciled" if state == "confirmed" else state, now_text, command_id),
            )
            _audit(connection, command_id, actor_id,
                   state, now_text, {"reason": reason})
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
        changed += 1
    return changed


def reconcile_uncertain(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    *,
    clock: Clock | None = None,
) -> int:
    """Resolve ambiguous attempts only when later telemetry positively confirms them."""
    now = (clock or Clock()).utc_now()
    now_text = _iso(now)
    commands = connection.execute(
        "SELECT c.command_id, c.actor_id, c.device_id, c.action_id, c.params_json, a.started_at "
        "FROM commands c JOIN command_attempts a ON a.command_id = c.command_id "
        "WHERE c.state = 'uncertain' AND a.attempt_number = 1"
    ).fetchall()
    changed = 0
    for command_id, actor_id, device_id, action_id, params_json, started_at in commands:
        device_config = next(
            d for d in app_config.devices if d.id == device_id)
        interval = device_config.poll_interval_seconds or app_config.polling.default_interval_seconds
        stale_after = interval * app_config.polling.stale_multiplier
        params = json.loads(params_json)
        if action_id == "set_mode":
            parameter_ids = ("mode", "relay1_mist",
                             "relay2_rain", "relay3_drip", "relay4_light")
        else:
            output = _OUTPUT_PARAMETERS[params["name"]]
            parameter_ids = ("mode", output[0], output[2])
        snapshot = _confirmation_snapshot(
            connection, device_id, parameter_ids, started_at,
            now=now, stale_after_seconds=stale_after)
        if snapshot is None:
            continue
        values = snapshot["values"]
        if action_id == "set_mode":
            confirmed = (
                values["mode"] == params["value"]
                and all(values[key] == "false" for key in parameter_ids[1:])
            )
        else:
            expected_state = "true" if params["state"] else "false"
            confirmed = values[parameter_ids[1]] == expected_state and (
                values["mode"] == "manual" or values[parameter_ids[2]] == "true"
            )
        if not confirmed:
            continue

        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.execute(
                "UPDATE commands SET state = 'confirmed', updated_at = ?, reason = NULL, "
                "confirmation_json = ? WHERE command_id = ? AND state = 'uncertain'",
                (
                    now_text,
                    json.dumps(snapshot, sort_keys=True),
                    command_id,
                ),
            )
            connection.execute(
                "UPDATE command_attempts SET state = 'reconciled', completed_at = ? "
                "WHERE command_id = ? AND attempt_number = 1",
                (now_text, command_id),
            )
            _audit(connection, command_id, actor_id, "reconciled_confirmed", now_text, {
                "source": "fresh_poller_telemetry",
            })
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
        changed += 1
    return changed


def process_reconciliation_requests(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    *,
    clock: Clock | None = None,
) -> int:
    """Apply admin reconciliation requests while retaining dispatcher lifecycle ownership."""
    now = (clock or Clock()).utc_now()
    now_text = _iso(now)
    requests = connection.execute(
        "SELECT r.request_id, r.command_id, r.actor_id, r.resolution, r.note, "
        "c.device_id, c.action_id, c.params_json, c.state, a.started_at "
        "FROM command_reconciliation_requests r "
        "JOIN commands c ON c.command_id = r.command_id "
        "JOIN command_attempts a ON a.command_id = c.command_id AND a.attempt_number = 1 "
        "WHERE r.status = 'pending' ORDER BY r.requested_at"
    ).fetchall()
    processed = 0
    for request_id, command_id, actor_id, resolution, note, device_id, action_id, params_json, state, started_at in requests:
        result_reason = None
        final_state = resolution if resolution == "failed" else "confirmed"
        snapshot = None
        if state != "uncertain":
            result_reason = "command is no longer uncertain"
        elif resolution == "confirmed":
            params = json.loads(params_json)
            if action_id == "set_mode":
                parameter_ids = (
                    "mode", "relay1_mist", "relay2_rain", "relay3_drip", "relay4_light")
            else:
                output = _OUTPUT_PARAMETERS[params["name"]]
                parameter_ids = ("mode", output[0], output[2])
            device_config = next(
                d for d in app_config.devices if d.id == device_id)
            interval = device_config.poll_interval_seconds or app_config.polling.default_interval_seconds
            snapshot = _confirmation_snapshot(
                connection,
                device_id,
                parameter_ids,
                started_at,
                now=now,
                stale_after_seconds=interval * app_config.polling.stale_multiplier,
            )
            if snapshot is None:
                result_reason = "fresh post-attempt telemetry is unavailable"
            elif action_id == "set_mode":
                values = snapshot["values"]
                matches = (
                    values["mode"] == params["value"]
                    and all(values[key] == "false" for key in parameter_ids[1:])
                )
                if not matches:
                    result_reason = "fresh telemetry does not satisfy the set_mode confirmation rule"
            else:
                values = snapshot["values"]
                expected_state = "true" if params["state"] else "false"
                matches = values[parameter_ids[1]] == expected_state and (
                    values["mode"] == "manual" or values[parameter_ids[2]] == "true"
                )
                if not matches:
                    result_reason = "fresh telemetry does not satisfy the set_output confirmation rule"

        connection.execute("BEGIN IMMEDIATE")
        try:
            if result_reason is None:
                connection.execute(
                    "UPDATE commands SET state = ?, updated_at = ?, reason = ?, "
                    "outcome_json = ?, confirmation_json = ? WHERE command_id = ? AND state = 'uncertain'",
                    (
                        final_state,
                        now_text,
                        "admin_reconciled_failed" if final_state == "failed" else None,
                        json.dumps(
                            {"admin_note": note, "reconciled_by": actor_id}, sort_keys=True),
                        json.dumps(snapshot, sort_keys=True)
                        if final_state == "confirmed" else None,
                        command_id,
                    ),
                )
                connection.execute(
                    "UPDATE command_attempts SET state = ?, completed_at = ? "
                    "WHERE command_id = ? AND attempt_number = 1",
                    ("reconciled" if final_state ==
                     "confirmed" else "failed", now_text, command_id),
                )
                _audit(connection, command_id, actor_id, f"admin_{final_state}", now_text, {
                    "request_id": request_id,
                    "note": note,
                    "evidence": snapshot,
                })
                request_status = "applied"
            else:
                _audit(connection, command_id, actor_id, "reconciliation_rejected", now_text, {
                    "request_id": request_id,
                    "reason": result_reason,
                })
                request_status = "rejected"
            connection.execute(
                "UPDATE command_reconciliation_requests SET status = ?, result_reason = ? "
                "WHERE request_id = ? AND status = 'pending'",
                (request_status, result_reason, request_id),
            )
        except Exception:
            connection.execute("ROLLBACK")
            raise
        else:
            connection.execute("COMMIT")
        processed += 1
    return processed
