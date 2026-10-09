# Garden PLC adapter contract (DEV-01)

Base address (configured, not verified as a stable endpoint contract per se):
`http://192.168.1.94/`.

Verified against firmware source at `C:\Scripts\Embedded\Garden_IS_ESP32_PLC_14_IO`
(local checkout, no commit pinned) on 2026-09-30:
`src/wifi_network.cpp`, `src/main.cpp`, `src/logic.cpp`, `include/io_config.h`.
Treated as historical/reference evidence, not a runtime dependency (ENV-01).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | HTML dashboard (auto-refresh 2s). Not used by this server. |
| GET | `/api/state` | Full IO snapshot. |
| POST | `/api/control` | Set one controllable output. Form body `name=<key>&state=<0\|1>`. |
| GET | `/update` | ElegantOTA firmware upload UI (Basic auth). Not a telemetry/control endpoint. |
| GET | `/diag`, `/diag.csv`, `/diag/clear` | Analog-probe diagnostic, compiled in because `DIAG_ENABLED=1` is currently set. **Not part of this contract** — diagnostic/commissioning only, must not be polled or relied on (DEV-06). |

No authentication on `/api/*` (plaintext LAN HTTP; SEC-03 applies).

## `GET /api/state` response shape

```json
{
  "firmware": "v4.0",
  "ip": "192.168.1.94",
  "inputs": [
    { "name": "right_sw", "label": "Switch Right", "state": false }
  ],
  "outputs": [
    { "name": "well_pump", "label": "Well Pump", "state": false, "controllable": true }
  ],
  "analog": [
    { "name": "pressure", "label": "Pressure Sensor", "mv": 1083, "signal": true, "bar": 3.0 },
    { "name": "water_level", "label": "Water Level Sensor", "mv": 1800, "signal": true, "liters": 750, "low": false }
  ]
}
```

- `firmware`: string, e.g. `"v4.0"` (`FIRMWARE_VERSION`, `include/io_config.h`).
- `ip`: string, device's own DHCP-assigned address; not authoritative for reachability.
- `inputs[]` (category: Boolean/state, DEV-03): `name`, `label`, `state: bool`.
  Configured instances (`src/main.cpp`): `right_sw`, `left_sw`. Active-LOW at the
  pin; the firmware already reports the logical `state` (no inversion needed by
  the adapter).
- `outputs[]` (category: Boolean/state): `name`, `label`, `state: bool`,
  `controllable: bool`. Configured instances: `well_pump` (controllable),
  `tank_pump` (controllable), `switch_led` (not controllable), `wifi_led` (not
  controllable). Only `controllable=true` entries may ever be targeted by
  `POST /api/control` (DEV-04); the firmware itself enforces this with a 403.
- `analog[]` (category: continuous measurement, DEV-03): `name`, `label`,
  `mv: int` (raw `analogReadMilliVolts`), `signal: bool`.
  - **`signal` is the validity flag** — not documented in the project README,
    found only in `src/wifi_network.cpp`. For `pressure`: `signal = mv > 512`
    (`PRESSURE_MIN_VALID_MV`, ~0.1 bar live-zero/rupture guard). For any other
    analog channel (currently only `water_level`): `signal = mv > 0`.
  - `bar` (pressure only) is present **only when `signal` is true**. Formula:
    `bar = (mv * 3.82 / 470.0 - 4.0) / 1.6` (`pressureMvToBar`,
    `include/io_config.h`).
  - `liters` and `low` (water_level only) are **always present, regardless of
    `signal`** — this is a firmware quirk, not a documented guarantee. A
    disconnected/zero-current sensor (`signal=false`) still yields a computed
    `liters` (clamped at 0) and `low=true`. **The adapter must treat
    `water_level.liters`/`low` as invalid whenever `signal` is false**,
    overriding what the firmware naively includes (DEV-05: a failed read must
    never surface as a synthetic valid zero/false reading).
  - Formula: `liters = clamp(mv / 2400 * 1000, 0, 1000)`
    (`waterLevelMvToLiters`, `WATER_LEVEL_UPPER_BOUND_MV=2400`,
    `WATER_LEVEL_FULL_L=1000`). `low = liters <= 300` (`WATER_LEVEL_LOW_L`).

## Known firmware issues (README, "Known issues / in progress", verified present)

1. `DIAG_ENABLED=1` is currently active — `/diag*` routes and flash logging are
   live. Not part of this contract; do not poll.
2. Water-level sensor (TL-136) is reported as over-ranging (~22 mA / 10.3 V) —
   `liters`/`low` values may currently be unreliable at the physical
   installation even when `signal=true`. This is an operational/calibration
   fact, not something the adapter can detect from the payload shape alone.

## Control: `POST /api/control?name=<key>&state=<0|1>`

- Success: `200 {"ok": true}`.
- `400` — missing `name` or `state`.
- `403` — `name` exists but `controllable=false`.
- `404` — unknown `name`.
- No conflict/mode concept: unlike the valve controller, there is **no
  automatic/manual interlock** on this device. Any accepted command is applied
  immediately (`digitalWrite`), with no server-side precondition beyond the
  `controllable` flag.
- **Idempotent desired-state action** (CMD-08/CMD-09): repeating the same
  `name`/`state` is safe to retry — it is a level write, not a pulse/toggle.
- **No transactional acknowledgment beyond the HTTP response** (CMD-06): a
  `200 {"ok":true}` only means the ESP32 accepted and applied the digitalWrite
  call; it is not itself telemetry confirmation.
- **Confirmation predicate** (CMD-07): a subsequent, fresh `GET /api/state`
  showing `outputs[].state` equal to the requested value. There is no
  physical-movement sensor tied to these relays (well/tank pump), so confirmed
  state means "relay coil is in the commanded position," not verified water
  flow (CMD-19).
- **No safety preconditions are enforced by the firmware** (e.g. no minimum
  on-time, no interlock between well pump and tank pump). Any such safety rule
  (DEV-04) must be added in this server's command layer — it does not exist on
  the device today, and none should be assumed.

## Capability summary (DEV-04)

| Capability | Action name | Params | Preconditions (device-enforced) | Confirmation | Retry safety |
| --- | --- | --- | --- | --- | --- |
| Well pump relay | `set_output` | `name="well_pump"`, `state: bool` | `controllable=true` only | next `state.outputs[well_pump].state == requested` | idempotent, safe to retry |
| Tank pump relay | `set_output` | `name="tank_pump"`, `state: bool` | `controllable=true` only | next `state.outputs[tank_pump].state == requested` | idempotent, safe to retry |

`switch_led` and `wifi_led` are outputs but `controllable=false` — read-only,
never targetable, per DEV-04 "unsupported actions shall not be advertised or
executable."
