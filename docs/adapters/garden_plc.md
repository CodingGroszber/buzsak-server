# Garden PLC adapter contract (DEV-01)

Base address (configured, not verified as a stable endpoint contract per se):
`http://192.168.1.94/`.

Verified against firmware source at `C:\Scripts\Embedded\Garden_IS_ESP32_PLC_14_IO`
(local checkout, no commit pinned) on 2026-10-21:
`src/wifi_network.cpp`, `src/main.cpp`, `src/logic.cpp`, `include/io_config.h`.
Treated as historical/reference evidence, not a runtime dependency (ENV-01).

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | HTML dashboard (auto-refresh 2s). Not used by this server. |
| GET | `/api/state` | Full IO snapshot. |
| POST | `/api/control` | Request a well-tank refill. Form body `name=refill&state=1`. |
| GET | `/update` | ElegantOTA firmware upload UI (Basic auth). Not a telemetry/control endpoint. |
| GET | `/diag`, `/diag.csv`, `/diag/clear` | Analog-probe diagnostic, compiled in because `DIAG_ENABLED=1` is currently set. **Not part of this contract** — diagnostic/commissioning only, must not be polled or relied on (DEV-06). |

No authentication on `/api/*` (plaintext LAN HTTP; SEC-03 applies).

## `GET /api/state` response shape

```json
{
  "firmware": "v4.1",
  "ip": "192.168.1.94",
  "switch_mode": 1,
  "refill_button": false,
  "refill_active": false,
  "outputs": [
    { "name": "well_pump", "label": "Well Pump", "state": false, "controllable": false },
    { "name": "tank_pump", "label": "Tank Pump", "state": false, "controllable": false },
    { "name": "switch_led", "label": "Switch LED", "state": true, "controllable": false },
    { "name": "wifi_led", "label": "WiFi LED", "state": true, "controllable": false }
  ],
  "analog": [
    { "name": "pressure", "label": "Pressure Sensor", "mv": 1083, "signal": true, "bar": 3.0 },
    { "name": "water_level", "label": "Water Level Sensor", "mv": 1800, "signal": true, "liters": 750, "low": false }
  ]
}
```

- `firmware`: string, e.g. `"v4.1"` (`FIRMWARE_VERSION`, `include/io_config.h`).
- `ip`: string, device's own DHCP-assigned address; not authoritative for reachability.
- `switch_mode`: integer selector code: `-1` invalid (both contacts active),
  `0` neutral/off, `1` automatic, `2` manual. v4.1 no longer returns `inputs[]`;
  the server accepts the legacy v4.0 inputs shape and reports `right_sw` and
  `left_sw` unavailable when omitted by v4.1.
- `refill_button`: bool for the simulated request pulse, normally high for
  500 ms after acceptance.
- `refill_active`: bool while the firmware refill state machine is running.
- `outputs[]` (category: Boolean/state): `name`, `label`, `state: bool`,
  `controllable: bool`. In v4.1 all four outputs report false. Pump outputs
  are driven by firmware logic and must not be exposed as direct set-output
  actions. Remote refill is a separate state-machine request described below.
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
- The v4.1 payload also reports `switch_mode`, refill state, and all output
  controllability flags. A live sample on 2026-10-21 reported firmware v4.1,
  AUTO mode, refill idle, and a valid 999 L water level. Treat this as sampled
  telemetry, not a promise that the physical sensor is calibrated.

## Known firmware issues (README, "Known issues / in progress", verified present)

1. `DIAG_ENABLED=1` is currently active — `/diag*` routes and flash logging are
   live. Not part of this contract; do not poll.
2. Water-level sensor (TL-136) is reported as over-ranging (~22 mA / 10.3 V) —
   `liters`/`low` values may currently be unreliable at the physical
   installation even when `signal=true`. This is an operational/calibration
   fact, not something the adapter can detect from the payload shape alone.

## Control: refill request via `POST /api/control`

Firmware v4.1 accepts `application/x-www-form-urlencoded` body
`name=refill&state=1`. It does **not** accept direct `well_pump` or
`tank_pump` commands; those outputs report `controllable=false` and remain
owned by the PLC automation.

- Success: `200 {"ok":true,"refill_button":true}` means only that a refill
  request pulse was accepted.
- `400` — missing or malformed `name`/`state`.
- `403` — any action name except `refill`.
- `409` — selector is not AUTO, or a refill request/fill is already pending
  or active.
- The firmware raises `refill_button` for approximately 500 ms. The next
  scan starts `refill_active`; the well pump then runs until 1000 L, selector
  change, or missing water-level signal. A selector change cancels the fill.
- Server acceptance additionally requires healthy fresh telemetry,
  `switch_mode==1`, both refill flags false, a valid water-level signal, and
  a reported level below 1000 L. The deployment gate
  `control.plc_refill_enabled` defaults false.
- This is a non-idempotent request pulse. The server records one attempt and
  never automatically retries an ambiguous result. Fresh post-attempt
  `refill_button==true` or `refill_active==true` confirms acceptance. A short
  fill may finish before polling observes either flag; that outcome remains
  uncertain rather than being resent.
- Device success/confirmation concerns the PLC's refill state machine and
  relay state; it does not prove water is flowing. The PLC's firmware
  interlocks remain authoritative. No server-side request can enforce a
  maximum pump-on time if the Pi or network is unavailable.

## Capability summary (DEV-04)

| Capability | Action name | Params | Preconditions (device-enforced) | Confirmation | Retry safety |
| --- | --- | --- | --- | --- | --- |
| Well-tank refill request | `refill` | `{}` | fresh AUTO mode; idle refill flags; valid water-level signal below threshold | fresh refill-button pulse or active-fill state | non-idempotent request; never automatically retry |

All four v4.1 outputs are telemetry-only (`controllable=false`). `refill` is
the only PLC command capability; it requests a firmware-owned operation and
does not write pump output pins directly.
