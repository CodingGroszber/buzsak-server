"""Durable acceptance and validation for valve desired-state commands (CMD-01..04)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from buzsak_pi3_server.adapters.registry import canonical_kind
from buzsak_pi3_server.clock import Clock
from buzsak_pi3_server.config import AppConfig
from buzsak_pi3_server.security.credentials import Principal

_VALVE_KIND = "valve_controller"
_ACTIVE_STATES = ("dispatching", "sent", "acknowledged", "uncertain")
_OUTPUT_PARAMETERS = {
    "relay1": ("relay1_mist", "relay1_controllable", "relay1_always_manual"),
    "relay2": ("relay2_rain", "relay2_controllable", "relay2_always_manual"),
    "relay3": ("relay3_drip", "relay3_controllable", "relay3_always_manual"),
    "relay4": ("relay4_light", "relay4_controllable", "relay4_always_manual"),
}


class CommandRejected(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


@dataclass(frozen=True)
class AcceptedCommand:
    command_id: str
    state: str
    expires_at: str
    deduplicated: bool = False


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _request_fingerprint(
    device_id: str,
    action_id: str,
    params: dict[str, Any],
    client_origin: str,
    expected_revisions: dict[str, int],
) -> str:
    encoded = json.dumps(
        {
            "device_id": device_id,
            "action_id": action_id,
            "params": params,
            "client_origin": client_origin,
            "expected_revisions": expected_revisions,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _action_parameters(action_id: str, params: Any) -> tuple[str, ...]:
    if not isinstance(params, dict):
        raise CommandRejected(
            "invalid_request", "params must be an object", 400)
    if action_id == "set_output":
        if set(params) != {"name", "state"}:
            raise CommandRejected(
                "invalid_request", "set_output requires name and state", 400)
        name, state = params["name"], params["state"]
        if not isinstance(name, str) or name not in _OUTPUT_PARAMETERS or type(state) is not bool:
            raise CommandRejected(
                "invalid_request", "invalid output name or state", 400)
        return ("mode", *_OUTPUT_PARAMETERS[name])
    if action_id == "set_mode":
        if (
            set(params) != {"value"}
            or not isinstance(params["value"], str)
            or params["value"] not in {"manual", "automatic"}
        ):
            raise CommandRejected(
                "invalid_request", "set_mode value must be manual or automatic", 400)
        return ("mode", *[ids[0] for ids in _OUTPUT_PARAMETERS.values()])
    raise CommandRejected("invalid_request", "unsupported action", 400)


def _fresh_value(
    connection: sqlite3.Connection,
    device_id: str,
    parameter_id: str,
    now: datetime,
    stale_after_seconds: float,
) -> tuple[str, int]:
    row = connection.execute(
        "SELECT value, value_type, quality, observed_at, revision "
        "FROM observations_latest WHERE device_id = ? AND parameter_id = ?",
        (device_id, parameter_id),
    ).fetchone()
    if row is None or row[2] != "good" or row[0] is None:
        raise CommandRejected(
            "stale_telemetry", f"fresh valid {parameter_id} telemetry is required", 409)
    age_seconds = (now - _parse_iso(row[3])).total_seconds()
    if age_seconds < -2.0:
        raise CommandRejected(
            "clock_untrusted", f"{parameter_id} timestamp is in the future", 503)
    if age_seconds > stale_after_seconds:
        raise CommandRejected(
            "stale_telemetry", f"{parameter_id} telemetry is stale", 409)
    return row[0], row[4]


def _validate_fresh_state(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    device_id: str,
    action_id: str,
    params: dict[str, Any],
    expected_revisions: dict[str, int],
    required_parameters: tuple[str, ...],
    now: datetime,
) -> dict[str, Any]:
    device_config = next(d for d in app_config.devices if d.id == device_id)
    interval = device_config.poll_interval_seconds or app_config.polling.default_interval_seconds
    stale_after = interval * app_config.polling.stale_multiplier

    health_row = connection.execute(
        "SELECT status, last_success_at, consecutive_failures FROM device_health "
        "WHERE device_id = ?",
        (device_id,),
    ).fetchone()
    health_age_seconds = (
        (now - _parse_iso(health_row[1])).total_seconds()
        if health_row is not None and health_row[1] is not None else None
    )
    if (
        health_row is None
        or health_row[0] != "healthy"
        or health_row[1] is None
        or health_row[2] != 0
        or health_age_seconds < -2.0
        or health_age_seconds > stale_after
    ):
        raise CommandRejected("device_unavailable",
                              "device is not healthy with fresh telemetry", 503)

    if set(expected_revisions) - set(required_parameters):
        raise CommandRejected(
            "invalid_request", "expected_revisions contains an unrelated parameter", 400)

    observed: dict[str, tuple[str, int]] = {
        parameter_id: _fresh_value(
            connection, device_id, parameter_id, now, stale_after)
        for parameter_id in required_parameters
    }
    for parameter_id, expected in expected_revisions.items():
        if type(expected) is not int or expected < 0:
            raise CommandRejected(
                "invalid_request", "revision values must be non-negative integers", 400)
        if observed[parameter_id][1] != expected:
            raise CommandRejected(
                "stale_revision", f"{parameter_id} revision changed", 409)

    mode = observed["mode"][0]
    if action_id == "set_output":
        name = params["name"]
        _, controllable_id, always_manual_id = _OUTPUT_PARAMETERS[name]
        if observed[controllable_id][0] != "true":
            raise CommandRejected(
                "forbidden", "output is not controllable", 403)
        always_manual = observed[always_manual_id][0] == "true"
        if mode != "manual" and not always_manual:
            raise CommandRejected("precondition_failed",
                                  "output requires manual mode", 409)
    return {
        "observed_revisions": {key: value[1] for key, value in observed.items()},
        "observed_mode": mode,
    }


def submit_command(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    principal: Principal,
    *,
    device_id: str,
    action_id: str,
    params: Any,
    idempotency_key: str,
    client_origin: str,
    expected_revisions: Any = None,
    clock: Clock | None = None,
) -> AcceptedCommand:
    if principal.role not in {"operator", "admin"}:
        raise CommandRejected("forbidden", "operator role is required", 403)
    if not app_config.control.valve_enabled:
        raise CommandRejected(
            "control_disabled", "valve control is disabled by deployment configuration", 403)
    device_config = next(
        (d for d in app_config.devices if d.id == device_id), None)
    if (
        device_config is None
        or not device_config.enabled
        or canonical_kind(device_config.kind) != _VALVE_KIND
    ):
        raise CommandRejected(
            "not_found", "unknown or disabled valve device", 404)
    if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
        raise CommandRejected(
            "invalid_request", "idempotency_key must contain 1 to 128 characters", 400)
    if not isinstance(client_origin, str) or not 1 <= len(client_origin) <= 64:
        raise CommandRejected(
            "invalid_request", "client_origin must contain 1 to 64 characters", 400)
    if not isinstance(params, dict):
        raise CommandRejected(
            "invalid_request", "params must be an object", 400)
    expected_revisions = {} if expected_revisions is None else expected_revisions
    if not isinstance(expected_revisions, dict):
        raise CommandRejected(
            "invalid_request", "expected_revisions must be an object", 400)
    if any(
        not isinstance(parameter_id, str)
        or type(revision) is not int
        or revision < 0
        for parameter_id, revision in expected_revisions.items()
    ):
        raise CommandRejected(
            "invalid_request", "expected revisions must map parameter ids to non-negative integers", 400)

    required_parameters = _action_parameters(action_id, params)
    now = (clock or Clock()).utc_now()
    fingerprint = _request_fingerprint(
        device_id, action_id, params, client_origin, expected_revisions)
    now_text = _iso(now)
    expires_at = _iso(
        now + timedelta(seconds=app_config.control.command_lifetime_seconds))

    connection.execute("BEGIN IMMEDIATE")
    try:
        device_row = connection.execute(
            "SELECT enabled, kind FROM devices WHERE id = ?", (device_id,)
        ).fetchone()
        if device_row is None or not device_row[0] or device_row[1] != _VALVE_KIND:
            raise CommandRejected(
                "not_found", "unknown or disabled valve device", 404)
        capability = connection.execute(
            "SELECT 1 FROM capabilities WHERE device_id = ? AND action_id = ?",
            (device_id, action_id),
        ).fetchone()
        if capability is None:
            raise CommandRejected("unsupported_action",
                                  "device does not support this action", 400)

        duplicate = connection.execute(
            "SELECT request_fingerprint, command_id FROM command_idempotency "
            "WHERE actor_id = ? AND idempotency_key = ?",
            (principal.principal_id, idempotency_key),
        ).fetchone()
        if duplicate is not None:
            if duplicate[0] != fingerprint:
                raise CommandRejected(
                    "idempotency_conflict", "idempotency key was used for a different request", 409)
            command = connection.execute(
                "SELECT state, expires_at FROM commands WHERE command_id = ?",
                (duplicate[1],),
            ).fetchone()
            connection.execute("COMMIT")
            return AcceptedCommand(duplicate[1], command[0], command[1], True)

        preconditions = _validate_fresh_state(
            connection,
            app_config,
            device_id,
            action_id,
            params,
            expected_revisions,
            required_parameters,
            now,
        )
        active = connection.execute(
            "SELECT command_id, state FROM commands WHERE device_id = ? "
            "AND state IN ('dispatching', 'sent', 'acknowledged', 'uncertain') LIMIT 1",
            (device_id,),
        ).fetchone()
        if active is not None:
            raise CommandRejected(
                "conflict", f"device has unresolved command {active[0]}", 409)
        queued = connection.execute(
            "SELECT COUNT(*) FROM commands WHERE device_id = ? AND state = 'pending'",
            (device_id,),
        ).fetchone()[0]
        if queued >= app_config.control.per_device_queue_depth:
            raise CommandRejected(
                "queue_full", "device command queue is full", 503)

        command_id = f"c_{uuid.uuid4().hex}"
        connection.execute(
            "INSERT INTO commands "
            "(command_id, actor_id, client_origin, device_id, action_id, params_json, "
            "idempotency_key, request_fingerprint, state, created_at, updated_at, "
            "expires_at, preconditions_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)",
            (
                command_id,
                principal.principal_id,
                client_origin,
                device_id,
                action_id,
                json.dumps(params, sort_keys=True, separators=(",", ":")),
                idempotency_key,
                fingerprint,
                now_text,
                now_text,
                expires_at,
                json.dumps(preconditions, sort_keys=True,
                           separators=(",", ":")),
            ),
        )
        connection.execute(
            "INSERT INTO command_idempotency "
            "(actor_id, idempotency_key, request_fingerprint, command_id, expires_at) "
            "VALUES (?, ?, ?, ?, NULL)",
            (principal.principal_id, idempotency_key, fingerprint, command_id),
        )
        connection.execute(
            "INSERT INTO command_audit_events "
            "(command_id, actor_id, event_type, occurred_at, details_json) "
            "VALUES (?, ?, 'accepted', ?, ?)",
            (
                command_id,
                principal.principal_id,
                now_text,
                json.dumps({"device_id": device_id,
                           "action_id": action_id}, sort_keys=True),
            ),
        )
        sequence = connection.execute(
            "SELECT last_insert_rowid()").fetchone()[0]
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")
    return AcceptedCommand(command_id, "pending", expires_at)
