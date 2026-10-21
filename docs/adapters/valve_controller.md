# Valve controller adapter contract (DEV-01)

Base address (configured, not verified as a stable endpoint contract per se):
`http://192.168.1.109/`.

Verified against firmware source at `C:\Scripts\Embedded\Garden_Valve_Control`
(local checkout, no commit pinned) on 2026-09-30:
`src/web.cpp`, `src/main.cpp`, `include/io_config.h`, `include/config.h`,
`include/sensors.h`, `include/humidity_control.h`, `include/valve_control.h`,
`src/sensors.cpp`. Treated as historical/reference evidence, not a runtime
dependency (ENV-01).

Rain schedule editing was inspected on 2026-10-21 in the live read-only
dashboard/state and the local `src/web.cpp`, `src/rain_control.cpp` and
`include/config.h` handlers. The reported firmware version remains v0.9;
the version string alone does not establish this endpoint's availability.
No live schedule POST was performed and no firmware commit is pinned.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | HTML dashboard (auto-refresh 2s). Not used by this server. |
| GET | `/api/state` | Relays, sensors, and automation status. |
| POST | `/api/control?name=<key>&state=<0\|1>` | Switch one relay. **Manual mode only.** |
| POST | `/api/mode?value=<manual\|automatic>` | Switch control mode; closes all valves first. |
| POST | `/api/rain?start=HH:MM&duration=<minutes>` | Set daily rain schedule; firmware accepts edits in either mode. Server adds manual/idle restrictions. |
| GET | `/api/rs485?addr=N[&loopback=1]` | Bus probe, raw bytes. **Commissioning only.** |
| GET | `/api/rs485/scan?max=N` | Baud/address sweep, blocking ~12s. **Commissioning only.** |
| POST | `/api/rs485/setaddr?from=A&to=B` | Re-address a probe; mutates device config. **Commissioning only, hazardous with >1 probe on the bus.** |
| GET | `/update` | ElegantOTA firmware upload UI (Basic auth). Not a telemetry/control endpoint. |

The three `/api/rs485*` routes are commissioning tools (`src/web.cpp` comment:
"commissioning"), not part of the regular poller/dispatcher contract (DEV-06).
They must never be called automatically — `setaddr` in particular can silently
break the bus if more than one probe is attached.

No authentication on `/api/*` (plaintext LAN HTTP; SEC-03 applies).

## `GET /api/state` response shape

```json
{
  "firmware": "v0.9",
  "ip": "192.168.1.109",
  "mode": "automatic",
  "outputs": [
    { "name": "relay1", "label": "MIST", "state": false, "controllable": true, "always_manual": false },
    { "name": "relay4", "label": "LIGHT", "state": true, "controllable": true, "always_manual": true },
    { "name": "status_led", "label": "Status LED", "state": true, "controllable": false, "always_manual": false }
  ],
  "sensors": [
    {
      "name": "sensor_a", "label": "Sensor-A", "address": 1, "ok": true,
      "temperature_c": 27.5, "humidity_pct": 48.8,
      "errors": 0, "last_error": "", "raw": "01 03 ...", "age_s": 3
    }
  ],
  "automation": {
    "mist": {
      "valve": "relay1", "target_pct": 90.0, "duty": 0.25, "valve_on": false,
      "period_s": 300, "elapsed_s": 120, "sensor_valid": true, "time_synced": true
    },
    "rain": {
      "valve": "relay2", "valve_on": false, "start_hour": 6, "start_minute": 30,
      "duration_s": 180, "has_last_run": false, "last_run_duration_s": 0,
      "time_synced": true
    }
  }
}
```

- `firmware`, `ip`: same semantics as the PLC.
- `mode`: `"manual" | "automatic"` (`controlModeName()`). Boots into
  `"automatic"` (`BOOT_IN_AUTOMATIC_MODE=true`, `include/config.h`) — an
  unattended controller resumes irrigating after a power cut.
- `outputs[]` (category: Boolean/state): `name`, `label`, `state: bool`,
  `controllable: bool`, `always_manual: bool`. Firmware v0.9 may operate
  outputs marked `always_manual` while in automatic mode. Older payloads
  omit this field; the server treats it as false for backward compatibility.
  Configured instances (`src/main.cpp`): `relay1`
  ("MIST", controllable, also the humidity-automation valve), `relay2`
  ("RAIN", controllable), `relay3` ("DRIP", controllable), `relay4` ("LIGHT",
  controllable), `status_led` (not controllable).
- `sensors[]` (category: continuous measurement, DEV-03): `name`, `label`,
  `address: int` (Modbus slave address), `ok: bool`, `errors: int`
  (cumulative since boot), `last_error: str` (`""`, `"timeout"`, or
  `"noframe"`), `raw: str` (hex dump of last RX buffer), `age_s: int`
  (seconds since last good frame, `0` if never).
  - `temperature_c` and `humidity_pct` are **present only when `ok=true`**
    (`src/web.cpp`: "omitted entirely when invalid, so a client can never
    mistake a stale reading for a fresh one") — this is the correct DEV-05
    behavior, unlike the PLC's water-level quirk. Treat missing keys as
    "unavailable," not zero.
  - `temperature_c` is derived from a **signed** 16-bit Modbus register (must
    not be parsed as unsigned — sub-zero readings use two's complement,
    `docs/manual.md` / `src/sensors.cpp`). `humidity_pct` is unsigned. Both are
    register-value ÷ 10.
- Current firmware nests the two automation sections at `automation.mist`
  and `automation.rain`. The server also accepts the older flat
  `automation` object as mist data; absent rain fields are reported
  unavailable. Output, sensor, mist, and rain parse failures are isolated
  so valid sections continue to refresh.
- `automation.mist` object: **always present in the payload regardless of
  `mode`** (unconditional in `src/web.cpp`), but only actively driven while
  `mode=="automatic"` — `humidityControlLoop()` is only invoked from
  `runAutomation()`, which only runs in automatic mode
  (`include/humidity_control.h`, `src/valve_control.*`). **While
  `mode=="manual"`, treat every field in `automation` as stale/frozen, not
  live** — it reflects whatever the controller last computed before the
  handover, not a suspended/neutral state.
  - `valve`: string, always `"relay1"` today (`HUMIDITY_VALVE_NAME`).
  - `target_pct`: float, `HUMIDITY_TARGET_PCT = 90.0`.
  - `duty`: float `0..1`, fraction of `period_s` the valve runs.
  - `valve_on`: bool, current commanded state of the mist valve.
  - `period_s`: int, `HUMIDITY_PERIOD_S = 600` (10 minutes), wall-clock aligned
    to Europe/Budapest via NTP.
  - `elapsed_s`: int, seconds into the current period.
  - `sensor_valid`: bool — false means the humidity reading behind the
    automation is stale/absent; per firmware comment, the algorithm
    deliberately does not act on stale humidity.
  - `time_synced`: bool — false means periods run on `millis()`, unaligned to
    the wall clock, until NTP answers (`MIN_VALID_EPOCH` guard in
    `include/config.h`).
- `automation.rain` contains `valve`, `valve_on`, `start_hour` (0..23),
  `start_minute` (0..59), `duration_s`, `has_last_run`,
  `last_run_duration_s`, and `time_synced`. When `has_last_run` is false,
  `rain_last_run_duration_s` is unavailable, not zero. Rain automation
  execution is suspended in manual mode, but schedule values are read from
  current configuration and `valve_on` reads current relay state, including
  in manual mode (`rainControlStatus()`). These fields can confirm an edit
  without running automation.

## Control: `POST /api/control?name=<key>&state=<0|1>`

- Success: `200 {"ok": true}`.
- `400` — missing `name` or `state`.
- `403` — `name` exists but `controllable=false`.
- `404` — unknown `name`.
- `409` — controller is in `automatic` mode and the target output is not
  `always_manual`. This is a real device-enforced precondition (unlike the PLC):
  a command must not be sent unless a fresh
  `GET /api/state` has confirmed `mode=="manual"` (CMD-01, CMD-05 "recheck
  ... immediately before sending"). A stale `mode` read is exactly the kind of
  precondition staleness CMD-15 requires rejecting.
- **Idempotent desired-state action**, same as the PLC: safe to retry the same
  `name`/`state` (CMD-08/CMD-09).
- **Confirmation predicate**: fresh `GET /api/state` showing
  `outputs[].state == requested` **and** `mode=="manual"` at the time of the
  read. No physical valve/flow sensor exists — confirmed state means "relay
  coil is in the commanded position," not verified water flow (CMD-19).
- `relay1` is additionally driven by the automation loop while
  `mode=="automatic"`; a manual command sent right after a mode switch that
  raced with dispatch could be legitimately rejected by a device-side `409` —
  this is not a bug to route around, it is the intended interlock.

## Control: `POST /api/mode?value=manual|automatic`

- Success: `200 {"ok": true, "mode": "<manual|automatic>"}`.
- `400` — `value` is neither `manual` nor `automatic`.
- Every mode change **closes all valves first** (`src/valve_control.*`
  comment: "neither owner inherits the other's state"). This is a real,
  documented side effect, not just a metadata switch — a mode-change command
  must be modeled as an action with its own confirmation (`mode` reads back as
  requested) rather than assumed synchronous with valve state.

## Capability summary (DEV-04)

### Rain configuration: `set_rain_schedule` (DEV-12)

- Client schema: exactly `{"start":"05:30","duration_minutes":10}`.
  ASCII `HH:MM` must be 00:00..23:59; duration is an integer 1..60
  **minutes**, never seconds or a Boolean. Firmware POST query uses the
  names `start` and `duration`; telemetry continues to use `duration_s`.
- The device uses Europe/Budapest local time, including DST, and permits
  windows crossing midnight. This is not an exact-once daily-run guarantee
  around DST transitions; its loop checks whether local time is in the window.
- Firmware returns `200 {"ok":true}` or `400 {"ok":false,"error":...}`.
  The server validates the bounded JSON acknowledgment, treats malformed
  success bodies as uncertain, and never automatically repeats the request.
- `rainControlSetSchedule()` updates runtime configuration immediately and
  attempts NVS writes. It does not check write results before reporting
  success. Runtime readback therefore cannot prove reboot persistence.
- Applying a schedule in automatic mode can immediately open or close the
  rain relay. The server deliberately requires fresh **manual mode and rain
  idle** (`rain_valve_on=false` and `relay2_rain=false`), no other pending work,
  and both `control.valve_enabled` and
  `control.valve_rain_schedule_enabled` (the latter defaults false).
  These restrictions are server checks, not a firmware interlock against
  another LAN controller changing mode between observation and transmission.
- The server never changes mode as part of this action. Returning to
  automatic mode, or a reboot into automatic mode, may start watering if
  local time is within the configured window. Firmware remains authoritative.
- Confirmation requires fresh, good, post-attempt `rain_start_hour`,
  `rain_start_minute`, and `rain_duration_s` matching start and
  `duration_minutes * 60`. It confirms runtime configuration only.
- The normal command policy applies: 12-second default request lifetime,
  one transmission, 5-second default confirmation window, no automatic
  replay, and uncertain outcomes block conflicting controller commands.
  Existing catalog synchronization registers this capability; no migration
  or new observations are required. Browser controls remain disabled.

### Relay and mode actions

| Capability | Action name | Params | Preconditions (device-enforced) | Confirmation | Retry safety |
| --- | --- | --- | --- | --- | --- |
| Mist valve | `set_output` | `name="relay1"`, `state: bool` | `controllable=true`, fresh `mode=="manual"` | next `state.outputs[relay1].state == requested` | idempotent, safe to retry |
| Rain valve | `set_output` | `name="relay2"`, `state: bool` | same | same pattern | idempotent, safe to retry |
| Drip valve | `set_output` | `name="relay3"`, `state: bool` | same | same pattern | idempotent, safe to retry |
| Light relay | `set_output` | `name="relay4"`, `state: bool` | `controllable=true`; fresh mode, or `always_manual=true` | same pattern | idempotent, safe to retry |
| Control mode | `set_mode` | `value: "manual"\|"automatic"` | none beyond valid enum | next `state.mode == requested` | idempotent (repeating same value is a no-op mode re-closure of valves) |

`status_led` is `controllable=false` — read-only, never targetable.
RS485 commissioning actions (`rs485`, `rs485/scan`, `rs485/setaddr`) are
**out of scope for the command dispatcher** — DEV-08-style "not configured"
treatment: no capability schema is defined for them, and they must remain
manually operated only.
