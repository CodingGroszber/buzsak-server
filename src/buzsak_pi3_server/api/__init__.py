"""Flask application factory for the web/API process (ARC-03)."""

from __future__ import annotations

import os
import secrets

from flask import Flask

from buzsak_pi3_server.config import AppConfig


def create_app(config: AppConfig) -> Flask:
    app = Flask(__name__)
    session_secret = os.environ.get("BUZSAK_SESSION_SECRET")
    if not session_secret:
        session_secret = secrets.token_urlsafe(32)
        app.logger.warning(
            "BUZSAK_SESSION_SECRET is unset; browser sessions will expire when this process restarts")
    app.secret_key = session_secret
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=True,
        SESSION_COOKIE_SAMESITE="Strict",
        MAX_CONTENT_LENGTH=16 * 1024,
    )
    app.config["APP_CONFIG"] = config

    from buzsak_pi3_server.api.routes.auth import auth_bp
    from buzsak_pi3_server.api.routes.health import health_bp
    from buzsak_pi3_server.api.routes.commands import commands_bp
    from buzsak_pi3_server.dashboard.routes import dashboard_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(health_bp)
    app.register_blueprint(commands_bp)
    app.register_blueprint(dashboard_bp)
    return app
