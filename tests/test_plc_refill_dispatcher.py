from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from buzsak_pi3_server import db, schema
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
    execute_one_pending,
    reconcile_acknowledged,
)
from buzsak_pi3_server.security.credentials import Principal

_NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
_OPERATOR = Principal("credential-1", "garden-operator", "operator")


class _Clock:
    def __init__(self, now=_NOW):
        self.now = now

    def utc_now(self):
        return self.now


class _RefillHandler(BaseHTTPRequestHandler):
    requests = []
    status_code = 200

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(size).decode("ascii")
        type(self).requests.append(
            (self.path, self.headers.get("Content-Type"), body))
        self.send_response(type(self).status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true,"refill_button":true}')


@pytest.fixture
def plc_server():
    _RefillHandler.requests = []
    _RefillHandler.status_code = 200
    server = HTTPServer(("127.0.0.1", 0), _RefillHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


def _setup(tmp_path, address, *, enabled=True):
    config = AppConfig(
        database=DatabaseConfig(
            path=str(tmp_path / "plc-refill-dispatch.sqlite3")),
        server=ServerConfig(),
        polling=PollingConfig(default_interval_seconds=1, stale_multiplier=3),
        devices=(DeviceConfig(
            id="garden-plc", kind="garden-plc", address=address, enabled=True),),
        control=ControlConfig(plc_refill_enabled=enabled),
    )
    connection = db.connect(config.database)
    schema.apply_migrations(connection)
    sync_devices(connection, config)
    timestamp = _NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    connection.execute(
        "INSERT INTO device_health "
        "(device_id,status,last_success_at,consecutive_failures,updated_at) "
        "VALUES ('garden-plc','healthy',?,0,?)", (timestamp, timestamp))
    observations = {
        "switch_mode_code": ("1", "int"),
        "refill_button": ("false", "bool"),
        "refill_active": ("false", "bool"),
        "water_level_signal": ("true", "bool"),
        "water_level_liters": ("750.0", "float"),
    }
    for parameter_id, (value, value_type) in observations.items():
        connection.execute(
            "INSERT INTO observations_latest "
            "(device_id,parameter_id,value,value_type,quality,observed_at,last_changed_at,revision) "
            "VALUES ('garden-plc',?,?,?,'good',?,?,1)",
            (parameter_id, value, value_type, timestamp, timestamp))
    connection.close()
    return config


def _enqueue(config, key="refill-command"):
    connection = db.connect(config.database)
    try:
        return submit_command(
            connection,
            config,
            _OPERATOR,
            device_id="garden-plc",
            action_id="refill",
            params={},
            idempotency_key=key,
            client_origin="android-app",
            clock=_Clock(),
        )
    finally:
        connection.close()


def _connection_factory(config):
    return lambda: db.connect(config.database)


def test_refill_dispatch_posts_firmware_form_and_confirms_from_fresh_state(tmp_path, plc_server):
    config = _setup(tmp_path, f"http://127.0.0.1:{plc_server.server_port}/")
    accepted = _enqueue(config)

    assert execute_one_pending(
        config, connection_factory=_connection_factory(config), clock=_Clock())
    assert _RefillHandler.requests == [
        ("/api/control", "application/x-www-form-urlencoded", "name=refill&state=1")]

    connection = db.connect(config.database)
    observed_at = (_NOW + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        connection.execute(
            "UPDATE observations_latest SET value='true',observed_at=? "
            "WHERE device_id='garden-plc' AND parameter_id='refill_active'",
            (observed_at,),
        )
        connection.execute(
            "UPDATE observations_latest SET value='false',observed_at=? "
            "WHERE device_id='garden-plc' AND parameter_id='refill_button'",
            (observed_at,),
        )
        assert reconcile_acknowledged(
            connection, config, clock=_Clock(_NOW + timedelta(seconds=1))) == 1
        state = connection.execute(
            "SELECT state,reason FROM commands WHERE command_id=?",
            (accepted.command_id,),
        ).fetchone()
        attempts = connection.execute(
            "SELECT COUNT(*) FROM command_attempts WHERE command_id=?",
            (accepted.command_id,),
        ).fetchone()[0]
    finally:
        connection.close()

    assert state == ("confirmed", None)
    assert attempts == 1
    assert len(_RefillHandler.requests) == 1


def test_refill_interlock_is_terminal_and_never_retried(tmp_path, plc_server):
    _RefillHandler.status_code = 409
    config = _setup(tmp_path, f"http://127.0.0.1:{plc_server.server_port}/")
    accepted = _enqueue(config)

    execute_one_pending(
        config, connection_factory=_connection_factory(config), clock=_Clock())

    connection = db.connect(config.database)
    try:
        state = connection.execute(
            "SELECT state,reason FROM commands WHERE command_id=?",
            (accepted.command_id,),
        ).fetchone()
    finally:
        connection.close()
    assert state == ("failed", "device_interlock")
    assert len(_RefillHandler.requests) == 1
