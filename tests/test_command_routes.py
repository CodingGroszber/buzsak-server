from __future__ import annotations

from datetime import datetime, timezone

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.api import create_app
from buzsak_pi3_server.config import (
    AppConfig,
    ControlConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.device_registration import sync_devices
from buzsak_pi3_server.security.credentials import issue_credential

_NOW = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _client(tmp_path, *, valve_enabled=True):
    config = AppConfig(
        database=DatabaseConfig(path=str(tmp_path / "commands.sqlite3")),
        server=ServerConfig(),
        polling=PollingConfig(),
        devices=(DeviceConfig(
            id="valve-controller", kind="valve-controller",
            address="http://127.0.0.1:1/", enabled=True),),
        control=ControlConfig(valve_enabled=valve_enabled),
    )
    connection = db.connect(config.database)
    schema.apply_migrations(connection)
    sync_devices(connection, config)
    connection.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES ('valve-controller', 'healthy', ?, 0, ?)", (_NOW, _NOW))
    values = {
        "mode": ("manual", "string"),
        "relay1_mist": ("false", "bool"),
        "relay1_controllable": ("true", "bool"),
        "relay1_always_manual": ("false", "bool"),
        "relay2_rain": ("false", "bool"),
        "relay3_drip": ("false", "bool"),
        "relay4_light": ("false", "bool"),
    }
    for index in range(1, 5):
        values.setdefault(f"relay{index}_controllable", ("true", "bool"))
        values.setdefault(f"relay{index}_always_manual", ("false", "bool"))
    for parameter_id, (value, value_type) in values.items():
        connection.execute(
            "INSERT INTO observations_latest "
            "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
            "VALUES ('valve-controller', ?, ?, ?, 'good', ?, ?, 1)",
            (parameter_id, value, value_type, _NOW, _NOW),
        )
    tokens = {}
    for role in ("viewer", "operator", "admin"):
        _, tokens[role] = issue_credential(connection, f"test-{role}", role)
    connection.close()
    return create_app(config).test_client(), tokens


def _command_body(key="request-1"):
    return {
        "action_id": "set_output",
        "params": {"name": "relay1", "state": True},
        "idempotency_key": key,
        "client_origin": "android-app",
    }


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_operator_can_submit_and_read_own_command(tmp_path):
    client, tokens = _client(tmp_path)
    accepted = client.post(
        "/api/v1/devices/valve-controller/commands",
        json=_command_body(),
        headers=_headers(tokens["operator"]),
        base_url="https://localhost",
    )

    assert accepted.status_code == 202
    payload = accepted.get_json()
    assert payload["status"] == "pending"
    assert payload["status_url"] == f"/api/v1/commands/{payload['command_id']}"

    status = client.get(
        payload["status_url"],
        headers=_headers(tokens["operator"]),
        base_url="https://localhost",
    )
    assert status.status_code == 200
    assert status.get_json()["params"] == {"name": "relay1", "state": True}


def test_command_submission_requires_operator_and_https(tmp_path):
    client, tokens = _client(tmp_path)

    anonymous = client.post(
        "/api/v1/devices/valve-controller/commands", json=_command_body(),
        base_url="https://localhost")
    viewer = client.post(
        "/api/v1/devices/valve-controller/commands", json=_command_body(),
        headers=_headers(tokens["viewer"]), base_url="https://localhost")
    insecure = client.post(
        "/api/v1/devices/valve-controller/commands", json=_command_body(),
        headers=_headers(tokens["operator"]))

    assert anonymous.status_code == 401
    assert viewer.status_code == 403
    assert insecure.status_code == 426


def test_disabled_valve_control_rejects_command(tmp_path):
    client, tokens = _client(tmp_path, valve_enabled=False)

    response = client.post(
        "/api/v1/devices/valve-controller/commands", json=_command_body(),
        headers=_headers(tokens["operator"]), base_url="https://localhost")

    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "control_disabled"


def test_owner_can_cancel_pending_command(tmp_path):
    client, tokens = _client(tmp_path)
    accepted = client.post(
        "/api/v1/devices/valve-controller/commands", json=_command_body(),
        headers=_headers(tokens["operator"]), base_url="https://localhost").get_json()

    response = client.delete(
        accepted["status_url"],
        headers=_headers(tokens["operator"]),
        base_url="https://localhost",
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "cancelled"


def test_only_admin_can_queue_uncertain_command_reconciliation(tmp_path):
    client, tokens = _client(tmp_path)
    accepted = client.post(
        "/api/v1/devices/valve-controller/commands", json=_command_body(),
        headers=_headers(tokens["operator"]), base_url="https://localhost").get_json()
    with client.application.app_context():
        connection = db.connect(
            client.application.config["APP_CONFIG"].database)
        connection.execute(
            "UPDATE commands SET state = 'uncertain', reason = 'transport_outcome_ambiguous' "
            "WHERE command_id = ?", (accepted["command_id"],))
        connection.close()

    viewer_response = client.post(
        f"{accepted['status_url']}/reconcile",
        json={"resolution": "failed", "note": "reviewed"},
        headers=_headers(tokens["operator"]), base_url="https://localhost")
    admin_response = client.post(
        f"{accepted['status_url']}/reconcile",
        json={"resolution": "failed", "note": "reviewed against controller state"},
        headers=_headers(tokens["admin"]), base_url="https://localhost")

    assert viewer_response.status_code == 403
    assert admin_response.status_code == 202
    assert admin_response.get_json()["status"] == "pending"
    connection = db.connect(client.application.config["APP_CONFIG"].database)
    try:
        command_state = connection.execute(
            "SELECT state FROM commands WHERE command_id = ?",
            (accepted["command_id"],),
        ).fetchone()[0]
        request_status = connection.execute(
            "SELECT status FROM command_reconciliation_requests WHERE request_id = ?",
            (admin_response.get_json()["request_id"],),
        ).fetchone()[0]
    finally:
        connection.close()
    assert command_state == "uncertain"
    assert request_status == "pending"
