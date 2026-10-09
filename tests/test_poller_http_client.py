"""Tests for buzsak_pi3_server.poller.http_client (POL-03).

Uses a real local HTTP server (a simulated device) in a background thread
rather than mocking urllib, so timeouts/oversized-body/bad-status handling
is checked against actual socket behavior.
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from buzsak_pi3_server.poller.errors import PollTransportError
from buzsak_pi3_server.poller.http_client import fetch_json


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # noqa: A002 - silence test output
        pass

    def do_GET(self):
        if self.path == "/ok":
            body = json.dumps({"firmware": "v1"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/big":
            body = ("x" * 200).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/not-json":
            body = b"not json"
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/error":
            self.send_response(500)
            self.end_headers()
        elif self.path == "/slow":
            time.sleep(2.0)
            self.send_response(200)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield httpd
    httpd.shutdown()
    thread.join()


def _url(server, path):
    return f"http://127.0.0.1:{server.server_port}{path}"


def test_fetch_json_returns_parsed_body(server):
    result = fetch_json(_url(server, "/ok"),
                        timeout_seconds=2.0, max_bytes=65536)
    assert result == {"firmware": "v1"}


def test_fetch_json_rejects_oversized_response(server):
    with pytest.raises(PollTransportError, match="exceeded"):
        fetch_json(_url(server, "/big"), timeout_seconds=2.0, max_bytes=100)


def test_fetch_json_rejects_invalid_json(server):
    with pytest.raises(PollTransportError, match="not valid JSON"):
        fetch_json(_url(server, "/not-json"),
                   timeout_seconds=2.0, max_bytes=65536)


def test_fetch_json_rejects_non_2xx_status(server):
    with pytest.raises(PollTransportError):
        fetch_json(_url(server, "/error"),
                   timeout_seconds=2.0, max_bytes=65536)


def test_fetch_json_raises_on_timeout(server):
    with pytest.raises(PollTransportError):
        fetch_json(_url(server, "/slow"),
                   timeout_seconds=0.2, max_bytes=65536)


def test_fetch_json_raises_when_unreachable():
    with pytest.raises(PollTransportError):
        fetch_json("http://127.0.0.1:1", timeout_seconds=0.5, max_bytes=65536)
