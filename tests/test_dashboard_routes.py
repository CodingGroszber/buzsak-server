"""Tests for the dashboard Flask routes (UI-01, API-07 error shape parity)."""

from __future__ import annotations

from datetime import datetime, timezone

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.api import create_app
from buzsak_pi3_server.config import (
    AppConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.security.credentials import issue_credential


def _app_config(tmp_path) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3")),
        server=ServerConfig(),
        polling=PollingConfig(),
        devices=(),
    )


def _authorization_headers(config: AppConfig, role: str) -> dict[str, str]:
    conn = db.connect(config.database)
    try:
        _, token = issue_credential(conn, "test-client", role)
    finally:
        conn.close()
    return {"Authorization": f"Bearer {token}"}


def test_index_serves_the_dashboard_shell(tmp_path):
    app = create_app(_app_config(tmp_path))
    client = app.test_client()

    insecure = client.get("/")
    response = client.get("/", base_url="https://localhost")
    login = client.get("/login", base_url="https://localhost")

    assert insecure.status_code == 426
    assert response.status_code == 303
    assert response.headers["Location"].endswith("/login")
    assert login.status_code == 200
    assert b"Access token" in login.data


def test_browser_token_login_uses_revocable_session_and_csrf(tmp_path):
    config = _app_config(tmp_path)
    connection = db.connect(config.database)
    schema.apply_migrations(connection)
    _, token = issue_credential(connection, "browser-owner", "operator")
    connection.close()
    client = create_app(config).test_client()

    login_page = client.get("/login", base_url="https://localhost")
    import re
    csrf_token = re.search(
        rb'name="csrf_token" value="([^"]+)"', login_page.data).group(1).decode()
    logged_in = client.post(
        "/login",
        data={"csrf_token": csrf_token, "token": token},
        base_url="https://localhost",
    )
    dashboard = client.get("/", base_url="https://localhost")
    csrf = re.search(
        rb'<meta name="csrf-token" content="([^"]+)"', dashboard.data).group(1).decode()
    logout_without_csrf = client.post("/logout", base_url="https://localhost")
    logout = client.post(
        "/logout",
        headers={"X-CSRF-Token": csrf},
        base_url="https://localhost",
    )

    assert logged_in.status_code == 303
    assert dashboard.status_code == 200
    assert b"Sign out" in dashboard.data
    assert logout_without_csrf.status_code == 403
    assert logout.status_code == 303


def test_state_endpoint_returns_the_three_fixed_parties(tmp_path):
    config = _app_config(tmp_path)
    conn = db.connect(config.database)
    schema.apply_migrations(conn)
    conn.close()

    app = create_app(config)
    client = app.test_client()

    response = client.get(
        "/api/dashboard/state",
        headers=_authorization_headers(config, "viewer"),
        base_url="https://localhost",
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert [party["label"] for party in payload["parties"]] == [
        "PUMP",
        "GREENHOUSE",
        "GARAGE",
    ]
    assert "generated_at" in payload


def test_state_endpoint_does_not_disclose_schema_state_to_anonymous_clients(tmp_path):
    # Authentication runs before the handler, so anonymous callers learn
    # nothing about whether the application schema has been initialized.
    app = create_app(_app_config(tmp_path))
    client = app.test_client()

    response = client.get(
        "/api/dashboard/state", base_url="https://localhost")

    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == "unauthenticated"


def test_pulse_endpoint_accepts_a_valid_request(tmp_path):
    config = AppConfig(
        database=DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3")),
        server=ServerConfig(),
        polling=PollingConfig(),
        devices=(
            DeviceConfig(id="sonoff-1", kind="sonoff-minid",
                         address="ws://192.168.1.95:5580/ws#node_id=1", enabled=True),
        ),
    )
    conn = db.connect(config.database)
    schema.apply_migrations(conn)
    conn.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )
    conn.execute(
        "INSERT INTO capabilities (device_id, action_id, params_schema) "
        "VALUES ('sonoff-1', 'pulse', '{}')"
    )
    observed_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES ('sonoff-1', 'healthy', ?, 0, ?)",
        (observed_at, observed_at),
    )
    conn.execute(
        "INSERT INTO parameters (device_id, parameter_id, category) "
        "VALUES ('sonoff-1', 'on_off', 'boolean')"
    )
    conn.execute(
        "INSERT INTO observations_latest "
        "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
        "VALUES ('sonoff-1', 'on_off', 'false', 'bool', 'good', ?, ?, 1)",
        (observed_at, observed_at),
    )
    conn.close()

    app = create_app(config)
    client = app.test_client()

    headers = _authorization_headers(config, "operator")
    response = client.post(
        "/api/dashboard/devices/sonoff-1/pulse",
        headers=headers,
        base_url="https://localhost",
    )

    assert response.status_code == 202
    assert response.get_json()["status"] == "accepted"


def test_pulse_endpoint_rejects_an_unknown_device(tmp_path):
    config = _app_config(tmp_path)
    conn = db.connect(config.database)
    schema.apply_migrations(conn)
    conn.close()

    app = create_app(config)
    client = app.test_client()

    response = client.post(
        "/api/dashboard/devices/does-not-exist/pulse",
        headers=_authorization_headers(config, "operator"),
        base_url="https://localhost",
    )

    assert response.status_code == 409
    assert response.get_json()["status"] == "rejected"


def test_state_endpoint_rejects_anonymous_and_plain_http_requests(tmp_path):
    config = _app_config(tmp_path)
    conn = db.connect(config.database)
    schema.apply_migrations(conn)
    conn.close()
    client = create_app(config).test_client()

    anonymous = client.get("/api/dashboard/state",
                           base_url="https://localhost")
    insecure = client.get(
        "/api/dashboard/state",
        headers=_authorization_headers(config, "viewer"),
    )

    assert anonymous.status_code == 401
    assert anonymous.get_json()["error"]["code"] == "unauthenticated"
    assert insecure.status_code == 426


def test_viewer_cannot_enqueue_a_pulse(tmp_path):
    config = AppConfig(
        database=DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3")),
        server=ServerConfig(),
        polling=PollingConfig(),
        devices=(),
    )
    conn = db.connect(config.database)
    schema.apply_migrations(conn)
    conn.close()
    client = create_app(config).test_client()

    response = client.post(
        "/api/dashboard/devices/sonoff-1/pulse",
        headers=_authorization_headers(config, "viewer"),
        base_url="https://localhost",
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "forbidden"
