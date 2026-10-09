"""Device adapters (DEV-01 through DEV-08).

Each adapter module parses a device's HTTP JSON contract into typed,
validated structures and documents its capability metadata (controllable
actions, preconditions, confirmation rules). Adapters intentionally contain
no HTTP client, scheduling, retry/backoff, or persistence logic (ARC-07):
that lives in the poller and dispatcher, which import these pure parsers.

The formal contracts, verified against firmware source, are documented in
docs/adapters/garden_plc.md and docs/adapters/valve_controller.md.
"""
