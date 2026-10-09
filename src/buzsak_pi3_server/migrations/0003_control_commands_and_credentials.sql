-- Durable control-command lifecycle and revocable API credentials (DB-02, DB-04, DB-10).

CREATE TABLE api_credentials (
    credential_id TEXT PRIMARY KEY,
    principal_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'operator', 'admin')),
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    revoked_at TEXT
);

CREATE INDEX idx_api_credentials_principal
    ON api_credentials (principal_id, role, revoked_at);

CREATE TABLE commands (
    acceptance_sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL UNIQUE,
    actor_id TEXT NOT NULL,
    client_origin TEXT NOT NULL,
    device_id TEXT NOT NULL REFERENCES devices (id) ON DELETE RESTRICT,
    action_id TEXT NOT NULL,
    params_json TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('pending', 'dispatching', 'sent', 'acknowledged', 'confirmed',
                  'failed', 'expired', 'cancelled', 'uncertain')
    ),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    preconditions_json TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    confirmation_deadline TEXT,
    confirmation_json TEXT,
    outcome_json TEXT,
    reason TEXT,
    UNIQUE (actor_id, idempotency_key)
);

CREATE INDEX idx_commands_pending_fifo
    ON commands (device_id, acceptance_sequence)
    WHERE state = 'pending';

CREATE INDEX idx_commands_device_state
    ON commands (device_id, state, acceptance_sequence);

CREATE TABLE command_idempotency (
    actor_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    command_id TEXT NOT NULL,
    expires_at TEXT,
    PRIMARY KEY (actor_id, idempotency_key)
);

CREATE INDEX idx_command_idempotency_expiry
    ON command_idempotency (expires_at);

CREATE TABLE command_attempts (
    attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT NOT NULL REFERENCES commands (command_id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN ('started', 'acknowledged', 'failed', 'uncertain', 'reconciled')
    ),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    request_json TEXT NOT NULL,
    response_json TEXT,
    error TEXT,
    UNIQUE (command_id, attempt_number)
);

CREATE TABLE command_audit_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    command_id TEXT REFERENCES commands (command_id) ON DELETE RESTRICT,
    actor_id TEXT,
    event_type TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE INDEX idx_command_audit_command
    ON command_audit_events (command_id, event_id);

CREATE TABLE command_reconciliation_requests (
    request_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL REFERENCES commands (command_id) ON DELETE RESTRICT,
    actor_id TEXT NOT NULL,
    resolution TEXT NOT NULL CHECK (resolution IN ('confirmed', 'failed')),
    note TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'applied', 'rejected')),
    result_reason TEXT
);

CREATE INDEX idx_reconciliation_pending
    ON command_reconciliation_requests (status, requested_at);

