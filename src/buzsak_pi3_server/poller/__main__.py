"""Poller process entry point (ARC-01, ARC-06).

Loads and validates configuration, sets up logging, then runs one polling
thread per enabled device (poller/scheduler.py) until a shutdown signal
arrives. Device adapters (DEV-01) implement the actual parsing; this
process only owns scheduling, persistence, and lifecycle (ARC-07).
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from types import FrameType

from buzsak_pi3_server.config import load_config_from_env
from buzsak_pi3_server.logging_setup import configure_logging
from buzsak_pi3_server.poller.scheduler import run_device_loop

logger = logging.getLogger(__name__)

# How often the main thread checks that every worker thread is still alive.
# A worker can only stop via an unhandled exception (daemon threads then
# vanish silently); without this check a dead device thread goes unnoticed
# indefinitely while the process itself stays "active" (OPS-04, ARC-06).
_THREAD_LIVENESS_CHECK_SECONDS = 30.0


def _supervise(
    stop_event: threading.Event,
    threads: list[threading.Thread],
    *,
    check_interval_seconds: float = _THREAD_LIVENESS_CHECK_SECONDS,
) -> None:
    """Blocks until shutdown; exits the process if any worker thread died.

    `sys.exit(1)` makes systemd's `Restart=on-failure` restart the whole
    process, which respawns every worker thread, instead of leaving the
    process running with fewer live threads than configured devices.
    """
    while not stop_event.is_set():
        if stop_event.wait(timeout=check_interval_seconds):
            return
        dead = [
            poll_thread.name for poll_thread in threads if not poll_thread.is_alive()]
        if dead:
            logger.critical(
                "poller worker thread(s) exited unexpectedly: %s; exiting so "
                "systemd restarts the process and resumes polling (OPS-04)",
                ", ".join(dead),
            )
            sys.exit(1)


def main() -> None:
    configure_logging(service_name="poller")
    config = load_config_from_env()

    enabled_devices = [device for device in config.devices if device.enabled]
    logger.info(
        "poller starting: %d device(s) configured, %d enabled",
        len(config.devices),
        len(enabled_devices),
    )
    if not enabled_devices:
        logger.warning(
            "no enabled devices configured; poller is idling")

    stop_event = threading.Event()

    def _handle_shutdown_signal(signum: int, frame: FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    threads = [
        threading.Thread(
            target=run_device_loop,
            args=(device, config, stop_event),
            name=f"poller-{device.id}",
            daemon=True,
        )
        for device in enabled_devices
    ]
    for poll_thread in threads:
        poll_thread.start()

    _supervise(stop_event, threads)

    for poll_thread in threads:
        poll_thread.join(timeout=5.0)

    logger.info("poller shutting down (OPS-13)")


if __name__ == "__main__":
    main()
