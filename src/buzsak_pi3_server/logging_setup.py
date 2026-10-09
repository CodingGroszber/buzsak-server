"""Logging setup: UTC timestamps, severity, service identifier (OPS-08).

Services are expected to run under systemd with stdout/stderr captured by
journald; this module only formats records, it does not manage a special
journal handler or file destination.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone


class UtcFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(
            timespec="milliseconds"
        )


def configure_logging(service_name: str, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(
        UtcFormatter(
            fmt=f"%(asctime)sZ %(levelname)s {service_name} %(name)s: %(message)s")
    )
    root.addHandler(handler)
