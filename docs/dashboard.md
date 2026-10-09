# Dashboard JSON contract (API-04, UI-01)

`GET /api/dashboard/state` is the only endpoint the dashboard's own
JavaScript calls, and it is deliberately plain data (no HTML fragments) so
a future native (Android) client can consume it directly without going
through this package's templates. `GET /` serves the HTML/CSS/JS shell
that renders it.

The browser shell and state route never contact a device, and the server
never assumes a submitted command changed an observed value (UI-05,
UI-06). All application data routes require trusted HTTPS and an active
credential (SEC-02, SEC-04). The browser exchanges its token for a
revocable Secure/HttpOnly/SameSite session; native clients use bearer tokens.

## `GET /api/dashboard/state`

  database/schema is not reachable (mirrors `/readyz`'s shape).

```jsonc
{
  "generated_at": "2026-09-30T12:00:00Z",   // UTC, always this format
  "parties": [
    {
      "id": "garden_plc",                    // stable, matches devices.kind
      "label": "PLC",                        // display name for tabs/header
      "kind": "garden_plc",
      "configured": true,                    // false only for the Matter party
      "devices": [
        {
          "id": "garden-plc",                // DEV-02 stable device id
          "address": "http://192.168.1.94/",
          "enabled": true,
          "health": {
            "status": "healthy",              // unknown | healthy | degraded | offline
            "last_success_at": "2026-09-30T11:59:58Z",
            "last_error": null,
            "consecutive_failures": 0,
            "stale": false                     // true when enabled device has no recent successful poll
          },
          "parameters": [
            {
              "id": "well_pump",
              "category": "boolean",
              "unit": null,
              "value": "true",                // null when has_data is false
              "value_type": "bool",
              "quality": "good",               // good | unavailable | invalid; "unavailable" when has_data is false
              "stale": false,                   // derived at read time (POL-11), never stored
              "observed_at": "2026-09-30T11:59:58Z",
              "last_changed_at": "2026-09-30T11:59:58Z",
              "revision": 1,
              "has_data": true                  // false: no observation has ever been recorded
            }
          ],
          "capabilities": [
            {
              "action_id": "set_output",
              "params_schema": { "name": "well_pump", "state": "bool" },
              "enabled": false,                  // valve control defaults off in deployment config
              "disabled_reason": "Valve control is disabled by deployment configuration; trusted HTTPS and operator authentication are required."
            }
          ]
        }
      ]
    },
    { "id": "valve_controller", "label": "Valve", "...": "same shape as above" },
    {
      "id": "matter",
      "label": "Matter-Server",
      "kind": "matter",
      "configured": false,
      "devices": [],
      "note": "Not configured: Matter-server integration is deferred (DEV-08)."
    }
  ]
}
```

## Party order and membership

Fixed to three parties, in this order: PLC (`devices.kind = "garden_plc"`),
Valve (`devices.kind = "valve_controller"`), Matter-Server (always
`configured: false`, no real rows — see backlog.md's "Kind-string
inconsistency" and "No config→DB sync" entries for why this list isn't
derived dynamically yet).

## Staleness

Computed per parameter as `now - observed_at > interval * stale_multiplier`,
using each device's `poll_interval_seconds` override from config if set,
otherwise `polling.default_interval_seconds`, only when `quality == "good"`.
It is never stored in the database.

Device health is also derived at read time from `last_success_at`. When an
enabled device's last successful poll exceeds the same configured stale
threshold, the response sets `health.stale: true` and downgrades a stored
`healthy` status to `degraded`. This catches a live poller process whose
per-device worker thread has stopped; it does not mutate the stored
`device_health` row. Disabled devices are not downgraded by age.

## Valve commands

The browser dashboard remains read-only (UI-06). An authenticated native
client may submit `POST /api/v1/devices/{device_id}/commands` over trusted
HTTPS with an operator bearer token. The durable API accepts only
`set_output` and `set_mode` for the valve controller, validates fresh health
and relevant observations, enforces idempotency and a bounded deadline, and
returns `202` with a command ID/status URL. Use `GET /api/v1/commands/{id}`
for status and `DELETE` only while pending. Admins may queue an uncertain
outcome for dispatcher-owned reconciliation at
`POST /api/v1/commands/{id}/reconcile`.

Valve actions are enabled in capability metadata only when the protected
deployment config sets `control.valve_enabled: true`; the default is false.
The dispatcher makes at most one device request. Acknowledgment is not
confirmation: only fresh post-attempt poller telemetry can confirm. Output
confirmation means relay state, not physical valve motion or water flow.
