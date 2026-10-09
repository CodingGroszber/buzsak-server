"""Dashboard HTTP routes: the diagnostic page, its read-only JSON state, and
the one write exception -- enqueuing a Sonoff MiniD pulse request (DEV-10).

The state/index routes only depend on `queries.build_dashboard_state` plus
the app's already-loaded config and never open a device connection (UI-05,
ARC-03). The pulse route enqueues a validated request via
`commands.request_pulse`; it never contacts a device either -- only the
dispatcher process does that (dispatcher/pulse.py).
"""

from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app, jsonify, redirect, render_template, request, session, url_for

from buzsak_pi3_server import db
from buzsak_pi3_server.config import AppConfig
from buzsak_pi3_server.dashboard.commands import PulseRejected, request_pulse
from buzsak_pi3_server.dashboard.queries import build_dashboard_state
from buzsak_pi3_server.security.credentials import authenticate_credential_id
from buzsak_pi3_server.security.http import new_csrf_token
from buzsak_pi3_server.security.http import require_role

dashboard_bp = Blueprint(
    "dashboard",
    __name__,
    template_folder="templates",
    static_folder="static",
    static_url_path="/static/dashboard",
)


@dashboard_bp.get("/")
def index() -> str:
    if not request.is_secure:
        return "trusted HTTPS is required", 426
    credential_id = session.get("credential_id")
    if not credential_id:
        return redirect(url_for("auth.login_page"), code=303)
    config: AppConfig = current_app.config["APP_CONFIG"]
    connection = db.connect(config.database)
    try:
        principal = authenticate_credential_id(connection, credential_id)
    except sqlite3.Error:
        current_app.logger.exception("browser session lookup failed")
        return "authentication service unavailable", 503
    finally:
        connection.close()
    if principal is None:
        session.clear()
        return redirect(url_for("auth.login_page"), code=303)
    csrf_token = session.get("csrf_token") or new_csrf_token()
    session["csrf_token"] = csrf_token
    return render_template("index.html", csrf_token=csrf_token)


@dashboard_bp.get("/api/dashboard/state")
@require_role("viewer")
def state() -> tuple[dict, int]:
    config: AppConfig = current_app.config["APP_CONFIG"]
    try:
        connection = db.connect(config.database)
        try:
            payload = build_dashboard_state(connection, config)
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return jsonify({"status": "unavailable", "reason": str(exc)}), 503
    return jsonify(payload), 200


@dashboard_bp.post("/api/dashboard/devices/<device_id>/pulse")
@require_role("operator")
def pulse(device_id: str) -> tuple[dict, int]:
    config: AppConfig = current_app.config["APP_CONFIG"]
    try:
        connection = db.connect(config.database)
        try:
            request_id = request_pulse(connection, config, device_id)
        finally:
            connection.close()
    except PulseRejected as exc:
        return jsonify({"status": "rejected", "reason": str(exc)}), 409
    except sqlite3.Error as exc:
        return jsonify({"status": "unavailable", "reason": str(exc)}), 503
    return jsonify({"status": "accepted", "request_id": request_id}), 202
