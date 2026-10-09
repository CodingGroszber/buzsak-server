-- Core telemetry schema (DB-02, DB-03, DB-05).
-- Statements are split on ';' by the migration runner: no ';' may appear
-- inside a string literal in this file.

CREATE TABLE devices (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    address TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE parameters (
    device_id TEXT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    parameter_id TEXT NOT NULL,
    category TEXT NOT NULL CHECK (
        category IN ('boolean', 'continuous', 'counter', 'configuration', 'identity', 'diagnostic')
    ),
    unit TEXT,
    PRIMARY KEY (device_id, parameter_id)
);

CREATE TABLE capabilities (
    device_id TEXT NOT NULL REFERENCES devices (id) ON DELETE CASCADE,
    action_id TEXT NOT NULL,
    params_schema TEXT NOT NULL,
    PRIMARY KEY (device_id, action_id)
);

-- Latest observation per (device, parameter). `revision` increments only
-- when the stored value changes, so CMD-15 preconditions can detect a
-- change without comparing full values; `observed_at` refreshes on every
-- poll regardless of validity (POL-07), while `last_changed_at` refreshes
-- only alongside `revision` (POL-11).
CREATE TABLE observations_latest (
    device_id TEXT NOT NULL,
    parameter_id TEXT NOT NULL,
    value TEXT,
    value_type TEXT NOT NULL CHECK (value_type IN ('bool', 'int', 'float', 'string', 'null')),
    quality TEXT NOT NULL CHECK (quality IN ('good', 'stale', 'unavailable', 'invalid')),
    observed_at TEXT NOT NULL,
    last_changed_at TEXT,
    revision INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (device_id, parameter_id),
    FOREIGN KEY (device_id, parameter_id) REFERENCES parameters (device_id, parameter_id) ON DELETE CASCADE
);

-- Append-only telemetry history; cadence/retention policy lives in the
-- poller and retention job, not in the schema itself (POL-11, DB-08).
CREATE TABLE observations_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id TEXT NOT NULL,
    parameter_id TEXT NOT NULL,
    value TEXT,
    value_type TEXT NOT NULL CHECK (value_type IN ('bool', 'int', 'float', 'string', 'null')),
    quality TEXT NOT NULL CHECK (quality IN ('good', 'stale', 'unavailable', 'invalid')),
    observed_at TEXT NOT NULL,
    FOREIGN KEY (device_id, parameter_id) REFERENCES parameters (device_id, parameter_id) ON DELETE CASCADE
);

CREATE INDEX idx_observations_history_lookup
    ON observations_history (device_id, parameter_id, observed_at);

CREATE TABLE device_health (
    device_id TEXT PRIMARY KEY REFERENCES devices (id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('unknown', 'healthy', 'degraded', 'offline')),
    last_success_at TEXT,
    last_error TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
