"""Liveness/readiness endpoints (API-07).

Liveness only proves the process is running. Readiness additionally proves
the database is reachable, distinguishing a running process from a usable
service.
"""

from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app

from buzsak_pi3_server import db
from buzsak_pi3_server.config import AppConfig

health_bp = Blueprint("health", __name__)


@health_bp.get("/healthz")
def liveness() -> tuple[dict, int]:
    return {"status": "ok"}, 200


@health_bp.get("/readyz")
def readiness() -> tuple[dict, int]:
    config: AppConfig = current_app.config["APP_CONFIG"]
    try:
        connection = db.connect(config.database)
        connection.execute("SELECT 1")
        connection.close()
    except sqlite3.Error as exc:
        return {"status": "unavailable", "reason": str(exc)}, 503
    return {"status": "ok"}, 200
