"""WSGI entry point for the production server (API-01: gunicorn, not Flask's dev server).

Run with, e.g.: gunicorn buzsak_pi3_server.api.wsgi:app
"""

from __future__ import annotations

from werkzeug.middleware.proxy_fix import ProxyFix

from buzsak_pi3_server.api import create_app
from buzsak_pi3_server.config import load_config_from_env
from buzsak_pi3_server.logging_setup import configure_logging

configure_logging(service_name="web")
app = create_app(load_config_from_env())
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1)
