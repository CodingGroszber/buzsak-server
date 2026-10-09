"""WebSocket transport for the Matter-server (DEV-09, POL-03).

Mirrors poller/http_client.py's contract (bounded timeout/size, raises
PollTransportError on any transport failure) but speaks the Matter-server's
WebSocket protocol instead of plain HTTP GET — see
docs/adapters/matter_server.md for the verified protocol this encodes.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import urlsplit, urlunsplit

import websockets
from websockets.exceptions import WebSocketException

from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.poller.errors import PollTransportError

_MESSAGE_ID = "get_nodes"
_PULSE_MESSAGE_ID = "device_command"

# OnOff cluster (Matter cluster_id 0x0006 = 6), verified against this
# server's real `AcceptedCommandList` for both nodes (DEV-10): decimal 66
# (0x42) = OnWithTimedOff is supported, so the device's own firmware times
# the on->off transition -- a dropped connection after send cannot leave
# the relay stuck on, unlike client-side On/sleep/Off timing.
_ONOFF_CLUSTER_ID = 6
_ON_WITH_TIMED_OFF_COMMAND = "OnWithTimedOff"


def parse_device_address(address: str) -> tuple[str, int]:
    """Splits a config address like ``ws://host:5580/ws#node_id=3`` into
    ``(ws_url, node_id)`` (docs/adapters/matter_server.md). A single
    DeviceConfig.address can only carry one string, but two Sonoff
    switches share one Matter-server connection, so the node id travels
    as a URL fragment on the shared endpoint.
    """
    parts = urlsplit(address)
    if not parts.fragment.startswith("node_id="):
        raise AdapterParseError(
            f"matter device address {address!r}: expected a "
            f"'#node_id=<int>' fragment")
    try:
        node_id = int(parts.fragment.removeprefix("node_id="))
    except ValueError as exc:
        raise AdapterParseError(
            f"matter device address {address!r}: node_id must be an integer") from exc
    ws_url = urlunsplit(parts._replace(fragment=""))
    return ws_url, node_id


async def _fetch_nodes_async(ws_url: str, *, timeout_seconds: float, max_bytes: int) -> list:
    async with websockets.connect(
        ws_url, open_timeout=timeout_seconds, max_size=max_bytes
    ) as connection:
        # DEV-01: the server sends one unsolicited "server info" message
        # immediately on connect, before any command can be sent.
        await asyncio.wait_for(connection.recv(), timeout=timeout_seconds)

        await asyncio.wait_for(
            connection.send(json.dumps(
                {"message_id": _MESSAGE_ID, "command": "get_nodes"})),
            timeout=timeout_seconds,
        )

        while True:
            raw = await asyncio.wait_for(connection.recv(), timeout=timeout_seconds)
            data = json.loads(raw)
            if data.get("message_id") != _MESSAGE_ID:
                continue
            if "error_code" in data:
                raise PollTransportError(
                    f"matter-server {ws_url} returned error_code="
                    f"{data['error_code']}: {data.get('details', '')}"
                )
            result = data.get("result")
            if not isinstance(result, list):
                raise PollTransportError(
                    f"matter-server {ws_url} get_nodes response missing 'result'")
            return result


def fetch_nodes(ws_url: str, *, timeout_seconds: float, max_bytes: int) -> list:
    """Returns the raw ``get_nodes`` result list (DEV-01).

    Opens and closes a short-lived connection per call, matching
    http_client.fetch_json()'s per-poll-cycle transport lifecycle (ARC-04).
    There is no verified single-node query (docs/adapters/matter_server.md),
    so every call fetches all commissioned nodes; callers filter by
    node_id via adapters.matter_server.find_node().
    """
    try:
        return asyncio.run(
            _fetch_nodes_async(ws_url, timeout_seconds=timeout_seconds, max_bytes=max_bytes))
    except (OSError, WebSocketException, asyncio.TimeoutError, TimeoutError) as exc:
        raise PollTransportError(
            f"matter-server request to {ws_url} failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PollTransportError(
            f"matter-server response from {ws_url} was not valid JSON: {exc}") from exc


async def _send_pulse_async(
    ws_url: str,
    node_id: int,
    *,
    on_time_deciseconds: int,
    off_wait_deciseconds: int,
    timeout_seconds: float,
) -> None:
    async with websockets.connect(
        ws_url, open_timeout=timeout_seconds, max_size=4096
    ) as connection:
        # DEV-01: same unsolicited handshake message as fetch_nodes().
        await asyncio.wait_for(connection.recv(), timeout=timeout_seconds)

        await asyncio.wait_for(
            connection.send(json.dumps({
                "message_id": _PULSE_MESSAGE_ID,
                "command": "device_command",
                "args": {
                    "node_id": node_id,
                    "endpoint_id": 1,
                    "cluster_id": _ONOFF_CLUSTER_ID,
                    "command_name": _ON_WITH_TIMED_OFF_COMMAND,
                    "payload": {
                        "onOffControl": 0,
                        "onTime": on_time_deciseconds,
                        "offWaitTime": off_wait_deciseconds,
                    },
                },
            })),
            timeout=timeout_seconds,
        )

        while True:
            raw = await asyncio.wait_for(connection.recv(), timeout=timeout_seconds)
            data = json.loads(raw)
            if data.get("message_id") != _PULSE_MESSAGE_ID:
                continue
            if "error_code" in data:
                raise PollTransportError(
                    f"matter-server {ws_url} device_command returned error_code="
                    f"{data['error_code']}: {data.get('details', '')}"
                )
            return


def send_pulse(
    ws_url: str,
    node_id: int,
    *,
    on_time_deciseconds: int,
    off_wait_deciseconds: int,
    timeout_seconds: float,
) -> None:
    """Sends one Matter OnOff ``OnWithTimedOff`` command (DEV-10).

    This is a single fire-and-forget attempt: callers must never retry a
    failure or timeout from this function automatically (CMD-09, CMD-10) --
    an ambiguous or failed pulse is terminal and requires a new, distinct
    human-issued request. The command name/args shape (``device_command``
    with ``node_id``/``endpoint_id``/``cluster_id``/``command_name``/
    ``payload``) was verified against the real
    ``home-assistant-libs/python-matter-server`` client source, not guessed.
    """
    try:
        asyncio.run(
            _send_pulse_async(
                ws_url,
                node_id,
                on_time_deciseconds=on_time_deciseconds,
                off_wait_deciseconds=off_wait_deciseconds,
                timeout_seconds=timeout_seconds,
            )
        )
    except (OSError, WebSocketException, asyncio.TimeoutError, TimeoutError) as exc:
        raise PollTransportError(
            f"matter-server pulse to {ws_url} node {node_id} failed: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PollTransportError(
            f"matter-server pulse response from {ws_url} was not valid JSON: {exc}") from exc
