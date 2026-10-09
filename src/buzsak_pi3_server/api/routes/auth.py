"""HTTPS login and logout for the browser dashboard (SEC-02, SEC-04, SEC-06)."""

from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app, redirect, render_template, request, session, url_for

from buzsak_pi3_server import db
from buzsak_pi3_server.security.credentials import authenticate_token
from buzsak_pi3_server.security.http import new_csrf_token, require_role


auth_bp = Blueprint("auth", __name__, template_folder="../templates")


def _https_required():
    return render_template("login.html", csrf_token="", login_error="Trusted HTTPS is required."), 426


@auth_bp.get("/login")
def login_page():
    if not request.is_secure:
        return _https_required()
    csrf_token = session.get("login_csrf") or new_csrf_token()
    session["login_csrf"] = csrf_token
    return render_template("login.html", csrf_token=csrf_token, login_error=None)


@auth_bp.post("/login")
def login():
    if not request.is_secure:
        return _https_required()
    supplied_csrf = request.form.get("csrf_token", "")
    expected_csrf = session.get("login_csrf", "")
    if not expected_csrf or supplied_csrf != expected_csrf:
        return render_template(
            "login.html", csrf_token=new_csrf_token(),
            login_error="Login form expired. Reload and try again."), 403
    token = request.form.get("token", "")
    config = current_app.config["APP_CONFIG"]
    connection = db.connect(config.database)
    try:
        principal = authenticate_token(connection, token)
    except sqlite3.Error:
        current_app.logger.exception("browser credential lookup failed")
        return render_template(
            "login.html", csrf_token=expected_csrf,
            login_error="Authentication service is unavailable."), 503
    finally:
        connection.close()
    if principal is None:
        return render_template(
            "login.html", csrf_token=expected_csrf,
            login_error="Credential is invalid, expired, or revoked."), 401

    session.clear()
    session["credential_id"] = principal.credential_id
    session["csrf_token"] = new_csrf_token()
    return redirect(url_for("dashboard.index"), code=303)


@auth_bp.post("/logout")
@require_role("viewer")
def logout():
    session.clear()
    return redirect(url_for("auth.login_page"), code=303)
