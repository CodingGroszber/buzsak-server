"""Dispatcher process entry point for durable and scoped device commands (ARC-02, ARC-06)."""

from __future__ import annotations

import logging
import signal
import time
from types import FrameType

from buzsak_pi3_server import db
from buzsak_pi3_server.config import load_config_from_env
from buzsak_pi3_server.dispatcher import commands as valve_commands
from buzsak_pi3_server.dispatcher import pulse
from buzsak_pi3_server.logging_setup import configure_logging

logger = logging.getLogger(__name__)

_shutdown_requested = False
_IDLE_WAIT_SECONDS = 0.5


def _handle_shutdown_signal(signum: int, frame: FrameType | None) -> None:
    global _shutdown_requested
    _shutdown_requested = True


def main() -> None:
    configure_logging(service_name="dispatcher")
    config = load_config_from_env()

    enabled_devices = [device for device in config.devices if device.enabled]
    logger.info(
        "dispatcher starting: %d device(s) configured, %d enabled",
        len(config.devices),
        len(enabled_devices),
    )
    if not enabled_devices:
        logger.warning("no enabled devices configured; dispatcher is idling")

    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)

    connection = db.connect(config.database)
    try:
        recovered = valve_commands.recover_interrupted_commands(connection)
    finally:
        connection.close()
    if recovered:
        logger.error(
            "quarantined %d interrupted valve command(s) as uncertain; "
            "operator reconciliation is required",
            recovered,
        )

    while not _shutdown_requested:
        connection = db.connect(config.database)
        try:
            processed_pulse = pulse.execute_pending_pulse(connection, config)
            reconciled = valve_commands.reconcile_acknowledged(
                connection, config)
            reconciled += valve_commands.reconcile_uncertain(
                connection, config)
            reconciliations = valve_commands.process_reconciliation_requests(
                connection, config)
        finally:
            connection.close()
        processed_command = valve_commands.execute_one_pending(config)
        if not processed_pulse and not processed_command and not reconciled and not reconciliations:
            time.sleep(_IDLE_WAIT_SECONDS)

    logger.info("dispatcher shutting down (OPS-13)")


if __name__ == "__main__":
    main()
