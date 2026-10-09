# Backlog

Open, not-implemented, or not-decided items surfaced while building the
persistence layer and adapters. This is a working list, not a
specification — `requirements.md` remains authoritative; entries here cite
requirement IDs where one exists.

## Persistence (DB-xx)

- **DB-12 storage-failure behavior**: disk-full / corrupt-WAL handling is
  not implemented or tested. Readiness currently only checks `SELECT 1`
  succeeds (`/readyz`); it does not check free disk space or WAL size.
- **DB-08 / DB-09 retention & cleanup**: no periodic job trims
  `observations_history` or bounds table growth. Capacity has not been
  estimated from a measured sample database (requirements.md Section 8).
- **DB-07 migration rollback under real failure**: `schema.py`'s
  `ROLLBACK`-on-exception path exists but has only been exercised by
  raising from a wrapped connection in a test, never by an actual failing
  SQL statement inside a real migration file.
- **OPS-10 / OPS-11 backup & restore**: `deploy/backup_db.py` now takes a
  minimal local SQLite online backup (`.backup()` API) before migrations
  as part of the OPS-06 deploy (partial OPS-10 only). No retention policy,
  no off-device copy, and no restore drill exist yet; retention/destination
  are still open decisions per requirements.md Section 16.
- **No auto-migration on startup**: none of the three process entry points
  (poller, dispatcher, web/API) call `schema.apply_migrations()`
  automatically. `deploy/migrate.py` now runs it as part of the OPS-06
  deploy tooling (`deploy/deploy.ps1`); manual invocation (see README.md)
  is still needed for a from-scratch local setup outside that flow.

## Device registration (DEV-xx)

- **Config→DB sync (resolved)**: `device_registration.sync_devices()`
  upserts `config.yaml`'s `devices` list (id/kind/address/enabled) plus
  their catalog parameters/capabilities (`adapters/catalog.py`) into the
  `devices` / `parameters` / `capabilities` tables, in one transaction per
  call. `deploy/sync_devices.py` now runs it as part of the OPS-06 deploy
  tooling (`deploy/deploy.ps1`); manual invocation (see README.md) is
  still needed for a from-scratch local setup outside that flow.
- **Kind-string inconsistency (resolved)**: `adapters/registry.py` maps
  both the hyphenated config spelling (`"garden-plc"`,
  `"valve-controller"`) and each adapter's own underscored `DEVICE_KIND`
  to the canonical underscored form stored in the database. An
  unrecognized `kind` raises `UnknownDeviceKindError` instead of silently
  defaulting.
- **DEV-08 Matter placeholders**: no Matter devices exist in config or DB
  yet (requirements.md notes addresses are "not supplied"). The dashboard
  shows a static "not configured" panel for this party rather than reading
  real rows.
- **Catalog does not track removals**: `adapters/catalog.py` upserts
  parameters/capabilities but never deletes rows that a future catalog
  change removes, and `sync_devices()` never removes a device that
  disappears from `config.yaml`. Acceptable for now (single operator,
  small static catalog) but should be revisited before the catalog
  changes often.

## Dashboard (UI-xx)

- **Live content now confirmed (2026-09-30)**: with explicit approval, both
  devices were flipped to `enabled: true` in the Pi's preview config and
  polled for real; the dashboard rendered live `pressure_bar`,
  `water_level_liters`, relay/pump states, sensor readings, and
  `healthy` device status, updating every poll cycle. `config/example.yaml`
  in the repo remains `enabled: false` — this was a scoped live test on
  the Pi's preview deployment only, not a change to the shipped default.
- **Controls: pulse capability now implemented for Sonoff relays (DEV-10,
  resolved 2026-09-30)**: garage relay devices (`sonoff-1`=GARAGE-RIGHT,
  `sonoff-2`=GARAGE-LEFT) now have a real, working `pulse` capability
  button — the *only* enabled capability in the release; every other
  capability row (PLC/valve) remains a visible-but-disabled placeholder
  per UI-06. See `docs/adapters/matter_server.md` "Command contract" and
  requirements.md DEV-10 for the full design (scoped exception to
  CMD-01..19, web/API enqueues via `pulse_requests` table, dispatcher
  executes via `OnWithTimedOff`, never auto-retries). **Real hardware has
  not yet been actuated** — the user will trigger the first live pulse
  personally; all verification so far is fake-server/unit tests
  (112 passing) plus a live read-only `AcceptedCommandList` inspection.
- **Valve controls: deployed and enabled under operator authentication
  (2026-10-07)**: the authenticated command API and dispatcher are live
  behind Caddy HTTPS; Gunicorn is loopback-bound and the production
  `control.valve_enabled` gate is true. The browser dashboard remains
  read-only. The approved relay4 LIGHT test changed false to true, received
  fresh telemetry confirmation, then restored false and confirmed again.
  No water-valve output or mode was changed. Each other physical action
  still needs its own explicit approval.
- **Real-browser testing (done, including live telemetry)**: the preview
  deployment on the Pi (`~/buzsak-pi3-server-preview/`) was verified in an
  integrated browser over the LAN with both real devices polling live —
  header, tab switching, KPI cards, live values, and disabled controls all
  render correctly. No phone-sized-viewport or accessibility-tooling review
  has been done yet. **OPS-06 cutover complete (2026-09-30)**: the
  repeatable, pinned-dependency release process (`deploy/deploy.ps1` +
  `deploy/systemd/*`) has been run against the Pi for real, including a
  live `-ApplyServices` cutover. Production-named units
  (`buzsak-poller.service`, `buzsak-dispatcher.service`,
  `buzsak-web.service`) are now enabled + active on the Pi; the old
  `-preview` units are disabled and stopped. `config/production.yaml` is
  in place on the target. The dashboard's client-side auto-refresh is
  1 second (`app.js`). A server-wide health badge was added beside the
  dashboard title (`#server-health`), distinct from per-device badges.
- **Authentication is active; LAN policy remains open**: Caddy HTTPS,
  hashed/revocable viewer/operator/admin credentials, secure browser
  sessions, CSRF checks, and the session secret are deployed. The
  development Windows user trusts Caddy's internal root CA. Other clients
  need that trust anchor and their own credential. Approved LAN subnet and
  firewall/no-port-forwarding policy still need verification; port 80 is a
  separate legacy Nginx service and is not the Buzsák app.
- **Tailscale/Android remote access (approved post-1.0, 2026-10-08)**: the
  Pi's Tailscale daemon is installed, enabled, and online, and now advertises
  only `192.168.1.95/32`. Tailnet route approval and effective TCP-443-only
  policy are not yet verified. Current Tailscale input rules accept traffic
  arriving on `tailscale0`, and SSH plus legacy Nginx bind broadly; confirm
  tailnet ACL blocks those ports before approving the route. An authorized
  phone's cellular acceptance test remains open (TAIL-PI-01..07).

## Poller / Dispatcher (ARC-01, ARC-02)

- **Poller (ARC-01, resolved for garden_plc/valve_controller)**: one thread
  per enabled device (`poller/scheduler.py`) fetches `GET api/state`,
  parses it via the adapter, flattens it via `poller/extraction.py`, and
  calls `observations.record_observation()` per parameter plus
  `poller/health.py` for device_health. Capped exponential backoff with
  jitter (`poller/backoff.py`) doubles as the circuit-breaker/cool-down;
  a device is reported "offline" after `OFFLINE_AFTER_CONSECUTIVE_FAILURES`
  (5) consecutive failures. Bounded concurrency (POL-02) comes from one
  thread per device rather than a shared pool — acceptable for today's
  two-device fleet; revisit if the fleet grows much larger.
- **Continuous-cadence simplification (open decision)**: POL-09 wants
  continuous metrics sampled at a *configurable cadence* separate from the
  raw poll interval. This implementation does not separate the two — one
  poll cycle always produces exactly one history sample for continuous
  parameters, so raising the poll rate would also raise continuous history
  density. Fine at today's 1 Hz default; revisit before polling faster.
- **`urlopen`'s single timeout (documented simplification)**: `http_client.py`
  cannot express separate connect/read timeouts with stdlib `urllib`
  without raw sockets; one `request_timeout_seconds` covers both.
- **Run against real hardware (done, 2026-09-30, approved live test)**:
  automated tests still use a local `http.server` fixture
  (`tests/test_poller_scheduler.py`, `test_poller_http_client.py`), but the
  poller has now also been run for real against `garden-plc`
  (192.168.1.94) and `valve-controller` (192.168.1.109) from the Pi,
  confirmed via live dashboard values and `device_health` rows. It runs
  under systemd (`buzsak-poller-preview.service`) with `Restart=on-failure`,
  `RestartSec=10` (OPS-04) and light sandboxing (`ProtectSystem=strict`,
  `ReadWritePaths=data/`). `config/example.yaml` in the repo still ships
  with both devices `enabled: false`; any further real-hardware change
  needs its own explicit approval per `.github/copilot-instructions.md` §5.
- **Valve command lifecycle (deployed, 2026-10-07)**:
  schema migration 0003 adds revocable hashed API credentials, durable
  commands/idempotency/attempt/audit records, and admin reconciliation
  requests. `/api/v1` submit/status/cancel/reconcile routes require trusted
  HTTPS and viewer/operator/admin credentials. Valve `set_output` and
  `set_mode` validate health, freshness, mode/relay preconditions, revisions,
  conflicts, and queue depth before durable acceptance. The dispatcher
  records one attempt before network I/O, never retries an ambiguous send,
  confirms from fresh post-attempt poller telemetry, quarantines interrupted
  attempts as `uncertain`, and applies admin reconciliation requests from its
  own lifecycle loop. The automated tests use an in-process simulated valve
  controller; the one live relay4 test is recorded above. `control.valve_enabled`
  is true in the protected production config. Remaining gates: verify the
  approved LAN subnet/firewall and import Caddy's CA plus provision unique
  credentials for each additional client.
- **Thread-liveness watchdog (added 2026-10-05, OPS-04)**: a real incident
  on the Pi showed all 4 per-device poller worker threads had silently
  died (one on 2026-10-03, three more on 2026-10-04) while the
  `buzsak-poller.service` process itself stayed `active` — systemd never
  noticed because the process didn't exit, so `device_health` rows went
  stale (24-51h) with `/readyz` still reporting `ok` the whole time. Root
  exception was never identified (see journald gap below). Fixed in
  `poller/__main__.py`: the main thread now runs `_supervise()`, checking
  `thread.is_alive()` for every worker every 30s and calling
  `sys.exit(1)` if any have died, so systemd's existing
  `Restart=on-failure` actually restarts the process and respawns all
  threads. Covered by `tests/test_poller_main.py`. This bounds a repeat of
  this failure mode to roughly `RestartSec` + one check interval instead
  of indefinitely; it does not prevent a thread from dying in the first
  place. The dispatcher is single-loop/synchronous and now quarantines any
  command left `dispatching` across a restart rather than blindly replaying.
- **journald volatile storage (found 2026-10-05, blocks future
  diagnosis)**: `/var/log/journal` exists on the Pi but is empty; the
  active journal lives in `/run/log/journal` (tmpfs/RAM-only), and
  `journalctl --list-boots` showed only a few hours of history despite 5
  days of uptime — this is why the dead-thread incident's root exception
  could not be recovered from logs. Proposed fix (not yet applied,
  touches a Pi-wide system service): set `Storage=persistent` and
  `SystemMaxUse=200M` in `/etc/systemd/journald.conf` and restart
  `systemd-journald`.

## Matter-server / Sonoff MiniD telemetry (DEV-09, approved scope change 2026-09-30)

- **Read-only telemetry plus the narrow pulse exception**: turning a switch
  on/off via general Matter commands remains deferred; only on/off state,
  reachability, and the existing verified pulse action are in scope. The
  new general command lifecycle currently targets the valve controller only.
- **Ground truth is a live protocol inspection, not firmware source**:
  unlike the PLC/valve controller, there is no firmware checkout to read.
  `docs/adapters/matter_server.md` documents the real, already-running
  `python-matter-server` instance's WebSocket protocol as observed on
  2026-09-30 (handshake message, `get_nodes` command/response shape, the
  unsupported single-node `get_node` command, and the exact attribute
  paths used).
- **No single-node query exists**: a `get_node` command with a `node_id`
  argument returned `error_code: 8` live against this server version, so
  `poller/matter_client.fetch_nodes()` always fetches *all* commissioned
  nodes and `adapters.matter_server.find_node()` filters client-side. Two
  registered Sonoff devices therefore mean two independent WebSocket
  round-trips per poll cycle (one per device thread) — accepted as
  negligible load for a local server on the same Pi rather than adding a
  shared-connection/cache layer (YAGNI, matches the existing small-fleet
  one-thread-per-device design).
- **Node ids are not sequential**: the two real commissioned nodes are
  `node_id` 1 and 3 (not 1 and 2) — any future code must look this up, not
  assume a contiguous range.
- **Address encoding**: `DeviceConfig.address` carries the shared WS URL
  plus the node id as a `#node_id=<int>` fragment (e.g.
  `ws://192.168.1.95:5580/ws#node_id=1`), parsed by
  `poller.matter_client.parse_device_address()`, since one device row can
  only hold one address string but two physical switches share one
  Matter-server connection.
- **Dashboard party is now conditional**: `dashboard/queries.py` shows a
  real "Matter-Server" party (like PLC/Valve) once any `sonoff_minid`
  device rows exist, and falls back to the original DEV-08
  not-configured placeholder otherwise — so a deployment that never syncs
  Sonoff devices keeps the old placeholder behavior.
- **Never commit raw captured node data**: a real live `get_nodes` capture
  contains Operational Credentials cluster certificate/key material
  (attribute paths under cluster `62`). Test fixtures
  (`tests/fixtures/matter_server/*.json`) are hand-built synthetic
  payloads shaped like the verified contract, not copies of any live
  capture.
- **Not yet deployed to the Pi** (as of this writing): `config/example.yaml`
  ships both Sonoff entries `enabled: false`; enabling them on the Pi's
  preview config and verifying live dashboard values is the next step.

## Verification gaps

- Nothing has run against actual Raspberry Pi hardware, its microSD/SSD
  storage, or real device firmware — all tests run on a dev machine
  against `tmp_path` SQLite files.
- No performance or soak testing against NFR-01 through NFR-05.
- **Graceful reboot tested (2026-09-30, partial NFR-06 evidence)**: a
  `sudo reboot` against the live Pi showed all 3 services auto-start
  (OPS-04), `/readyz` healthy, `PRAGMA integrity_check` clean, and all 4
  devices self-recovered within ~2 minutes (including a transient
  matter-server dependency that started slower than the Pi's own
  services — absorbed by the existing POL-03 backoff with no code
  change). This only proves clean-shutdown recovery; an actual power-yank
  (unclean power loss) test still has not been done and needs physical
  access to the Pi.
- **Stale-data blind spot confirmed live (2026-10-05)**: `/readyz`'s
  `SELECT 1` check cannot detect "process alive but not actually
  polling" — this is exactly what happened during the thread-death
  incident above, and is the concrete case for DB-12-style monitoring
  (disk/WAL size, and ideally a staleness check) beyond a bare
  connectivity check.
- **Authenticated valve control tested on the Pi (2026-10-07)**: Caddy
  HTTPS, operator authentication, schema v3, and default production control
  gate were deployed. Two approved `relay4` LIGHT tests each set the output
  `false -> true`, confirmed via fresh telemetry, then restored and confirmed
  `true -> false`; no water relay or mode action was issued. One intervening
  attempt was rejected as `device_unavailable` before transmission while the
  poller's data was stale; relay4 remained false. After fresh polls resumed,
  the approved retry and restore both confirmed. Final check: all 4 devices
  healthy and non-stale, 4 services active, direct LAN port 8080 closed,
  `/readyz` 200, SQLite integrity check `ok`. Intermittent poller logs around
  deployment included foreign-key and database-lock errors; exact cause was
  not isolated, so keep monitoring poll freshness. Rate limiting remains
  open (SEC-09); the approved LAN subnet/firewall policy also needs review.

## Open decisions (not yet approved)

- Final retention window, backup schedule/destination, and recovery-time
  target (requirements.md Section 16).
- Whether a PWA or native Android client is built, and its offline/sync
  contract (requirements.md Section 16) — see "Dashboard/Android
  direction" below for the current working assumption.
- SSD vs microSD-only operation for the live database (DB-11).

## Dashboard/Android direction (approved Tailscale scope, post-1.0)

- The initial dashboard (this phase) is LAN-only, read-only, and served by
  the existing web/API process (ARC-03), per UI-01–UI-08.
- A future native Android client is intended to reach the same server
  over the private Tailscale tailnet from outside the LAN; the server has
  no public endpoint. The app uses the official Tailscale VPN and keeps
  the same API URL, CA trust, and bearer auth. To keep that migration
  low-friction, the dashboard's backend
  exposes a plain JSON contract (`/api/dashboard/state`) that a native
  client could consume directly, and the dashboard package is kept
  isolated from poller/dispatcher/adapter code so it only depends on a
  narrow read-only query layer.
- Caddy HTTPS and credential authentication are deployed. The Pi advertises
  only `192.168.1.95/32`; verify the tailnet ACL restricts this route to
  HTTPS TCP 443, approve it, then perform TAIL-PI-06 from an authorized
  physical phone over cellular.
