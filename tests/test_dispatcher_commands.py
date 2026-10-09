from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.api import create_app
from buzsak_pi3_server.commands.service import submit_command
from buzsak_pi3_server.config import (
    AppConfig,
    ControlConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.device_registration import sync_devices
from buzsak_pi3_server.dispatcher.commands import (
    claim_one_pending,
    execute_one_pending,
    process_reconciliation_requests,
    recover_interrupted_commands,
    reconcile_acknowledged,
    reconcile_uncertain,
)
from buzsak_pi3_server.security.credentials import Principal
from buzsak_pi3_server.security.credentials import issue_credential

_NOW = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
_PRINCIPAL = Principal("credential-1", "android-owner", "operator")


class _FixedClock:
    def __init__(self, now=_NOW):
        self._now = now

    def utc_now(self):
        return self._now


class _SequenceClock:
    def __init__(self, *moments):
        self._moments = iter(moments)

    def utc_now(self):
        return next(self._moments)


class _ValveHandler(BaseHTTPRequestHandler):
    status_code = 200
    requests = []

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        type(self).requests.append((self.path, self.command))
        self.send_response(type(self).status_code)
        self.end_headers()
        self.wfile.write(b"{}")


@pytest.fixture
def valve_server():
    _ValveHandler.status_code = 200
    _ValveHandler.requests = []
    server = HTTPServer(("127.0.0.1", 0), _ValveHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _setup(tmp_path, address):
    config = AppConfig(
        database=DatabaseConfig(path=str(tmp_path / "dispatch.sqlite3")),
        server=ServerConfig(),
        polling=PollingConfig(
            default_interval_seconds=1.0, stale_multiplier=3.0),
        devices=(DeviceConfig(
            id="valve-controller", kind="valve-controller", address=address, enabled=True),),
        control=ControlConfig(valve_enabled=True),
    )
    connection = db.connect(config.database)
    schema.apply_migrations(connection)
    sync_devices(connection, config)
    now_text = _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    connection.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES ('valve-controller', 'healthy', ?, 0, ?)", (now_text, now_text))
    values = {
        "mode": ("manual", "string"),
        "relay1_mist": ("false", "bool"),
        "relay1_controllable": ("true", "bool"),
        "relay1_always_manual": ("false", "bool"),
        "relay2_rain": ("false", "bool"),
        "relay2_controllable": ("true", "bool"),
        "relay2_always_manual": ("false", "bool"),
        "relay3_drip": ("false", "bool"),
        "relay3_controllable": ("true", "bool"),
        "relay3_always_manual": ("false", "bool"),
        "relay4_light": ("false", "bool"),
        "relay4_controllable": ("true", "bool"),
        "relay4_always_manual": ("true", "bool"),
    }
    for parameter_id, (value, value_type) in values.items():
        connection.execute(
            "INSERT INTO observations_latest "
            "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
            "VALUES ('valve-controller', ?, ?, ?, 'good', ?, ?, 1)",
            (parameter_id, value, value_type, now_text, now_text))
    connection.close()
    return config


def _enqueue(config, key="cmd-1", *, action="set_output", params=None):
    connection = db.connect(config.database)
    try:
        return submit_command(
            connection,
            config,
            _PRINCIPAL,
            device_id="valve-controller",
            action_id=action,
            params=params or {"name": "relay1", "state": True},
            idempotency_key=key,
            client_origin="android-app",
            clock=_FixedClock(),
        )
    finally:
        connection.close()


def _connection_factory(config):
    return lambda: db.connect(config.database)


def _state(config, parameter_id):
    connection = db.connect(config.database)
    try:
        return connection.execute(
            "SELECT state, reason, confirmation_json FROM commands "
            "WHERE command_id = ?", (parameter_id,)).fetchone()
    finally:
        connection.close()


def _update_observations(config, values, observed_at):
    connection = db.connect(config.database)
    try:
        for parameter_id, value in values.items():
            connection.execute(
                "UPDATE observations_latest SET value = ?, observed_at = ?, quality = 'good' "
                "WHERE device_id = 'valve-controller' AND parameter_id = ?",
                (value, observed_at, parameter_id),
            )
    finally:
        connection.close()


def test_dispatches_once_and_confirms_only_from_post_attempt_observation(tmp_path, valve_server):
    address = f"http://127.0.0.1:{valve_server.server_port}/"
    config = _setup(tmp_path, address)
    accepted = _enqueue(config)

    processed = execute_one_pending(
        config, connection_factory=_connection_factory(config), clock=_FixedClock())

    assert processed is True
    assert _ValveHandler.requests == [
        ("/api/control?name=relay1&state=1", "POST")]
    assert _state(config, accepted.command_id)[0:2] == ("acknowledged", None)

    observed_at = (_NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _update_observations(config, {
        "mode": "manual",
        "relay1_mist": "true",
        "relay1_always_manual": "false",
    }, observed_at)
    connection = db.connect(config.database)
    try:
        assert reconcile_acknowledged(
            connection, config, clock=_FixedClock(_NOW + timedelta(seconds=1))) == 1
    finally:
        connection.close()

    state, reason, evidence = _state(config, accepted.command_id)
    assert (state, reason) == ("confirmed", None)
    assert json.loads(evidence)["values"]["relay1_mist"] == "true"
    assert len(_ValveHandler.requests) == 1


def test_set_mode_requires_mode_readback_and_all_relays_off(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    accepted = _enqueue(
        config, action="set_mode", params={"value": "automatic"})

    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())

    assert _ValveHandler.requests == [("/api/mode?value=automatic", "POST")]
    assert _state(config, accepted.command_id)[0] == "acknowledged"

    observed_at = (_NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _update_observations(config, {
        "mode": "automatic",
        "relay1_mist": "false",
        "relay2_rain": "false",
        "relay3_drip": "false",
        "relay4_light": "false",
    }, observed_at)
    connection = db.connect(config.database)
    try:
        assert reconcile_acknowledged(
            connection, config, clock=_FixedClock(_NOW + timedelta(seconds=1))) == 1
    finally:
        connection.close()

    assert _state(config, accepted.command_id)[0] == "confirmed"


def test_device_interlock_is_terminal_and_not_retried(tmp_path, valve_server):
    _ValveHandler.status_code = 409
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    accepted = _enqueue(config)

    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())

    state, reason, _ = _state(config, accepted.command_id)
    assert (state, reason) == ("failed", "device_interlock")
    assert len(_ValveHandler.requests) == 1


def test_transport_ambiguity_is_uncertain_without_retransmission(tmp_path):
    config = _setup(tmp_path, "http://127.0.0.1:1/")
    accepted = _enqueue(config)

    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())

    state, reason, _ = _state(config, accepted.command_id)
    assert (state, reason) == ("uncertain", "transport_outcome_ambiguous")
    connection = db.connect(config.database)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM command_attempts WHERE command_id = ?",
            (accepted.command_id,),
        ).fetchone()[0] == 1
    finally:
        connection.close()


def test_later_matching_telemetry_reconciles_transport_ambiguity(tmp_path):
    config = _setup(tmp_path, "http://127.0.0.1:1/")
    accepted = _enqueue(config)
    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())
    observed_at = (_NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    _update_observations(config, {
        "mode": "manual",
        "relay1_mist": "true",
        "relay1_always_manual": "false",
    }, observed_at)

    connection = db.connect(config.database)
    try:
        assert reconcile_uncertain(
            connection, config, clock=_FixedClock(_NOW + timedelta(seconds=1))) == 1
    finally:
        connection.close()

    assert _state(config, accepted.command_id)[0] == "confirmed"


def test_state_change_after_acceptance_prevents_transmission(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    accepted = _enqueue(config)
    connection = db.connect(config.database)
    try:
        connection.execute(
            "UPDATE observations_latest SET value = 'automatic', revision = 2 "
            "WHERE device_id = 'valve-controller' AND parameter_id = 'mode'")
    finally:
        connection.close()

    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())

    state, reason, _ = _state(config, accepted.command_id)
    assert (state, reason) == ("failed", "precondition_failed")
    assert _ValveHandler.requests == []


def test_acknowledged_command_becomes_uncertain_after_confirmation_window(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    accepted = _enqueue(config)
    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())

    connection = db.connect(config.database)
    try:
        assert reconcile_acknowledged(
            connection, config, clock=_FixedClock(_NOW + timedelta(seconds=6))) == 1
    finally:
        connection.close()

    state, reason, _ = _state(config, accepted.command_id)
    assert (state, reason) == ("uncertain", "confirmation_timeout")


def test_dispatch_does_not_pass_an_unresolved_acknowledged_command(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    first = _enqueue(config, key="first")
    second = _enqueue(config, key="second", params={
                      "name": "relay4", "state": True})

    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())
    processed = execute_one_pending(
        config, connection_factory=_connection_factory(config), clock=_FixedClock())

    assert _state(config, first.command_id)[0] == "acknowledged"
    assert _state(config, second.command_id)[0] == "pending"
    assert processed is False
    assert len(_ValveHandler.requests) == 1


def test_restart_quarantines_a_claimed_attempt_instead_of_replaying(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    accepted = _enqueue(config)
    connection = db.connect(config.database)
    try:
        processed, claimed = claim_one_pending(
            connection, config, clock=_FixedClock())
        assert processed is True
        assert claimed is not None
        assert _state(config, accepted.command_id)[0] == "dispatching"
        assert recover_interrupted_commands(
            connection, clock=_FixedClock()) == 1
    finally:
        connection.close()

    state, reason, _ = _state(config, accepted.command_id)
    assert (state, reason) == ("uncertain", "interrupted_dispatch")
    assert _ValveHandler.requests == []


def test_backward_clock_does_not_extend_pending_command_lifetime(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    accepted = _enqueue(config)

    execute_one_pending(
        config,
        connection_factory=_connection_factory(config),
        clock=_FixedClock(_NOW - timedelta(seconds=1)),
    )

    state, reason, _ = _state(config, accepted.command_id)
    assert (state, reason) == ("failed", "clock_untrusted")
    assert _ValveHandler.requests == []


def test_command_expiring_after_claim_is_not_transmitted(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    accepted = _enqueue(config)
    clock = _SequenceClock(_NOW, _NOW + timedelta(seconds=13))

    execute_one_pending(
        config, connection_factory=_connection_factory(config), clock=clock)

    state, reason, _ = _state(config, accepted.command_id)
    assert (state, reason) == ("expired", "deadline_expired")
    assert _ValveHandler.requests == []


def test_authenticated_api_to_simulated_valve_to_confirmed_status(tmp_path, valve_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{valve_server.server_port}/")
    connection = db.connect(config.database)
    try:
        current_text = datetime.now(
            timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        connection.execute(
            "UPDATE device_health SET last_success_at = ?, updated_at = ? "
            "WHERE device_id = 'valve-controller'",
            (current_text, current_text),
        )
        connection.execute(
            "UPDATE observations_latest SET observed_at = ? "
            "WHERE device_id = 'valve-controller'",
            (current_text,),
        )
        _, token = issue_credential(connection, "android-owner", "operator")
    finally:
        connection.close()
    client = create_app(config).test_client()

    accepted = client.post(
        "/api/v1/devices/valve-controller/commands",
        json={
            "action_id": "set_output",
            "params": {"name": "relay1", "state": True},
            "idempotency_key": "full-stack-simulation",
            "client_origin": "android-app",
        },
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://localhost",
    )
    assert accepted.status_code == 202
    command_id = accepted.get_json()["command_id"]

    execute_one_pending(config, connection_factory=_connection_factory(config))
    confirmation_time = datetime.now(timezone.utc).replace(
        microsecond=0) + timedelta(seconds=2)
    observed_at = confirmation_time.strftime("%Y-%m-%dT%H:%M:%SZ")
    _update_observations(config, {
        "mode": "manual",
        "relay1_mist": "true",
        "relay1_always_manual": "false",
    }, observed_at)
    connection = db.connect(config.database)
    try:
        assert reconcile_acknowledged(
            connection, config, clock=_FixedClock(confirmation_time)) == 1
    finally:
        connection.close()

    status = client.get(
        f"/api/v1/commands/{command_id}",
        headers={"Authorization": f"Bearer {token}"},
        base_url="https://localhost",
    )
    assert status.status_code == 200
    assert status.get_json()["status"] == "confirmed"
    assert _ValveHandler.requests == [
        ("/api/control?name=relay1&state=1", "POST")]


def test_admin_reconciliation_requires_fresh_evidence_and_is_audited(tmp_path):
    config = _setup(tmp_path, "http://127.0.0.1:1/")
    accepted = _enqueue(config)
    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())
    connection = db.connect(config.database)
    try:
        connection.execute(
            "INSERT INTO command_reconciliation_requests "
            "(request_id, command_id, actor_id, resolution, note, requested_at, status) "
            "VALUES ('r_confirm', ?, 'administrator', 'confirmed', 'checked relay state', ?, 'pending')",
            (accepted.command_id, _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        assert process_reconciliation_requests(
            connection, config, clock=_FixedClock()) == 1
        request_status = connection.execute(
            "SELECT status, result_reason FROM command_reconciliation_requests "
            "WHERE request_id = 'r_confirm'").fetchone()
    finally:
        connection.close()

    assert request_status == (
        "rejected", "fresh post-attempt telemetry is unavailable")
    assert _state(config, accepted.command_id)[0] == "uncertain"


def test_admin_failed_reconciliation_requires_note_and_resolves_block(tmp_path):
    config = _setup(tmp_path, "http://127.0.0.1:1/")
    accepted = _enqueue(config)
    execute_one_pending(config, connection_factory=_connection_factory(
        config), clock=_FixedClock())
    connection = db.connect(config.database)
    try:
        connection.execute(
            "INSERT INTO command_reconciliation_requests "
            "(request_id, command_id, actor_id, resolution, note, requested_at, status) "
            "VALUES ('r_failed', ?, 'administrator', 'failed', 'inspected controller; state unchanged', ?, 'pending')",
            (accepted.command_id, _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        assert process_reconciliation_requests(
            connection, config, clock=_FixedClock()) == 1
        audit = connection.execute(
            "SELECT event_type, actor_id FROM command_audit_events "
            "WHERE command_id = ? ORDER BY event_id DESC LIMIT 1",
            (accepted.command_id,),
        ).fetchone()
    finally:
        connection.close()

    state, reason, _ = _state(config, accepted.command_id)
    connection = db.connect(config.database)
    try:
        outcome = connection.execute(
            "SELECT outcome_json FROM commands WHERE command_id = ?",
            (accepted.command_id,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert (state, reason) == ("failed", "admin_reconciled_failed")
    assert json.loads(outcome)[
        "admin_note"] == "inspected controller; state unchanged"
    assert audit == ("admin_failed", "administrator")
