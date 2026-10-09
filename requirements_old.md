# 1. Basic Notions
There is a an rpi3B home server you can SSH into via: C:\Users\z004c8cj\.ssh\config:

Host rpi3
    HostName 192.168.1.95
    User neulas
    IdentityFile C:/Users/z004c8cj/.ssh/id_ed25519

you can ssh into this device freely.
The production code shall soly in this repo and the server you need to establish runs soley on the pi3. Deploy via ssh.
The server should realise the following:
Backend: Python/flask
Database: SQLite with polling / control service (command lifecycle, conflict handling , closed loop confirmation) -> singe source of truth for the later services (e.g. Dashboard)
Shall collect (poll) data from 3 LAN sources:
- PLC: source code: "C:\Scripts\Embedded\Garden_IS_ESP32_PLC_14_IO"; (http://192.168.1.94/)
- Valve Controller:  source code: "C:\Scripts\Embedded\Garden_Valve_Control" (http://192.168.1.109/)
- 2 Sonoff MiniD devices served via Matter server (comes later, lay down as placeholder)

log Bool/State (e.g.) Data only on change, log continuous Data (e.g. pressure) only in every 1s.

realise a LAN Browser Dashboard (this time only put every device's paramters you can read in the LAN to the UI for checking, later new features will come)

Previous pyserver source code if you had questions: C:\Scripts\Python\py_app

Great choice. Here is the architecture for a Python-based, LAN-only home server on a Raspberry Pi 3 that you fully own end-to-end. No code, just structure and reasoning.

---

## 2. High-level architecture

```
[LAN devices] 
     │  (HTTP JSON, MQTT, etc.)
     ▼
[Poller service] ── writes ──► [SQLite database]
                                     │
                                     ▼
[Web/API service] ── reads ──► [SQLite database]
     │
     ▼
[Dashboard in browser / PWA]
```

Two independent Python processes share one SQLite file. This separation is the core of robustness: if the web server crashes, polling continues; if a poll fails, the dashboard still shows last known data.

---

## 2. Component responsibilities

### Poller service
- Runs as a separate systemd service.
- Reads a YAML configuration listing devices, their URLs/protocols, poll intervals, and how to extract values.
- Contains small **adapters** per device type: HTTP JSON, MQTT, SNMP, Modbus, etc. Each adapter converts the device’s response into a common `(device, metric, value, timestamp)` tuple.
- Handles timeouts, retries with exponential backoff, and a simple circuit breaker per device (stop hammering a dead device for a cool-down period).
- Writes readings to SQLite in batches or single transactions.
- Normalises all timestamps to UTC.

### Storage (SQLite)
- Single file database on a USB SSD (not the microSD card, for longevity).
- **WAL mode** enabled for concurrent reads while writing.
- Schema: `devices`, `metrics`, and `readings` tables. `readings` has an index on `(device, metric, timestamp)`.
- Retention policy: a periodic job deletes readings older than a configurable window (e.g., 90 days) to keep the database small.
- Backup: a cron job uses SQLite’s `.backup` command to copy the database to another USB drive or LAN share. This is safe even while the poller is writing.

### Web/API service
- Flask (or FastAPI) serving two things:
  1. **JSON API** for querying devices, metrics, and time-series data over a time range.
  2. **Static dashboard** (HTML, CSS, JS).
- Opens SQLite in **read-only** mode. It never writes.
- Exposes a `/health` endpoint for systemd watchdog or external monitoring.
- Binds only to the local LAN IP. No external exposure.

### Dashboard (frontend)
- Single-page app using **Chart.js** (vendored locally, no CDN).
- Responsive layout for desktop and mobile browsers.
- **PWA (Progressive Web App)** support: a manifest and a service worker make it installable on Android/iOS and cache the shell for offline viewing of the last loaded data.
- Communicates only with the local API.

### Configuration
- A single YAML file for device definitions, poll intervals, and retention settings.
- Environment variables for any secrets (e.g., MQTT credentials).
- Reload by restarting the poller service; no complex config management.

### Logging and monitoring
- Python’s `logging` module with rotating file handler.
- Systemd captures stdout/stderr into the journal.
- Optional: poller also records its own health (last successful poll per device) into SQLite so the dashboard can show stale devices.

---

## 3. Process management and deployment

- **systemd** manages two services: `home-poller.service` and `home-web.service`.
- Both use `Restart=always` and `RestartSec=10`.
- The web service depends on the poller only for data availability, not for startup.
- Static IP or DHCP reservation for the Pi so device URLs never change.
- Firewall (ufw) allows only LAN access to the web port.
- NTP keeps timestamps accurate.

---

## 4. Robustness strategy

| Risk | Mitigation |
|------|------------|
| Web server crash | Separate systemd service with auto-restart; polling unaffected. |
| Poller crash | systemd auto-restart; last data remains in DB. |
| Device offline | Exponential backoff + circuit breaker; no log spam. |
| SD card wear | USB SSD for database; WAL mode reduces write amplification. |
| Power loss | SQLite WAL is crash-safe; systemd restarts services; consider a small UPS. |
| Database corruption | Regular `.backup` copies; WAL checkpointing. |
| Memory pressure | SQLite is lightweight; Flask is small; no heavy runtime. |
| Stale data | Dashboard shows last-seen timestamp per device. |

---

## 5. Mobile app support in the Python route

You have two realistic options:

1. **PWA (recommended)**  
   The dashboard is already a web app. Adding a manifest and service worker makes it installable on a phone’s home screen, launches full-screen, and can cache the UI. This gives a native-app feel without a separate codebase. It works on Android and iOS.

2. **Native Python mobile app (not recommended for robustness)**  
   Tools like BeeWare or Kivy can build Android/iOS apps in Python, but they are less mature, have larger binaries, and add significant complexity. The API you build for the dashboard is already mobile-ready, so if you ever need a true native app, you can write it later in any language without changing the backend.

For a LAN-only home server, the PWA is the pragmatic, robust choice.

---

## 6. Final tech stack (all free and open source)

- **Python 3** – runtime
- **Flask** – web/API
- **SQLite** – storage
- **Chart.js** – visualisation
- **systemd** – process supervision
- **Optional**: `requests` (HTTP polling), `paho-mqtt` (MQTT devices), `PyYAML` (config)
- **No cloud, no external services, no proprietary components.**

---

## 7. Summary

The architecture is deliberately minimal: one poller, one database, one web service, one dashboard. Every part is replaceable and understandable. The separation of polling and serving, combined with SQLite WAL and systemd supervision, gives you a robust, self-owned system that runs comfortably on a Raspberry Pi 3 and is accessible only on your LAN. Mobile support comes free via PWA, and the API remains open for any future native app.

# 2. Control Route:
Good — this is a meaningfully different architecture. It introduces a **control plane** alongside the existing **data plane**, and two independent command sources (web server and Android app). That changes several things, so let me first correct one assumption, then lay out the architecture.

---

## 1. First, a correction on "app has access to the database"

The Android app should **not** open the Pi's SQLite file directly (no SMB share, no direct file access). Doing so breaks WAL safety, bypasses validation, and creates two writers with no coordination. Instead:

- The Pi's database is the **single source of truth**.
- The Android app talks to it through the **same REST API** the web dashboard uses.
- Optionally, the app keeps a **local read replica** (its own SQLite via Room) for offline viewing and queued commands.

So "the app has access to the database" becomes: *the app has full read/write access to the system's state through the API, and holds a local cache.*

---

## 2. The three routes, side by side

| | Route 1 (Web only) | Route 2 (C# / MAUI) | Route 3 (Python + Android + Control) |
|---|---|---|---|
| Backend | Python/Flask | .NET AOT | Python/Flask |
| Storage | SQLite | SQLite | SQLite |
| Client | Browser | Native app | Browser **and** native Android |
| Data plane | Poll only | Poll only | Poll only |
| Control plane | None | None | **Yes — write back to devices** |
| Command sources | — | — | Web + App |
| Offline app | No (PWA cache only) | Partial | **Yes (local replica + queue)** |
| Complexity | Low | Medium | **High** |

Route 3 is the only one that needs a **command lifecycle**, **conflict handling**, and **closed-loop confirmation**. That's where the real design work is.

---

## 3. Route 3 architecture

```
                    ┌─────────────────────────────────────┐
                    │           Raspberry Pi              │
                    │                                     │
  [LAN devices] ◄───┤  Poller service   (reads)           │
       ▲            │  Command dispatcher (writes)        │
       │            │                                     │
       │            │        ┌──────────────┐             │
       └────────────┤        │   SQLite     │             │
        control     │        │  readings    │             │
                    │        │  commands    │             │
                    │        │  devices     │             │
                    │        └──────┬───────┘             │
                    │               │                     │
                    │        Web/API service              │
                    │        (read + command endpoint)    │
                    └───────────────┬─────────────────────┘
                                    │ LAN only
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
             [Browser dashboard]            [Android app]
                                            + local SQLite replica
                                            + command outbox
```

### Two planes, two processes

- **Data plane (read)**: poller → SQLite → API → clients. Unchanged from before.
- **Control plane (write)**: clients → API → command queue → dispatcher → devices.

Keeping these as separate processes means a stuck command dispatch never blocks polling, and a poller restart never loses queued commands.

---

## 4. New schema concepts

Beyond `devices`, `metrics`, `readings`, you add:

- **`commands`** — the queue: `id`, `device`, `action`, `params`, `origin` (web/app), `status`, `created_at`, `sent_at`, `ack_at`, `retries`, `idempotency_key`, `error`.
- **`device_capabilities`** — which actions each device supports, so the UI and app can render valid controls and reject invalid ones early.
- **`device_state`** — last known actuator state (e.g., valve open/closed), derived from polling, not from command ACKs. This is what closes the loop.

Command statuses: `pending → sent → acknowledged → confirmed | failed | expired`.

---

## 5. Command lifecycle (the core of Route 3)

1. **Client submits** a command via `POST /api/commands` with an idempotency key.
2. **API validates** against `device_capabilities` and writes it as `pending`.
3. **Dispatcher picks it up**, translates it via the device's control adapter (HTTP POST, MQTT publish, Modbus write…), and marks `sent`.
4. **Device ACKs** the transport (HTTP 200, MQTT puback). Marked `acknowledged`.
5. **Poller next cycle observes the new state** and updates `device_state`. Marked `confirmed`.
6. If no state change is observed within a timeout, the command is marked `failed` and optionally retried.

**Key principle: an ACK is not success. Telemetry confirms success.** This is what makes the system robust against devices that lie, drop commands, or partially apply them.

---

## 6. Idempotency and retries

- Every command carries an **idempotency key** generated by the client.
- The dispatcher and device adapter use it so retries never double-actuate (e.g., open a valve twice).
- Retries use exponential backoff with a cap.
- Commands have an **expiry** — a stale command (e.g., "open valve" from 20 minutes ago) is dropped rather than executed late.

---

## 7. Conflict handling

Two actors (web + app) can issue commands for the same device.

- **Per-device serialization**: the dispatcher processes one command per device at a time, in FIFO order.
- **Last-write-wins with visible ordering**: clients see queue depth and pending commands, so a user knows another command is in flight.
- **Cancellation**: a pending (not yet sent) command can be cancelled by its originator.
- **No silent overwrite**: if a newer command arrives while an older is `sent` but not `confirmed`, the API rejects or warns.

---

## 8. Android app: thin client vs offline-first

Two sub-options:

**8a. Thin client (simpler)**
- App calls the REST API for everything.
- No local database.
- Requires LAN connectivity to Pi at all times.
- Fails gracefully with a clear "server unreachable" state.

**8b. Offline-first replica (recommended for Route 3)**
- App keeps its own **SQLite (Room)** with a recent slice of readings and `device_state`.
- App has a **command outbox**: commands created offline are stored locally and synced when the Pi is reachable.
- Sync uses a **cursor / last-synced timestamp** for readings (append-only, easy) and a **merge by idempotency key** for commands (no duplicates).
- UI is optimistic: command appears immediately as "pending", then updates as the server confirms.
- All FOSS: Kotlin, Jetpack Compose, Room, Retrofit, WorkManager.

Option 8b is what actually justifies a native app over a PWA — it works during outages, which is exactly when you want to control things.

---

## 9. Security (still LAN-only)

- **Shared bearer token** on the API, configured once on the Pi, entered once in the app.
- **HTTPS with a self-signed cert** (or plain HTTP if you accept LAN trust — but note that commands are state changes, so signing is worth the small effort).
- **Per-command audit log** in SQLite: who (web/app), what, when, result.
- No inbound internet exposure; firewall restricts to LAN subnet.
- App stores the token in Android Keystore.

---

## 10. Failure modes and how Route 3 handles them

| Failure | Behaviour |
|---|---|
| Pi offline | App serves cached data, queues commands in outbox. |
| Device offline | Command stays `pending`, retries with backoff, eventually `expired`. |
| Device ACKs but doesn't act | Poller never confirms; command marked `failed`; UI shows the discrepancy. |
| Two conflicting commands | Serialized per device; second sees first's state. |
| Web server crash | Poller and dispatcher keep running; app still works; browser reconnects. |
| App crash mid-command | Idempotency key prevents duplicate actuation on resync. |
| Duplicate submission | Idempotency key collapses them server-side. |
| Clock skew | All timestamps UTC; server is authoritative. |

---

## 11. When to choose which route

- **Route 1** — you want the smallest, most understandable system and web-only access is fine.
- **Route 2** — you want one codebase (C#) for both backend and native app and accept the Pi 3 memory cost.
- **Route 3** — you need **bidirectional control**, a **proper native Android app**, and **offline tolerance**. It is the most capable and the most complex; the complexity lives almost entirely in the command lifecycle and sync, not in the languages.

If you go Route 3, the single most important design decision is the closed-loop rule: **commands are confirmed by observed state, never by ACKs alone.** Everything else (queue, retries, idempotency, conflicts) exists to make that rule safe.