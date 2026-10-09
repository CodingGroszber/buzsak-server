"""Authenticated durable command API (API-03, CMD-01..04, CMD-16)."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from flask import Blueprint, current_app, jsonify, request

from buzsak_pi3_server import db
from buzsak_pi3_server.commands.service import CommandRejected, submit_command
from buzsak_pi3_server.security.http import current_principal, require_role

commands_bp = Blueprint("commands", __name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _decode_json(value: str | None):
    return json.loads(value) if value is not None else None


@commands_bp.post("/api/v1/devices/<device_id>/commands")
@require_role("operator")
def create_command(device_id: str):
    if not request.is_json:
        return jsonify({"error": {"code": "invalid_request", "message": "application/json required"}}), 400
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": {"code": "invalid_request", "message": "JSON object required"}}), 400
    allowed = {"action_id", "params", "idempotency_key",
               "client_origin", "expected_revisions"}
    if set(payload) - allowed or not {"action_id", "params", "idempotency_key", "client_origin"} <= set(payload):
        return jsonify({"error": {"code": "invalid_request", "message": "invalid command fields"}}), 400

    config = current_app.config["APP_CONFIG"]
    connection = db.connect(config.database)
    try:
        accepted = submit_command(
            connection,
            config,
            current_principal(),
            device_id=device_id,
            action_id=payload["action_id"],
            params=payload["params"],
            idempotency_key=payload["idempotency_key"],
            client_origin=payload["client_origin"],
            expected_revisions=payload.get("expected_revisions"),
        )
    except CommandRejected as exc:
        connection.close()
        return jsonify({"error": {"code": exc.code, "message": str(exc)}}), exc.status_code
    except (sqlite3.Error, TypeError, ValueError) as exc:
        current_app.logger.exception("command acceptance failed")
        connection.close()
        return jsonify({"error": {"code": "unavailable", "message": "command service unavailable"}}), 503
    connection.close()
    return jsonify({
        "command_id": accepted.command_id,
        "status": accepted.state,
        "status_url": f"/api/v1/commands/{accepted.command_id}",
        "expires_at": accepted.expires_at,
        "deduplicated": accepted.deduplicated,
    }), 202


@commands_bp.get("/api/v1/commands/<command_id>")
@require_role("viewer")
def command_status(command_id: str):
    principal = current_principal()
    connection = db.connect(current_app.config["APP_CONFIG"].database)
    try:
        row = connection.execute(
            "SELECT command_id, actor_id, client_origin, device_id, action_id, params_json, "
            "state, created_at, updated_at, expires_at, confirmation_json, outcome_json, reason "
            "FROM commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
    except sqlite3.Error:
        current_app.logger.exception("command status lookup failed")
        return jsonify({"error": {"code": "unavailable", "message": "command store unavailable"}}), 503
    finally:
        connection.close()
    if row is None or (row[1] != principal.principal_id and principal.role != "admin"):
        return jsonify({"error": {"code": "not_found", "message": "command not found"}}), 404
    return jsonify({
        "command_id": row[0],
        "device_id": row[3],
        "action_id": row[4],
        "params": _decode_json(row[5]),
        "status": row[6],
        "created_at": row[7],
        "updated_at": row[8],
        "expires_at": row[9],
        "confirmation": _decode_json(row[10]),
        "outcome": _decode_json(row[11]),
        "reason": row[12],
    }), 200


@commands_bp.delete("/api/v1/commands/<command_id>")
@require_role("operator")
def cancel_command(command_id: str):
    principal = current_principal()
    connection = db.connect(current_app.config["APP_CONFIG"].database)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT actor_id, state FROM commands WHERE command_id = ?",
            (command_id,),
        ).fetchone()
        if row is None or (row[0] != principal.principal_id and principal.role != "admin"):
            connection.execute("ROLLBACK")
            return jsonify({"error": {"code": "not_found", "message": "command not found"}}), 404
        if row[1] != "pending":
            connection.execute("ROLLBACK")
            return jsonify({"error": {"code": "conflict", "message": "only pending commands can be cancelled"}}), 409
        now = _iso_now()
        connection.execute(
            "UPDATE commands SET state = 'cancelled', updated_at = ?, reason = 'cancelled' "
            "WHERE command_id = ? AND state = 'pending'",
            (now, command_id),
        )
        connection.execute(
            "INSERT INTO command_audit_events "
            "(command_id, actor_id, event_type, occurred_at, details_json) "
            "VALUES (?, ?, 'cancelled', ?, '{}')",
            (command_id, principal.principal_id, now),
        )
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        current_app.logger.exception("command cancellation failed")
        return jsonify({"error": {"code": "unavailable", "message": "command store unavailable"}}), 503
    finally:
        connection.close()
    return jsonify({"command_id": command_id, "status": "cancelled"}), 200


@commands_bp.post("/api/v1/commands/<command_id>/reconcile")
@require_role("admin")
def request_reconciliation(command_id: str):
    if not request.is_json:
        return jsonify({"error": {"code": "invalid_request", "message": "application/json required"}}), 400
    payload = request.get_json(silent=True)
    if (
        not isinstance(payload, dict)
        or set(payload) != {"resolution", "note"}
    ):
        return jsonify({"error": {"code": "invalid_request", "message": "resolution and a 1-500 character note are required"}}), 400
    resolution = payload["resolution"]
    note = payload["note"]
    if (
        not isinstance(resolution, str)
        or resolution not in {"confirmed", "failed"}
        or not isinstance(note, str)
        or not 1 <= len(note.strip()) <= 500
    ):
        return jsonify({"error": {"code": "invalid_request", "message": "resolution and a 1-500 character note are required"}}), 400

    principal = current_principal()
    connection = db.connect(current_app.config["APP_CONFIG"].database)
    request_id = f"r_{uuid.uuid4().hex}"
    now = _iso_now()
    try:
        connection.execute("BEGIN IMMEDIATE")
        command = connection.execute(
            "SELECT state FROM commands WHERE command_id = ?", (command_id,)
        ).fetchone()
        if command is None:
            connection.execute("ROLLBACK")
            return jsonify({"error": {"code": "not_found", "message": "command not found"}}), 404
        if command[0] != "uncertain":
            connection.execute("ROLLBACK")
            return jsonify({"error": {"code": "conflict", "message": "only uncertain commands can be reconciled"}}), 409
        pending = connection.execute(
            "SELECT 1 FROM command_reconciliation_requests "
            "WHERE command_id = ? AND status = 'pending' LIMIT 1",
            (command_id,),
        ).fetchone()
        if pending:
            connection.execute("ROLLBACK")
            return jsonify({"error": {"code": "conflict", "message": "reconciliation is already pending"}}), 409
        connection.execute(
            "INSERT INTO command_reconciliation_requests "
            "(request_id, command_id, actor_id, resolution, note, requested_at, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (request_id, command_id, principal.principal_id,
             resolution, note.strip(), now),
        )
        connection.execute(
            "INSERT INTO command_audit_events "
            "(command_id, actor_id, event_type, occurred_at, details_json) "
            "VALUES (?, ?, 'reconciliation_requested', ?, ?)",
            (
                command_id,
                principal.principal_id,
                now,
                json.dumps({"request_id": request_id, "resolution": resolution,
                           "note": note.strip()}, sort_keys=True),
            ),
        )
        connection.execute("COMMIT")
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        current_app.logger.exception("command reconciliation request failed")
        return jsonify({"error": {"code": "unavailable", "message": "command store unavailable"}}), 503
    finally:
        connection.close()
    return jsonify({"request_id": request_id, "command_id": command_id, "status": "pending"}), 202
