"""Bounded, timed-out JSON fetch over HTTP (POL-03).

Stdlib-only (no new dependency): `urllib.request` is sufficient for the
simple `GET` + JSON-body contract every current adapter uses. `urlopen`'s
`timeout` applies to the whole connect-and-read operation, not connect and
read separately -- stdlib cannot express that split without raw sockets;
documented here as a known simplification rather than a silent gap.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any

from buzsak_pi3_server.poller.errors import PollTransportError

_USER_AGENT = "buzsak-pi3-server-poller/1"


def fetch_json(url: str, *, timeout_seconds: float, max_bytes: int) -> Any:
    """Fetches `url` and parses its body as JSON.

    Raises PollTransportError for any network failure, non-2xx status,
    oversized response, or invalid JSON -- never returns a partial or
    guessed result.
    """
    request = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": _USER_AGENT}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(max_bytes + 1)
    except OSError as exc:
        raise PollTransportError(f"request to {url} failed: {exc}") from exc

    if len(raw) > max_bytes:
        raise PollTransportError(
            f"response from {url} exceeded {max_bytes} bytes")

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PollTransportError(
            f"response from {url} was not valid JSON: {exc}") from exc
