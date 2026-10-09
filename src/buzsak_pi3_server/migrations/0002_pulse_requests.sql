-- Minimal scoped command-execution exception (DEV-10) for the Sonoff
-- MiniD "pulse" capability only. This is deliberately NOT the general
-- CMD-01..19 command lifecycle (Section 9): no idempotency keys, no
-- authenticated-actor tracking, no automatic retry. A pulse is a single
-- fire-and-forget attempt; a failed or ambiguous outcome is terminal and
-- requires a new, distinct human-issued request (CMD-09, CMD-10 spirit).

CREATE TABLE pulse_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'sent', 'succeeded', 'failed', 'expired')
    ),
    requested_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    executed_at TEXT,
    error TEXT
);

CREATE INDEX idx_pulse_requests_device_lookup
    ON pulse_requests (device_id, requested_at);
