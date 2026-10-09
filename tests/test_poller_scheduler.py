"""Tests for buzsak_pi3_server.poller.scheduler (POL-01, POL-02, ARC-01).

Uses a real local HTTP server (a simulated device) and a real SQLite file,
matching this project's preference for behavioral tests over mocks.
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import websockets

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.clock import Clock
from buzsak_pi3_server.config import (
    AppConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.device_registration import sync_devices
from buzsak_pi3_server.poller.scheduler import poll_once, run_device_loop

FIXTURE = Path(__file__).parent / "fixtures" / \
    "garden_plc" / "state_ok.json"


class _FixedClock(Clock):
    def utc_now(self) -> datetime:
        return datetime(2026, 9, 30, 0, 0, 0, tzinfo=timezone.utc)


class _StateHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - silence test output
        pass

    def do_GET(self):
        if self.path == "/api/state":
            body = FIXTURE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def state_server():
    httpd = HTTPServer(("127.0.0.1", 0), _StateHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    thread.join()


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "buzsak.sqlite3"
    setup = db.connect(DatabaseConfig(path=str(path)))
    schema.apply_migrations(setup)
    setup.close()
    return path


_MATTER_HANDSHAKE = json.dumps({"schema_version": 11})
_MATTER_NODES = [
    {"node_id": 1, "available": True, "attributes": {
        "0/40/1": "SONOFF", "0/40/3": "SONOFF MINI-D Wi-Fi Smart Switch",
        "0/40/15": "SN1", "1/6/0": True,
    }},
    {"node_id": 3, "available": True, "attributes": {
        "0/40/1": "SONOFF", "0/40/3": "SONOFF MINI-D Wi-Fi Smart Switch",
        "0/40/15": "SN3", "1/6/0": False,
    }},
]


async def _matter_handler(connection):
    await connection.send(_MATTER_HANDSHAKE)
    async for raw in connection:
        data = json.loads(raw)
        await connection.send(json.dumps(
            {"message_id": data["message_id"], "result": _MATTER_NODES}))


class _MatterServerThread(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.port: int | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_until_complete(self._serve())

    async def _serve(self) -> None:
        self._stop_event = asyncio.Event()
        async with websockets.serve(_matter_handler, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop_event.wait()

    def stop(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        self.join(timeout=5)


@pytest.fixture
def matter_server():
    thread = _MatterServerThread()
    thread.start()
    assert thread._ready.wait(timeout=5)
    yield thread
    thread.stop()


def _app_config(db_path, address: str, **polling_overrides) -> AppConfig:
    app_config = AppConfig(
        database=DatabaseConfig(path=str(db_path)),
        server=ServerConfig(),
        polling=PollingConfig(
            default_interval_seconds=0.05,
            request_timeout_seconds=2.0,
            backoff_base_seconds=0.05,
            backoff_max_seconds=0.2,
            **polling_overrides,
        ),
        devices=(
            DeviceConfig(id="garden-plc", kind="garden-plc",
                         address=address, enabled=True),
        ),
    )
    connection = db.connect(app_config.database)
    sync_devices(connection, app_config)
    connection.close()
    return app_config


def test_poll_once_records_observations_and_marks_device_healthy(db_path, state_server):
    address = f"http://127.0.0.1:{state_server.server_port}/"
    app_config = _app_config(db_path, address)
    device = app_config.devices[0]

    outcome = poll_once(
        lambda: db.connect(app_config.database), device, app_config, _FixedClock())

    assert outcome.success is True
    assert outcome.consecutive_failures == 0

    connection = db.connect(app_config.database)
    try:
        value, quality = connection.execute(
            "SELECT value, quality FROM observations_latest "
            "WHERE device_id = 'garden-plc' AND parameter_id = 'well_pump'"
        ).fetchone()
        assert (value, quality) == ("true", "good")

        status, last_success_at = connection.execute(
            "SELECT status, last_success_at FROM device_health WHERE device_id = 'garden-plc'"
        ).fetchone()
        assert status == "healthy"
        assert last_success_at == "2026-09-30T00:00:00Z"
    finally:
        connection.close()


def test_poll_once_survives_a_db_write_error_instead_of_raising(db_path, state_server, monkeypatch):
    # Regression: a transient "database is locked" error during the write
    # phase must not propagate out of poll_once and kill the device's
    # polling thread (ARC-06) -- it must become a bounded failure outcome.
    import sqlite3

    from buzsak_pi3_server.poller import scheduler

    def _raise(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(scheduler.observations_module,
                        "record_observation", _raise)

    address = f"http://127.0.0.1:{state_server.server_port}/"
    app_config = _app_config(db_path, address)
    device = app_config.devices[0]

    outcome = poll_once(
        lambda: db.connect(app_config.database), device, app_config, _FixedClock())

    assert outcome.success is False
    assert outcome.consecutive_failures == 1


def test_poll_once_survives_an_unanticipated_exception_instead_of_raising(db_path, state_server, monkeypatch):
    # Regression for the 2026-10-05 incident: an exception type the
    # existing `except` clauses don't anticipate (not PollTransportError,
    # AdapterParseError, or sqlite3.Error) must still degrade to a bounded
    # failure instead of silently ending this device's daemon thread.
    from buzsak_pi3_server.poller import scheduler

    def _raise(*args, **kwargs):
        raise KeyError("unexpected_parameter_id")
    monkeypatch.setattr(scheduler, "extract_observations", _raise)

    address = f"http://127.0.0.1:{state_server.server_port}/"
    app_config = _app_config(db_path, address)
    device = app_config.devices[0]

    outcome = poll_once(
        lambda: db.connect(app_config.database), device, app_config, _FixedClock())

    assert outcome.success is False
    assert outcome.consecutive_failures == 1

    connection = db.connect(app_config.database)
    try:
        status, last_error = connection.execute(
            "SELECT status, last_error FROM device_health WHERE device_id = 'garden-plc'"
        ).fetchone()
        assert status == "degraded"
        assert "unexpected error" in last_error
    finally:
        connection.close()


def test_poll_once_survives_a_health_write_failure_during_crash_handling(db_path, state_server, monkeypatch):
    # If even the fallback health-write fails after an unanticipated
    # exception, poll_once must still return rather than raise (ARC-06).
    import sqlite3

    from buzsak_pi3_server.poller import scheduler

    def _raise_in_extraction(*args, **kwargs):
        raise RuntimeError("boom")

    def _raise_in_health_write(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(scheduler, "extract_observations",
                        _raise_in_extraction)
    monkeypatch.setattr(
        scheduler.health, "record_poll_failure", _raise_in_health_write)

    address = f"http://127.0.0.1:{state_server.server_port}/"
    app_config = _app_config(db_path, address)
    device = app_config.devices[0]

    outcome = poll_once(
        lambda: db.connect(app_config.database), device, app_config, _FixedClock())

    assert outcome.success is False
    assert outcome.consecutive_failures == 1


def test_poll_once_marks_device_degraded_when_unreachable(db_path):
    app_config = _app_config(db_path, "http://127.0.0.1:1/")
    device = app_config.devices[0]

    outcome = poll_once(
        lambda: db.connect(app_config.database), device, app_config, _FixedClock())

    assert outcome.success is False
    assert outcome.consecutive_failures == 1

    connection = db.connect(app_config.database)
    try:
        status, last_error = connection.execute(
            "SELECT status, last_error FROM device_health WHERE device_id = 'garden-plc'"
        ).fetchone()
        assert status == "degraded"
        assert last_error is not None
    finally:
        connection.close()


def test_poll_once_polls_a_sonoff_minid_device_over_matter_ws(db_path, matter_server):
    # DEV-09: a get_nodes call always returns both commissioned nodes; the
    # scheduler must filter down to this device's own node_id (3) before
    # extraction/observation writes.
    address = f"ws://127.0.0.1:{matter_server.port}/ws#node_id=3"
    app_config = AppConfig(
        database=DatabaseConfig(path=str(db_path)),
        server=ServerConfig(),
        polling=PollingConfig(
            default_interval_seconds=0.05,
            request_timeout_seconds=2.0,
            backoff_base_seconds=0.05,
            backoff_max_seconds=0.2,
        ),
        devices=(
            DeviceConfig(id="sonoff-2", kind="sonoff-minid",
                         address=address, enabled=True),
        ),
    )
    connection = db.connect(app_config.database)
    sync_devices(connection, app_config)
    connection.close()
    device = app_config.devices[0]

    outcome = poll_once(
        lambda: db.connect(app_config.database), device, app_config, _FixedClock())

    assert outcome.success is True

    connection = db.connect(app_config.database)
    try:
        value, quality = connection.execute(
            "SELECT value, quality FROM observations_latest "
            "WHERE device_id = 'sonoff-2' AND parameter_id = 'on_off'"
        ).fetchone()
        assert (value, quality) == ("false", "good")

        status = connection.execute(
            "SELECT status FROM device_health WHERE device_id = 'sonoff-2'"
        ).fetchone()[0]
        assert status == "healthy"
    finally:
        connection.close()


def test_run_device_loop_polls_repeatedly_until_stopped(db_path, state_server):
    address = f"http://127.0.0.1:{state_server.server_port}/"
    app_config = _app_config(db_path, address)
    device = app_config.devices[0]
    stop_event = threading.Event()

    thread = threading.Thread(
        target=run_device_loop,
        args=(device, app_config, stop_event),
        kwargs={"clock": _FixedClock()},
        daemon=True,
    )
    thread.start()
    stop_event.wait(timeout=0.3)
    stop_event.set()
    thread.join(timeout=2.0)

    assert not thread.is_alive()

    connection = db.connect(app_config.database)
    try:
        status = connection.execute(
            "SELECT status FROM device_health WHERE device_id = 'garden-plc'"
        ).fetchone()[0]
        assert status == "healthy"
    finally:
        connection.close()
