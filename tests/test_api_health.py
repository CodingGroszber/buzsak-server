from __future__ import annotations

import pytest

from buzsak_pi3_server.api import create_app
from buzsak_pi3_server.config import AppConfig, DatabaseConfig, PollingConfig, ServerConfig


@pytest.fixture
def app_config(tmp_path):
    return AppConfig(
        database=DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3")),
        server=ServerConfig(),
        polling=PollingConfig(),
        devices=(),
    )


@pytest.fixture
def client(app_config):
    app = create_app(app_config)
    app.testing = True
    return app.test_client()


def test_liveness_ok(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_readiness_ok_when_db_reachable(client):
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"
