"""Validate the target's configuration before a deploy touches data or
services (OPS-02, OPS-06).

Run on the target with `BUZSAK_CONFIG` already set. Exits 0 and prints a
short summary on success; exits 1 and prints the `ConfigError` on failure,
so a deploy can abort before running migrations or restarting services.
"""
from __future__ import annotations

import sys

from buzsak_pi3_server import config

try:
    app_config = config.load_config_from_env()
except config.ConfigError as exc:
    print(f"CONFIG INVALID: {exc}", file=sys.stderr)
    sys.exit(1)

print(
    f"config OK: database={app_config.database.path} "
    f"bind={app_config.server.bind_host}:{app_config.server.bind_port} "
    f"devices={len(app_config.devices)}"
)
