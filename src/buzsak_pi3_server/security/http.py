"""HTTP authentication/authorization guards for API routes (SEC-02, SEC-04, SEC-05)."""

from __future__ import annotations

import functools
import hmac
import sqlite3
from collections.abc import Callable
from typing import Any

from flask import current_app, g, jsonify, request, session

from buzsak_pi3_server import db
from buzsak_pi3_server.security.credentials import (
    Principal,
    authenticate_credential_id,
    authenticate_token,
)

_ROLE_LEVEL = {"viewer": 1, "operator": 2, "admin": 3}


def require_role(required_role: str, *, require_https: bool = True) -> Callable:
    """Require a valid bearer credential and minimum role for a route."""
    if required_role not in _ROLE_LEVEL:
        raise ValueError(f"unsupported role: {required_role}")

    def decorate(view: Callable) -> Callable:
        @functools.wraps(view)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            if require_https and not request.is_secure:
                return jsonify({
                    "error": {
                        "code": "https_required",
                        "message": "trusted HTTPS is required",
                    }
                }), 426

            authorization = request.headers.get("Authorization", "")
            scheme, _, token = authorization.partition(" ")
            bearer_token = token.strip() if scheme.lower() == "bearer" else ""
            session_credential_id = session.get(
                "credential_id") if not bearer_token else None
            if not bearer_token and not session_credential_id:
                return _unauthenticated()

            config = current_app.config["APP_CONFIG"]
            connection = db.connect(config.database)
            try:
                principal = (
                    authenticate_token(connection, bearer_token)
                    if bearer_token
                    else authenticate_credential_id(connection, session_credential_id)
                )
            except sqlite3.Error:
                current_app.logger.exception("credential lookup failed")
                return jsonify({
                    "error": {
                        "code": "unavailable",
                        "message": "authentication store unavailable",
                    }
                }), 503
            finally:
                connection.close()

            if principal is None:
                return _unauthenticated()
            if _ROLE_LEVEL[principal.role] < _ROLE_LEVEL[required_role]:
                return jsonify({
                    "error": {
                        "code": "forbidden",
                        "message": "credential role is insufficient",
                    }
                }), 403

            if session_credential_id and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
                expected_csrf = session.get("csrf_token", "")
                provided_csrf = (
                    request.headers.get("X-CSRF-Token", "")
                    or request.form.get("csrf_token", "")
                )
                if not expected_csrf or not hmac.compare_digest(expected_csrf, provided_csrf):
                    return jsonify({
                        "error": {
                            "code": "csrf_failed",
                            "message": "valid CSRF token is required",
                        }
                    }), 403

            g.principal = principal
            g.authenticated_by_session = bool(session_credential_id)
            return view(*args, **kwargs)

        return wrapped

    return decorate


def _unauthenticated() -> tuple[Any, int, dict[str, str]]:
    response = jsonify({
        "error": {
            "code": "unauthenticated",
            "message": "a valid bearer credential is required",
        }
    })
    return response, 401, {"WWW-Authenticate": 'Bearer realm="buzsak"'}


def current_principal() -> Principal:
    """Return the principal installed by :func:`require_role`."""
    return g.principal


def new_csrf_token() -> str:
    import secrets

    return secrets.token_urlsafe(32)
