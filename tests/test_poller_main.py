"""Tests for buzsak_pi3_server.poller.__main__'s thread-liveness watchdog (OPS-04).

A worker thread can only stop by raising an unhandled exception (it is
daemon=True, so it then vanishes silently); `_supervise` must notice this
and exit the process so systemd restarts it and respawns every thread.
"""

from __future__ import annotations

import threading
import time

import pytest

from buzsak_pi3_server.poller.__main__ import _supervise


def test_supervise_exits_when_a_worker_thread_dies():
    stop_event = threading.Event()
    dead_thread = threading.Thread(
        target=lambda: None, name="poller-garden-plc")
    dead_thread.start()
    dead_thread.join()  # already finished before _supervise ever checks it

    with pytest.raises(SystemExit) as exc_info:
        _supervise(stop_event, [dead_thread], check_interval_seconds=0.01)

    assert exc_info.value.code == 1


def test_supervise_returns_on_clean_shutdown_without_checking_threads():
    stop_event = threading.Event()
    alive_thread = threading.Thread(
        target=stop_event.wait, name="poller-garden-plc")
    alive_thread.start()
    try:
        stop_event.set()
        _supervise(stop_event, [alive_thread], check_interval_seconds=5.0)
    finally:
        alive_thread.join(timeout=5.0)


def test_supervise_keeps_running_while_all_threads_are_alive():
    stop_event = threading.Event()
    alive_thread = threading.Thread(
        target=stop_event.wait, name="poller-garden-plc")
    alive_thread.start()
    try:
        def _stop_after_a_few_checks():
            time.sleep(0.2)  # several 0.05s check_interval cycles
            stop_event.set()

        threading.Thread(target=_stop_after_a_few_checks, daemon=True).start()

        _supervise(stop_event, [alive_thread], check_interval_seconds=0.05)
        # Reaching here (no SystemExit) proves repeated liveness checks
        # against a live thread don't trigger the watchdog.
    finally:
        stop_event.set()
        alive_thread.join(timeout=5.0)
