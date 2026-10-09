# Buzsak Pi 3 Server Requirements V2

Status: consolidated specification for review, not an implementation report.
Date: 2026-09-29.
Source: `requirements.md`; Python baseline: `pyproject.toml`.

## 1. Purpose and Interpretation

Build a self-hosted server on a Raspberry Pi 3 Model B that collects garden-device telemetry, stores authoritative server records in SQLite, exposes a Flask API, and provides a browser dashboard. Access is limited to the local LAN and the owner's private Tailscale tailnet host route; the server shall not expose a public endpoint. The server shall support durable, validated device commands with observable outcomes.

In this document:

- **Shall** identifies a required behavior in the consolidated baseline.
- **Should** identifies a recommendation, not a release blocker.
- **Deferred** identifies work outside the initial release.
- **Proposed default** identifies a concrete starting value requiring deployment review.
- **Open decision** identifies something that the supplied requirements do not establish.

The baseline below resolves contradictions by choosing explicit, conservative behavior. These choices are recommendations for approval, not evidence that the devices already support them. No device, reference repository, or live Pi was inspected while preparing this document.

## 2. Scope and Delivery Phases

### 2.1 Initial Server Release

- Python/Flask API and browser dashboard running only on the Pi.
- SQLite persistence for device inventory, latest observations, history, health, commands, and audit records.
- Polling adapters for the PLC and valve controller.
- A durable command dispatcher, API submission and status endpoints, conflict handling, and telemetry-based confirmation for verified device actions.
- A read-only diagnostic dashboard displaying all verified, safe-to-display device parameters. Dashboard control buttons are not required initially.
- Two Sonoff MiniD devices, read-only telemetry (on/off state, reachability) via an existing, already-commissioned Matter-server instance (approved scope change, 2026-09-30 — see DEV-09), plus one narrow, verified momentary "pulse" control action for these two garage-relay devices only (approved scope change, 2026-09-30 — see DEV-10). All other Matter command execution remains deferred.
- Configuration, service supervision, SSH deployment, backup/restore, logging, and operational documentation.

### 2.2 Deferred Work

- Any Sonoff/Matter command execution beyond the two commissioned devices' single verified `pulse` action (DEV-10), and installing/operating a Matter-server for any device beyond the two already commissioned. Read-only telemetry and the momentary pulse action for those two are active (DEV-09, DEV-10).
- Interactive dashboard controls, scheduling, irrigation automation, and advanced analytics.
- Native Android application, local Room database, synchronization, and offline command outbox.
- PWA installation, service worker, and offline cache.
- Charts unless needed for diagnostics; Chart.js shall be locally vendored if adopted.
- Additional protocols such as MQTT, Modbus, or SNMP unless verified devices require them.
- Public internet access, public endpoints, cloud application dependencies, and native iOS applications. Private Tailscale access to the single Pi host route is approved post-1.0 by `docs/tailscale-remote-access-requirements.md` (TAIL-PI-01..07).

The API shall be reusable by future clients, but this does not require building those clients or their synchronization infrastructure now.

## 3. Reconciled Architecture Decisions

| Inconsistency or ambiguity | Consolidated decision |
| --- | --- |
| Flask versus FastAPI | Flask is the required framework. |
| Web service never writes versus command submission | The API reads telemetry and writes only command-related records, audit records, and required authentication metadata. It never fabricates device observations. |
| Two processes versus isolated polling and dispatch | Use three independently supervised application processes: poller, dispatcher, and web/API. This makes the stated failure isolation testable. |
| FIFO versus last-write-wins | Serialize commands per device in FIFO acceptance order. Never silently replace an accepted command. Reject conflicting active commands by default. |
| HTTP acknowledgment versus completed action | Protocol acceptance and observed target-state confirmation are distinct. Neither necessarily proves physical movement without a corresponding sensor. |
| Idempotency means no repeated actuation | API deduplication is required; exactly-once execution cannot be promised when device firmware lacks deduplication. |
| All parameters versus minimal persistence | Show all verified safe parameters, maintain latest observations, and apply separate history policies by parameter type. |
| One-second continuous logging versus outage retries | One second is the default continuous history cadence while healthy, not a promise to generate samples during outages. |
| Basic dashboard versus native/offline app | Deliver the diagnostic browser dashboard first; native and offline features are deferred. |
| App works when web/API crashes | Cached app data could remain readable later, but all live clients lose server access while the API is unavailable. Polling and dispatch remain independent. |
| Offline commands execute after reconnect | No offline replay in the initial release. A future design must prevent stale, unsafe actuation. |
| USB SSD and extra backup media assumed available | Prefer an SSD, but verify storage availability before deployment. Backups need a separate failure domain. |
| Plain LAN HTTP versus secure control | Require authenticated, trusted HTTPS for client command access; plaintext device endpoints remain an explicit LAN risk. |
| Rotating files plus journal | Use journald as the default application log destination; avoid duplicate file logging. |

## 4. Deployment Environment and Ownership

- ENV-01: Production application code and deployment artifacts shall be maintained in this repository. The older project is a reference, not a runtime dependency.
- ENV-02: All production application processes and the active SQLite database shall run on the Raspberry Pi 3 Model B. A development computer may build, test, and deploy but shall not be needed for normal operation.
- ENV-03: Deployment shall use SSH through the existing `rpi3` host alias. Current connection information is host `192.168.1.95`, user `neulas`; the private key shall remain outside the repository.
- ENV-04: The deployment shall verify the Pi OS, CPU architecture, installed Python, memory, storage, and systemd support. Python shall satisfy the current project requirement of Python 3.11 or newer; dependencies must support the actual Pi architecture.
- ENV-05: The Pi shall have a stable LAN address through a DHCP reservation or static configuration, and synchronized system time.
- ENV-06: Local telemetry, dashboard, API, and control operation shall require no cloud application service, CDN, external font, or public endpoint. Approved post-1.0 Tailscale remote access depends on Tailscale's private coordination/control plane; loss of internet may interrupt remote access but shall not stop local LAN operation. System updates and dependency installation may require internet access during maintenance.

### 4.1 Integration Inventory

| Device | Initial address | Reference source | Initial status |
| --- | --- | --- | --- |
| Garden PLC | `http://192.168.1.94/` | `C:\Scripts\Embedded\Garden_IS_ESP32_PLC_14_IO` | Active after adapter verification |
| Valve controller | `http://192.168.1.109/` | `C:\Scripts\Embedded\Garden_Valve_Control` | Active after adapter verification |
| Sonoff MiniD 1 | Matter-server `ws://192.168.1.95:5580/ws`, node_id 1 | Live `get_nodes` inspection, 2026-09-30 (docs/adapters/matter_server.md) | Active read-only telemetry after adapter verification |
| Sonoff MiniD 2 | Matter-server `ws://192.168.1.95:5580/ws`, node_id 3 | Live `get_nodes` inspection, 2026-09-30 (docs/adapters/matter_server.md) | Active read-only telemetry after adapter verification |

Previous Python implementation for reference: `C:\Scripts\Python\py_app`.

These are now four polling targets: two direct HTTP endpoints and two Sonoff switches reached indirectly through an already-running Matter-server on the same Pi (`rpi3`). Base addresses do not establish telemetry paths, action paths, payload schemas, or supported methods.

## 5. Architecture and Responsibility Boundaries

```text
PLC / Valve Controller --telemetry--> Poller ------> SQLite
PLC / Valve Controller <--commands--- Dispatcher <-> SQLite
Browser / future clients <----------> Flask API <-> SQLite
                                              |
                                      local dashboard assets
```

- ARC-01: A poller process shall own device observation acquisition and updates to latest telemetry, telemetry history, and polling health.
- ARC-02: A dispatcher process shall own command claiming, transmission, retry decisions, outcome reconciliation, and command lifecycle transitions after acceptance.
- ARC-03: A web/API process shall serve local frontend assets, query stored state, authenticate clients, validate submissions, and atomically enqueue commands. It shall not directly contact devices on behalf of a browser request.
- ARC-04: The services shall share one local SQLite database through short transactions and separate connections. No database transaction shall remain open during device network I/O.
- ARC-05: Clients shall access system data only through the API. The SQLite file shall not be exposed through SMB, HTTP downloads, or direct client access.
- ARC-06: API unavailability shall not stop polling or dispatch. A stalled device shall not block unrelated devices. Poller unavailability shall prevent claims of fresh confirmation and block new commands requiring fresh telemetry.
- ARC-07: Adapter interfaces shall separate protocol-specific parsing and actions from scheduling, persistence, and lifecycle rules. Implement only protocols needed by verified integrations.
- ARC-08: SQLite is the authoritative server record of observations and command intent. Devices and their sensors remain authoritative for actual observed device state; desired state and observed state shall remain separate.

## 6. Device Discovery and Adapter Contracts

- DEV-01: Before implementing each active adapter, inspect the relevant firmware/API and document readable parameters, endpoints, methods, value types, units, enumerations, null semantics, errors, and firmware assumptions.
- DEV-02: Each device and parameter shall have a stable identifier independent of its display name or network address.
- DEV-03: Each readable parameter shall define its category: Boolean/state, continuous measurement, counter, configuration, identity, or diagnostic metadata. History cadence shall follow the category, not merely the JSON type.
- DEV-04: Each controllable capability shall declare a validated action schema, ranges, units, target resource, safety preconditions, retry safety, expiry bounds, and a telemetry confirmation predicate. Unsupported actions shall not be advertised or executable.
- DEV-05: Device values shall preserve false, zero, null, unavailable, and invalid as distinct cases where applicable. A failed read shall never become a synthetic zero or false reading.
- DEV-06: Additive or unrecognized safe fields shall be surfaced in a diagnostic representation or identified as unmapped. Secrets and oversized payloads shall not be exposed. Breaking response changes shall produce a visible adapter error.
- DEV-07: Each active adapter shall have representative response fixtures and failure tests. Reference projects shall not be modified or become runtime imports of this server.
- DEV-08: A Matter integration that is not yet configured shall appear as not configured, advertise no executable capabilities, and produce no polling traffic or recurring offline alarms.
- DEV-09 (approved scope change, 2026-09-30): The two Sonoff MiniD devices are commissioned on an existing `python-matter-server` instance (`ws://192.168.1.95:5580/ws`, node_id 1 and node_id 3, verified live via its `get_nodes` command — see docs/adapters/matter_server.md). Read-only telemetry (on/off state, per-node `available` reachability) is in scope for the initial release; issuing Matter commands (turning a switch on/off) remains deferred until the command dispatcher (Section 9) exists for the primary two devices.
- DEV-10 (approved scope change, 2026-09-30): The two Sonoff MiniD switches are physical garage door opener relays (node_id 1 = GARAGE-RIGHT, node_id 3 = GARAGE-LEFT) that shall only ever operate in momentary "pulse" mode (0.5s on, then off), never a persistent on/off toggle. A single verified capability, `pulse`, issues Matter's native OnOff cluster `OnWithTimedOff` command (cluster 6, command 0x42; verified against both nodes' real `AcceptedCommandList` and the `home-assistant-libs/python-matter-server` client source, not guessed) with a fixed 0.5s on-time and 0.5s off-wait-time, so the device's own firmware performs the timed transition and a dropped connection mid-pulse cannot leave the relay stuck on. This is a minimal, narrowly scoped exception approved explicitly in place of building the full command dispatcher (Section 9, CMD-01..19): no idempotency keys, no authenticated-actor tracking, and above all no automatic retry of a failed or ambiguous pulse (CMD-09, CMD-10) — a failed, expired, or uncertain outcome is terminal and requires a new, distinct human-issued request. The web/API only validates and enqueues a `pulse_requests` row (ARC-03); only the dispatcher process contacts the device. A minimum 3-second gap between accepted requests per device and a `status=healthy` precondition at acceptance time are enforced as partial mitigations, but this capability is still exposed on the same unauthenticated LAN dashboard as the rest of the initial release (SEC-01..10 remain unimplemented). No other Matter command is advertised or executable for any device.

## 7. Polling, Freshness, and History

- POL-01: Enabled targets shall be polled automatically using configurable schedules. The proposed default for active telemetry endpoints is one second, subject to verified device capacity.
- POL-02: A device shall not have overlapping polls for the same endpoint. Missed intervals shall not create an unbounded catch-up backlog. Use bounded concurrency so an unreachable target cannot consume all polling capacity.
- POL-03: Every network operation shall have finite connection and response timeouts and bounded response size. Failures shall use capped exponential backoff with jitter and a per-device circuit breaker/cool-down.
- POL-04: The server shall record successful and failed poll times, latency, consecutive failures, next retry time, and a sanitized last error separately from telemetry history.
- POL-05: Store timestamps in UTC using a documented API format. Distinguish server observation time from optional device-reported time. Device clocks shall not control ordering or freshness decisions.
- POL-06: Use a monotonic clock for in-process intervals and timeouts. Detect significant wall-clock changes and unsynchronized time; do not extend command lifetime or dispatch stale commands because the clock moved backward.
- POL-07: Every valid successful poll shall refresh the latest observation and its freshness, even if its value did not change. Invalid or missing fields shall retain their last valid values with explicit quality/freshness information.
- POL-08: Boolean, enumeration, and discrete-state history shall contain the initial valid observation and subsequent observed value changes only. An unchanged poll shall not append another state-history row.
- POL-09: Continuous measurements shall be sampled into history at a configurable cadence, default one second, including unchanged values. Store at most one selected fresh sample per metric per cadence interval; never duplicate an old value merely to fill a gap.
- POL-10: Configuration and identity values shall be stored initially and on observed change. Counter policy shall be explicit per metric. No numeric change threshold or deadband shall silently discard readings.
- POL-11: Each parameter shall expose last observed time, last value-change time, quality, and staleness. Proposed staleness default: three times its configured nominal observation interval. Backoff shall not make stale data appear fresh.
- POL-12: Recovery shall update health and preserve outage gaps. Re-observing the same discrete value after an outage shall refresh freshness without requiring a duplicate state-history entry.
- POL-13: Polling only detects sampled changes. Short transitions between polls may be missed; complete event capture shall require a verified device event counter, event log, or push protocol.

## 8. Persistence and Data Integrity

- DB-01: SQLite shall use WAL mode, foreign-key enforcement, a bounded busy timeout, and an explicitly documented durability setting. Use `synchronous=FULL` as the initial durability baseline, then measure its performance on the actual storage.
- DB-02: The schema shall represent devices, parameter definitions, capabilities, latest observations, telemetry history, device/service health, commands, dispatch attempts, and append-only command audit events. Physical table names may differ.
- DB-03: Telemetry records shall associate typed values and timestamps with stable device/parameter identifiers. Latest observations shall include quality and a revision suitable for checking relevant state changes.
- DB-04: Commands shall persist identity, authenticated actor, client origin, device/action/parameters, idempotency key and request fingerprint, acceptance sequence, lifecycle state, timestamps, deadline, preconditions, attempt count, confirmation evidence, and structured outcome/error details.
- DB-05: Indexes shall support bounded per-device/per-metric time queries and pending-command selection. Queue size, history page size, and query duration shall be bounded.
- DB-06: Concurrent writers shall handle lock contention with bounded waits and retries. Queue acceptance, idempotency checks, cancellation, and command claiming shall be transactional; only one dispatcher instance shall own a device's active execution.
- DB-07: Schema changes shall use versioned migrations with a pre-migration backup and documented compatibility/rollback rules. Services shall reject unsupported schema versions clearly.
- DB-08: Retention shall be configurable separately for telemetry, health events, command history, audit records, and idempotency records. Ninety days of raw telemetry is an example, not an approved default until storage is sized.
- DB-09: Retention shall preserve latest observations and active commands. Cleanup shall use bounded batches, avoid blocking normal operation, and retain enough preceding state to interpret change-only history at a query boundary.
- DB-10: Idempotency records shall remain available for the documented client retry window even after other command data is archived. Expired keys shall not silently authorize replay of a previously executed request within that window.
- DB-11: The database shall reside on a local filesystem, preferably a USB SSD. Network shares shall not host the live WAL database. MicroSD-only operation requires explicit wear, capacity, and backup review.
- DB-12: Disk capacity and WAL growth shall be monitored. Storage failure shall surface as degraded readiness; no command shall be accepted or sent without a durable record. WAL does not guarantee survival of faulty storage or all power-loss scenarios.

Capacity shall be estimated before selecting retention: continuous history rows per day are approximately `continuous_metric_count * 86400 / history_interval_seconds`, plus state changes and operational records. Estimate index and WAL overhead from a measured sample database rather than assuming a fixed bytes-per-row value.

## 9. Command Lifecycle and Safety

### 9.1 Lifecycle

| State | Meaning |
| --- | --- |
| `pending` | Validated and durably accepted; no transmission attempted. |
| `dispatching` | Claimed durably; a transmission may have occurred. |
| `sent` | Transmission is known to have occurred; application acceptance is not yet established. |
| `acknowledged` | Device/protocol acceptance was received, but the target state is not yet confirmed. |
| `confirmed` | Fresh, valid telemetry satisfies the action's documented confirmation rule. |
| `failed` | A definitive rejection or known failure was established, with a reason. |
| `expired` | The deadline passed before any transmission attempt. |
| `cancelled` | An authorized actor cancelled the command before dispatch. |
| `uncertain` | Execution may have occurred but the final outcome cannot safely be determined. |

Normal path: `pending -> dispatching -> sent -> acknowledged -> confirmed`. Protocols without separate acknowledgment may skip `acknowledged`; synchronous responses may record intermediate transitions in one transaction. `pending` may instead become `cancelled`, `expired`, or `failed`. An in-flight command may become `failed` or `uncertain` rather than confirmed.

An in-flight deadline does not prove that the device did nothing: it shall produce `uncertain` unless there is definitive failure evidence. Late evidence shall remain auditable; an uncertain command may be reconciled to confirmed or failed only through a documented, attributable reconciliation event.

### 9.2 Required Behavior

- CMD-01: Authenticated submission shall validate device capability, action parameters, authorization, freshness, safety preconditions, conflicts, idempotency, and deadline before accepting a command.
- CMD-02: Acceptance shall atomically persist the command and audit event before returning its identifier. Acceptance is not actuation success.
- CMD-03: Idempotency keys shall be scoped to the authenticated client/principal. Reusing a key with the same request shall return the original command; reuse with different contents shall return a conflict.
- CMD-04: Commands shall have a server-authoritative, persisted expiry deadline derived from an allowed action-specific lifetime. Retries and restarts shall not reset it. No unlimited-lifetime command is allowed.
- CMD-05: The dispatcher shall recheck expiry, authorization-relevant constraints, freshness, and safety immediately before sending. It shall durably record an attempt before network I/O and reconcile interrupted attempts after restart.
- CMD-06: An HTTP success code or transport acknowledgment alone shall not confirm a command. Adapter-specific error bodies and application-level rejection shall be interpreted.
- CMD-07: Confirmation shall use fresh telemetry tied to the relevant device, resource, and attempt, normally observed after dispatch. Continuous values shall use documented tolerances and any required settling period. Unrelated telemetry shall not confirm an action.
- CMD-08: A desired-state command whose target is already satisfied may complete without transmission only after a fresh validation read and an explicit already-satisfied outcome. This shall not be used for pulse, toggle, or other non-idempotent actions.
- CMD-09: Retrying a transmission shall require verified device-side idempotency or an adapter-specific replay-safe desired-state operation. API deduplication alone shall not justify retrying an ambiguous pulse/toggle request.
- CMD-10: On a timeout, crash, or lost response after possible transmission, reconcile by fresh observation first. If execution cannot be determined safely, mark uncertain and require operator reconciliation; do not blindly resend.
- CMD-11: Definitive failure, deadline exhaustion, retry exhaustion, and uncertain outcome shall be distinguishable in the API. Retry limits and delays shall be configurable within capability-specific safety bounds.
- CMD-12: If a target is offline before transmission, its command may remain pending with backoff until expiry. No transmission shall start after expiry. Device recovery shall not revive expired or cancelled commands.
- CMD-13: The system shall serialize execution per device. Acceptance order shall be durable and independent of client timestamps. By default, a second command affecting a resource with an active or unresolved command shall receive a conflict, not replace it.
- CMD-14: Nonconflicting commands may queue in FIFO order with bounded depth; any broader queuing policy must explicitly define safe interactions. An unresolved uncertain action shall block further commands that could conflict with it.
- CMD-15: Clients shall be able to attach expected relevant-state revisions. The API and dispatcher shall reject stale preconditions. Observation timestamps alone shall not invalidate a revision on every unchanged poll.
- CMD-16: The originator or an authorized administrator may cancel a pending command. Cancellation shall race safely with claiming; after dispatch begins, cancellation shall return a conflict and shall not imply that actuation stopped.
- CMD-17: Command history shall identify the authenticated actor separately from a client-provided origin such as web, app, or API. Every transition, retry, rejection, cancellation, and reconciliation shall be auditable without recording credentials.
- CMD-18: Hardware interlocks and firmware safety limits shall remain authoritative. The server shall not assume it can enforce emergency stops or maximum actuator-on time while the Pi/network is unavailable. Actions lacking adequate verified safety and observable confirmation shall remain disabled.
- CMD-19: Reported relay/valve output state shall not be presented as physical valve movement, pressure, or flow unless that fact is actually measured. Confirmation semantics shall be visible in capability metadata.

## 10. API Contract

- API-01: The Flask application shall run behind a production-capable WSGI server, not Flask's development server, and expose a documented versioned API such as `/api/v1`.
- API-02: Read endpoints shall provide device inventory, capabilities, parameter metadata, latest observations, freshness/quality, health, bounded historical queries, and command status/history.
- API-03: Command endpoints shall support submission, lookup, and pending cancellation. A capability or deployment-level read-only mode shall disable command submission explicitly.
- API-04: JSON contracts shall document UTC timestamps, units, typed values, missing values, pagination, filtering, and stable error codes. Historical queries shall have bounded ranges and deterministic ordering.
- API-05: Accepted commands shall return HTTP 202 with command ID and status URL. The contract shall distinguish invalid input, unauthenticated access, forbidden action, missing resource, conflict, rate limit, and service unavailable. Deduplicated requests shall return the original identity with documented response semantics.
- API-06: The API shall validate request content types, sizes, fields, and parameter bounds; reject unsupported actions; and sanitize errors. Device addresses shall come from trusted configuration, not arbitrary client URLs.
- API-07: Separate liveness and readiness endpoints shall distinguish a running process from a usable database/schema and fresh service heartbeats. Detailed device/network diagnostics shall require authentication.
- API-08: Queue depth, database failure, or unavailable control infrastructure shall cause explicit backpressure/service-unavailable responses rather than unbounded acceptance or false success.
- API-09: Browser and future native clients shall use the same lifecycle contract. Initial release shall not promise an offline synchronization cursor, replica protocol, or safe queued offline actuation.

## 11. Initial Dashboard

- UI-01: The root browser page shall be a usable diagnostic dashboard, grouped by device, with responsive desktop and mobile layouts.
- UI-02: It shall display every verified safe-to-display readable parameter, with name, value, units where applicable, type/state meaning, observation time, and quality/freshness. Empty, unsupported, stale, and invalid values shall be distinguishable.
- UI-03: Device summaries shall show configured/enabled status, connectivity, last successful poll, and sanitized errors. A Matter integration that is not yet configured shall be visibly not configured rather than falsely healthy or offline (DEV-08); the two commissioned Sonoff switches (DEV-09) shall show real health/telemetry like any other device.
- UI-04: The page shall refresh from the local API without full-page reload. Proposed refresh interval: two seconds. Refresh failure shall retain visibly stale last-loaded data and show loss of server connectivity.
- UI-05: No browser request shall directly poll or control a device. No credentials shall be embedded in static JavaScript or URLs.
- UI-06: The initial UI shall be read-only for device control. Displayed observations shall never be optimistically replaced by requested command values.
- UI-07: Assets shall be served locally. The UI shall support keyboard navigation, labeled controls, readable contrast, and non-color-only state indicators, without overlapping content on supported mobile/desktop viewports.
- UI-08: PWA/offline caching is deferred. If added later, it shall require a supported secure context, explicit cached-data timestamps, and no implied ability to control devices while disconnected.

## 12. Security

- SEC-01: Firewall and bind/proxy configuration shall restrict access to the verified approved LAN subnet and, for approved post-1.0 remote access, the private Tailscale tailnet. The Pi may advertise only `192.168.1.95/32`; do not advertise the home subnet or act as an exit node. No router port forwarding, Funnel, public DNS, or public firewall rule is allowed. Tailnet reachability does not replace HTTPS or server authentication.
- SEC-02: Client command access shall require trusted HTTPS and authentication. A self-signed certificate is acceptable only with explicitly configured client trust. A reverse proxy may terminate TLS while the Flask listener remains local.
- SEC-03: Device HTTP links, where required by firmware, shall be documented as plaintext LAN traffic and isolated through network policy where practical. Browser-to-server TLS does not secure server-to-device HTTP.
- SEC-04: Telemetry access shall require authentication by default. Explicitly configured anonymous read-only access may be approved, but command access shall never be anonymous.
- SEC-05: Support at least viewer and operator authorization. Administrative configuration and reconciliation operations shall have explicitly restricted permissions. Individually attributable credentials are preferred over a household-wide shared token.
- SEC-06: API clients may use bearer tokens. Browser authentication shall avoid long-lived tokens in local storage; use secure, HttpOnly, appropriately SameSite session cookies with CSRF protection for state-changing requests when using cookie authentication.
- SEC-07: Secrets shall stay outside source control in restricted deployment configuration or a protected service credential mechanism. Tokens, private keys, and passwords shall not appear in logs, API responses, telemetry payload views, or backup documentation.
- SEC-08: Services shall run without root privileges, with minimum filesystem/network permissions. SSH deployment credentials shall not be available to the running application.
- SEC-09: Requests and command submission shall have appropriate rate/size limits. Use same-origin browser access by default; broad CORS access shall not be enabled.
- SEC-10: Authentication shall support credential revocation/rotation. Backup access and any retained sensitive operational data shall receive equivalent filesystem/access protection.

## 13. Configuration, Deployment, and Operations

- OPS-01: A versioned YAML configuration shall define device identifiers, adapters, addresses, enabled flags, polling and history intervals, stale thresholds, timeouts/backoff, command limits, retention, database path, and listen settings. Secrets shall be supplied separately.
- OPS-02: Configuration shall be validated at startup, with clear errors for invalid types, duplicate identifiers, unsafe intervals, or unsupported capabilities. Commit a documented nonsecret example configuration.
- OPS-03: Configuration changes may require restarting affected services; hot reload is not required. Invalid configuration shall not partially enable control.
- OPS-04: systemd shall independently supervise poller, dispatcher, and web/API services, start them at boot, and restart unexpected failures with a bounded restart policy. A proposed restart delay is ten seconds.
- OPS-05: Web/API startup shall not require a running poller, but it shall report stale/unavailable data and degraded control readiness accurately. A health endpoint alone shall not be described as a systemd watchdog integration.
- OPS-06: Deployment over SSH shall be repeatable: install pinned compatible dependencies in an isolated environment, validate configuration, back up before migration, update the release, restart services, and check health. Record the deployed application version.
- OPS-07: Provide rollback instructions covering both code and schema compatibility. Keep runtime data/configuration outside replaceable release directories. Never delete the database as part of a routine deployment.
- OPS-08: Application logs shall go to journald with bounded retention. Include UTC timestamps, severity, service/device/command identifiers, and sanitized errors. Repeated outages shall be rate-limited or summarized rather than logged every second.
- OPS-09: Monitor process heartbeat, poll latency/failures, stale devices, queue depth/age, uncertain commands, database contention, disk/WAL size, backup age, and clock synchronization status.
- OPS-10: Backups shall use SQLite's online backup API or `.backup`, not an uncoordinated copy of a live database/WAL pair. Proposed schedule: daily and before migrations; final retention and destination require approval.
- OPS-11: At least one backup copy shall be on a separate device or other failure domain. Verify backup readability and perform a documented restore drill. Keep restricted configuration material needed for recovery separately backed up.
- OPS-12: Restore shall occur with services stopped and commands quarantined until reviewed against current device state. Restoring an old queue shall not automatically replay possibly completed actions.
- OPS-13: Controlled shutdown shall stop new claims, finish or safely record bounded in-flight work, and close connections. After restart, reconcile interrupted commands before enabling conflicting dispatch.
- OPS-14: If free space reaches a configured safety threshold or SQLite becomes unavailable, reject new commands, stop unsafe dispatch, and report the fault. Recovery shall not silently discard authoritative command records.

### 13.1 Approved Post-1.0 Tailscale Access

The detailed owner-approved requirements and acceptance scope are recorded in the Buzsak App repository's `docs/tailscale-remote-access-requirements.md` (2026-10-08). These requirements do not enable additional actuation.

- TAIL-PI-01: The Pi shall join the owner's private Tailscale tailnet and restore the service after reboot.
- TAIL-PI-02: The Pi shall advertise only `192.168.1.95/32`; the route shall be approved in tailnet policy. Do not advertise the home subnet or enable exit-node service for this feature.
- TAIL-PI-03: Do not expose the API through Funnel, public DNS, public port forwarding, or a public firewall rule.
- TAIL-PI-04: Preserve the Caddy HTTPS listener, `https://192.168.1.95` URL, and pinned root CA. Tailscale requests reach the same API and retain bearer authentication and viewer/operator role checks.
- TAIL-PI-05: Tailnet policy shall allow all tailnet members to reach the Buzsák HTTPS service on TCP 443 as approved by the owner; server authentication and roles remain independently enforced. Do not make SSH, legacy HTTP, or other Pi services reachable merely because the host route exists.
- TAIL-PI-06: Before declaring remote access complete, verify from an authorized physical phone outside the home LAN over cellular that the official Tailscale app reaches the existing HTTPS URL and retrieves authenticated state. Turning Tailscale off shall produce the existing offline behavior; reconnecting shall recover without a service restart.
- TAIL-PI-07: Network acceptance shall not send valve or garage commands. Any later live actuation test requires separate explicit approval under Section 15 and the device safety requirements.

## 14. Performance and Reliability Targets

These are proposed qualification targets, to be measured on the actual Pi and approved before release. They are not claims about the current implementation or device capacity.

- NFR-01: Sustain the configured one-second healthy telemetry schedule for both current targets without accumulating poll work, while serving two concurrent dashboard sessions.
- NFR-02: Keep p95 server-side latency below 500 ms for latest-state/device-list API requests under that workload, excluding client network/TLS setup; history requests shall remain bounded and shall not starve collection.
- NFR-03: With one-second collection and two-second UI refresh, aim to display healthy observed changes within four seconds, allowing for normal LAN/device response time.
- NFR-04: Poller, dispatcher, and web/API combined should remain within a proposed 300 MiB steady-state resident-memory budget, subject to measurement and explicit adjustment. The Pi shall not enter sustained memory-pressure or swap thrashing.
- NFR-05: A 24-hour soak test shall show no unbounded queue, connection, memory, or WAL growth and no unexplained healthy-period history gaps. Device outages shall be distinguishable from scheduling failures.
- NFR-06: Service restarts and a Pi reboot shall preserve durable records. No restart shall cause blind replay of an ambiguously transmitted non-idempotent command.
- NFR-07: With daily backups, the proposed disaster-recovery data-loss window is up to 24 hours; recovery-time target remains an open decision. Ordinary service restarts shall not depend on restoring a backup.

## 15. Acceptance and Verification

Automated tests shall use simulated devices/fixtures for error cases. Hardware tests shall be explicitly controlled and shall not actuate irrigation equipment without a reviewed safe test setup.

| Test | Acceptance evidence |
| --- | --- |
| AT-01: Pi deployment | A clean deployment starts all services on the Pi, reports the deployed version, and runs without the development computer or internet. |
| AT-02: Integration inventory | Firmware/endpoint inventory maps every readable parameter to the dashboard and records each supported action and confirmation signal. Unsupported actions remain disabled. |
| AT-03: Change-only history | Initial false/zero/state values are retained; repeated unchanged state polls add no history; a detected change adds one event and refreshes latest state. |
| AT-04: Continuous history | Fresh constant and varying measurements are stored at the configured one-second cadence; faster polls do not exceed it; outages produce gaps, not fabricated samples. |
| AT-05: Fault isolation | One target timing out, returning invalid JSON, or oversized data does not stop the other target. Staleness, backoff, recovery, and missing-field quality are visible. |
| AT-06: Service independence | Stopping web/API leaves polling and dispatch running. Stopping polling prevents fresh confirmations and unsafe new dispatch; API still reports last known data as stale. |
| AT-07: Command confirmation | A valid API request is durably accepted; acknowledgment alone never confirms; matching fresh telemetry confirms according to capability semantics. |
| AT-08: Failure and ambiguity | Rejection, acknowledged-but-unconfirmed action, network timeout, and unavailable confirmation each produce the specified distinct outcome and audit evidence. |
| AT-09: Deduplication | Concurrent same-key/same-payload requests create one command; same-key/different-payload requests conflict. Ambiguous unsafe actions are not retransmitted. |
| AT-10: Conflict and cancellation | Conflicting actors receive deterministic conflicts; allowed commands retain FIFO order; cancellation cannot race into a falsely cancelled transmitted command. |
| AT-11: Preconditions and expiry | Stale revisions, stale telemetry, expired pending commands, unsafe parameters, and clock changes cannot bypass dispatch checks. |
| AT-12: Crash windows | Restart at claim, send, and response-persistence boundaries preserves intent, reconciles possible execution, and avoids unsupported exactly-once assumptions. |
| AT-13: Security | Anonymous/unauthorized commands fail; CSRF protections work where applicable; secrets are absent from assets/logs; access outside the permitted LAN/private tailnet is blocked; no public endpoint exists. |
| AT-14: API contracts | Pagination, query limits, error codes, command status lookup, read-only mode, and health/readiness responses match the documented contract. |
| AT-15: Dashboard | Desktop and mobile views show all inventory parameters, stale/unavailable states, loss of API connectivity, the two Sonoff switches' live telemetry, and any unconfigured Matter integration without layout failures. |
| AT-16: Storage | Concurrent writes and readers, lock contention, retention, migrations, disk-full conditions, and WAL checkpoint behavior meet data-integrity requirements. |
| AT-17: Backup and restore | Online backup passes integrity checks; a restore drill recovers data/configuration and quarantines restored commands without automatic replay. |
| AT-18: Load and endurance | Measured Pi results meet the approved targets in Section 14, including a 24-hour soak, and record the tested firmware/configuration. |
| AT-19: Tailscale remote access | From an authorized phone on cellular outside the home LAN, official Tailscale reaches only the approved `192.168.1.95/32` host route, validates the unchanged HTTPS CA, and retrieves authenticated state. Disconnect/reconnect works without a restart. No device command is sent. |

## 16. Required Project Deliverables

- Packaged Python application, declared dependencies, and reproducible installation instructions.
- PLC and valve-controller adapters, parameter/capability inventory, and test fixtures.
- SQLite schema, versioned migrations, retention and safe backup/restore tooling.
- Flask API contract and local diagnostic dashboard assets.
- Poller, dispatcher, and web/API systemd units plus a documented TLS deployment arrangement.
- Nonsecret example YAML configuration and documented secret provisioning.
- Automated tests covering the lifecycle, persistence, security, and adapter contracts.
- Deployment, upgrade, rollback, troubleshooting, and recovery runbooks.
- Post-1.0 Tailscale host-route configuration and off-LAN acceptance record (TAIL-PI-01..07).
- Pi acceptance-test results, approved defaults, and a list of remaining limitations.

## 17. Open Decisions and Implementation Gates

These questions do not prevent building the server framework, but the relevant integration or deployment shall not be declared complete until they are resolved.

| Decision | Required resolution | Gate |
| --- | --- | --- |
| Pi platform | OS/version, architecture, usable Python 3.11+, RAM headroom, and package availability | Deployment |
| Storage | SSD availability, live database path/capacity, free-space threshold, and backup destination | Persistence deployment |
| Device telemetry | Actual endpoints, schemas, units, update rates, and safe polling frequency | Each adapter |
| Device control | Supported actions, authentication, deduplication support, physical interlocks, and trustworthy confirmation signals | Enable each action |
| Command policy | **Approved 2026-10-07 for initial valve actions:** 12s lifetime, one send attempt, 5s telemetry-confirmation window, reject if offline at submission, queue depth at most 3 per device, `set_mode` confirms mode plus all relays off, and uncertain outcomes require admin reconciliation. | Initial relay4 set/restore test passed 2026-10-07; each other live action requires explicit approval |
| LAN/tailnet security | Verify approved LAN subnet/firewall, tailnet route policy, HTTPS certificate trust, user identities, and HTTPS-only listener reachability | Client access |
| Tailscale route | Verify Pi forwarding/firewall behavior for the `192.168.1.95/32` route, tailnet approval, TCP-443-only tailnet policy, and physical off-LAN phone test; advertise no broader route | Post-1.0 remote access |
| Retention/recovery | Measured database growth, history/audit/idempotency retention, backup retention, approved RPO and recovery-time target | Production acceptance |
| Qualification targets | Approve or revise the numeric defaults and targets after measuring actual Pi/device behavior | Release acceptance |
| Matter | Command/control contract for the two Sonoff MiniD switches beyond the single verified momentary `pulse` action | Telemetry and pulse control verified 2026-09-30 (DEV-09, DEV-10); all other Matter commands remain deferred |
| Future clients | Whether PWA or native Android is needed; safe offline intent/expiry rules and synchronization contract | Deferred client phase |

## 18. Definition of Done

The initial server release is complete when its code and operational artifacts are in this repository, it runs autonomously on the Pi, all active integrations (PLC, valve controller, and the two Sonoff MiniD read-only telemetry parties, DEV-09) pass their inventory and telemetry checks, the diagnostic dashboard exposes the verified parameters, and the API/dispatcher safely supports the approved device actions, including the single verified Sonoff MiniD `pulse` action (DEV-10). Security, backup/restore, failure recovery, and approved performance targets shall pass acceptance testing. Any Sonoff/Matter command beyond the verified `pulse` action, and any further Matter devices, remain deferred and shall not be represented as implemented, and any blocked device action or approved exception shall be documented explicitly.