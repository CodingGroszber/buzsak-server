"""Tests for buzsak_pi3_server.poller.matter_client (DEV-09, POL-03).

Uses a real local WebSocket server (a simulated Matter-server) in a
background thread with its own event loop, mirroring
test_poller_http_client.py's real-socket-over-mocking approach.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
import websockets

from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.poller.errors import PollTransportError
from buzsak_pi3_server.poller.matter_client import fetch_nodes, parse_device_address, send_pulse

_HANDSHAKE = json.dumps({"schema_version": 11})


async def _handler(connection):
    path = connection.request.path
    if path == "/hangs-before-handshake":
        await asyncio.sleep(5)
        return

    await connection.send(_HANDSHAKE)
    async for raw in connection:
        data = json.loads(raw)
        message_id = data["message_id"]
        if path == "/ok":
            await connection.send(json.dumps({
                "message_id": message_id,
                "result": [{"node_id": 1, "available": True, "attributes": {}}],
            }))
        elif path == "/error":
            await connection.send(json.dumps({
                "message_id": message_id,
                "error_code": 8,
                "details": "unsupported",
            }))
        elif path == "/not-json":
            await connection.send("not json")
        elif path == "/hangs-after-handshake":
            await asyncio.sleep(5)
        elif path == "/pulse-ok":
            assert data["command"] == "device_command"
            assert data["args"] == {
                "node_id": 1,
                "endpoint_id": 1,
                "cluster_id": 6,
                "command_name": "OnWithTimedOff",
                "payload": {"onOffControl": 0, "onTime": 5, "offWaitTime": 5},
            }
            await connection.send(json.dumps({
                "message_id": message_id,
                "result": None,
            }))
        elif path == "/pulse-error":
            await connection.send(json.dumps({
                "message_id": message_id,
                "error_code": 1,
                "details": "unsupported command",
            }))


class _ServerThread(threading.Thread):
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
        async with websockets.serve(_handler, "127.0.0.1", 0) as server:
            self.port = server.sockets[0].getsockname()[1]
            self._ready.set()
            await self._stop_event.wait()

    def stop(self) -> None:
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        self.join(timeout=5)


@pytest.fixture
def server():
    thread = _ServerThread()
    thread.start()
    assert thread._ready.wait(timeout=5)
    yield thread
    thread.stop()


def _ws_url(server, path):
    return f"ws://127.0.0.1:{server.port}{path}"


def test_fetch_nodes_returns_the_result_list(server):
    result = fetch_nodes(_ws_url(server, "/ok"),
                         timeout_seconds=2.0, max_bytes=65536)

    assert result == [{"node_id": 1, "available": True, "attributes": {}}]


def test_fetch_nodes_raises_on_error_code_response(server):
    with pytest.raises(PollTransportError, match="error_code"):
        fetch_nodes(_ws_url(server, "/error"),
                    timeout_seconds=2.0, max_bytes=65536)


def test_fetch_nodes_raises_on_non_json_response(server):
    with pytest.raises(PollTransportError):
        fetch_nodes(_ws_url(server, "/not-json"),
                    timeout_seconds=2.0, max_bytes=65536)


def test_fetch_nodes_raises_on_timeout_after_handshake(server):
    with pytest.raises(PollTransportError):
        fetch_nodes(_ws_url(server, "/hangs-after-handshake"),
                    timeout_seconds=0.2, max_bytes=65536)


def test_fetch_nodes_raises_on_timeout_before_handshake(server):
    with pytest.raises(PollTransportError):
        fetch_nodes(_ws_url(server, "/hangs-before-handshake"),
                    timeout_seconds=0.2, max_bytes=65536)


def test_fetch_nodes_raises_when_server_is_unreachable():
    with pytest.raises(PollTransportError):
        fetch_nodes("ws://127.0.0.1:1/ws",
                    timeout_seconds=0.5, max_bytes=65536)


def test_parse_device_address_splits_url_and_node_id():
    ws_url, node_id = parse_device_address(
        "ws://192.168.1.95:5580/ws#node_id=3")

    assert ws_url == "ws://192.168.1.95:5580/ws"
    assert node_id == 3


def test_parse_device_address_raises_without_fragment():
    with pytest.raises(AdapterParseError):
        parse_device_address("ws://192.168.1.95:5580/ws")


def test_parse_device_address_raises_on_non_integer_node_id():
    with pytest.raises(AdapterParseError):
        parse_device_address("ws://192.168.1.95:5580/ws#node_id=abc")


def test_send_pulse_sends_the_verified_device_command_shape(server):
    send_pulse(
        _ws_url(server, "/pulse-ok"),
        1,
        on_time_deciseconds=5,
        off_wait_deciseconds=5,
        timeout_seconds=2.0,
    )
    # The handler asserts the exact message shape; reaching here means it matched.


def test_send_pulse_raises_on_error_code_response(server):
    with pytest.raises(PollTransportError, match="error_code"):
        send_pulse(
            _ws_url(server, "/pulse-error"),
            1,
            on_time_deciseconds=5,
            off_wait_deciseconds=5,
            timeout_seconds=2.0,
        )


def test_send_pulse_raises_when_server_is_unreachable():
    with pytest.raises(PollTransportError):
        send_pulse(
            "ws://127.0.0.1:1/ws",
            1,
            on_time_deciseconds=5,
            off_wait_deciseconds=5,
            timeout_seconds=0.5,
        )
