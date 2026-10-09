# Buzsak Pi 3 Server

Self-hosted, LAN-only garden telemetry/command server for a Raspberry Pi 3 Model B.
See [requirements.md](requirements.md) for the full specification and
[.github/copilot-instructions.md](.github/copilot-instructions.md) for the
project's working conventions.

## Architecture

Three independently supervised processes share one local SQLite database
(ARC-01..ARC-08):

- **Poller** (`buzsak-poller`) — owns device observation acquisition: one
  thread per enabled device polls `GET api/state`, parses it via the
  device's adapter, and records observations/health (POL-01..POL-12,
  ARC-01).
- **Dispatcher** (`buzsak-dispatcher`) — owns command claiming and execution.
- **Web/API** (`buzsak-web` for development, `buzsak_pi3_server.api.wsgi:app`
  for production) — serves the dashboard/API; never contacts devices directly.

The poller and adapters support the registered PLC, valve controller, and
commissioned Sonoff telemetry devices. The durable command lifecycle
supports the valve controller's `set_output`/`set_mode` actions and the
narrow Sonoff pulse exception. Valve control requires trusted HTTPS and an
operator credential and is enabled by protected deployment configuration.
The browser dashboard remains read-only. See [backlog.md](backlog.md) for
remaining operational gates.

## Setup

```powershell
uv sync
```

## Configuration

Copy [config/example.yaml](config/example.yaml) and adjust for your
environment (OPS-01, OPS-02). Secrets are never stored in this file. Every
process reads its config path from the `BUZSAK_CONFIG` environment variable:

```powershell
$env:BUZSAK_CONFIG = "config/example.yaml"
```

## Running (development)

```powershell
uv run buzsak-web          # Flask dev server; NOT for production (API-01)
uv run buzsak-poller
uv run buzsak-dispatcher
```

Production web/API deployment must use a production WSGI server, e.g.:

```bash
gunicorn buzsak_pi3_server.api.wsgi:app
```

Application services do not apply schema migrations on startup; production
deployment applies them before restarting services. For a fresh local
development database, apply migrations once:

```powershell
uv run python -c "from buzsak_pi3_server import db, schema, config; c = db.connect(config.load_config_from_env().database); schema.apply_migrations(c); c.close()"
```

Application services do not sync `config.yaml`'s `devices` list into the
database on startup. Production deployment syncs the catalog; for local
development, sync configured devices/parameters/capabilities after
migrating:

```powershell
uv run python -c "from buzsak_pi3_server import db, config; from buzsak_pi3_server.device_registration import sync_devices; c = db.connect(config.load_config_from_env().database); sync_devices(c, config.load_config_from_env()); c.close()"
```

Re-running this is safe (upserts by id); an unrecognized device `kind`
fails the whole sync rather than partially registering devices
(`buzsak_pi3_server.adapters.registry.UnknownDeviceKindError`).

The poller only polls devices with `enabled: true` in the config file.
All shipped device entries stay `enabled: false` until a live test against real
hardware has been explicitly approved (`.github/copilot-instructions.md`
§5) — do not flip this without that approval.

For local development, open `http://<bind_host>:<bind_port>/`; development
HTTP is not suitable for real credentials or device control. Production
clients use `https://192.168.1.95/` through Caddy and must trust its CA.
See [docs/dashboard.md](docs/dashboard.md) for the state and command API
contracts.

## Connecting a Device

A new ESP32 or other provider cannot be added by entering its IP alone.
The server only polls registered adapter kinds. Before enabling a new
provider:

1. Verify its firmware/API contract: endpoint, fields, types, units,
   invalid/missing-value semantics, polling limits, and any supported
   actions. Keep device URLs and credentials in protected deployment
   configuration, never in the app or a public URL.
2. If its kind is not already supported, implement and test an adapter,
   catalog entries, extraction, and a representative fixture. Document the
   contract under [docs/adapters](docs/adapters). Unknown kinds fail device
   sync; do not impersonate an existing kind unless the protocol really
   matches.
3. Reserve a stable LAN address and add a unique device ID, registered
   `kind`, base `address`, and `enabled: false` to the Pi's protected
   `config/production.yaml`. Enable it only after simulated tests and an
   explicitly approved live telemetry check. Control actions require their
   own verified safety/confirmation contract and approval.
4. Deploy the adapter, validate configuration, and sync the catalog using
   [deploy/deploy.ps1](deploy/deploy.ps1). Use `-ApplyServices` only when an
   approved service restart is intended. Never point a browser or phone
   directly at the ESP32; all observations flow through the server.

The supported device contracts are [garden PLC](docs/adapters/garden_plc.md),
[valve controller](docs/adapters/valve_controller.md), and [Sonoff/Matter](docs/adapters/matter_server.md).

## Connecting a Client

Each app/user needs the server's HTTPS trust anchor and their own credential;
the LAN address alone does not grant access. For the current Pi deployment:

- Server URL: `https://192.168.1.95`.
- Install the Caddy root CA (`.tmp/caddy-root.crt`) on the client. It is a
  public certificate, not a secret; distribute it through a trusted channel.
- An administrator issues a unique credential on the Pi with
  `manage_credentials.py issue <principal-id> viewer|operator|admin`.
  The token is printed once; provision it directly into the client's
  protected credential store. Do not commit it, put it in a URL, or send it
  in logs/chat. Credentials have no automatic expiry currently, so revoke
  them when a client is lost or access should end.
- `viewer` may read telemetry; `operator` may submit/cancel commands;
  `admin` may request reconciliation of an uncertain result. Browser users
  enter the token at `/login`; the server exchanges it for a Secure,
  HttpOnly session. Native clients use `Authorization: Bearer <token>`.
- The Android/API command route accepts only `set_output` and `set_mode`
  for the valve controller. The browser dashboard remains read-only.
  `confirmed` reports observed relay/mode state, not water flow or physical
  valve movement. A mode change closes all relays.
- For remote use, install the official Tailscale app and join the owner's
  private tailnet. The Pi now advertises only `192.168.1.95/32`; once the
  tailnet admin approves that route and confirms policy allows TCP 443 only,
  use the same `https://192.168.1.95` URL, CA trust, and server credential as
  on the LAN. The Buzsak app does not manage Tailscale state. Remote access
  still needs route approval and the off-LAN phone test.

An admin should provision clients individually and retain their credential
IDs so credentials can be revoked with `manage_credentials.py revoke
<credential-id>`. Never share the admin token with ordinary app users.

## Deploying (production)

The manual steps above are for local development. Repeatable deployment
to the production systemd units on the Pi (pinned dependencies, config
validation, minimal backup, migrations, device sync, service
restart — OPS-06) is automated by [deploy/deploy.ps1](deploy/deploy.ps1).
See [deploy/README.md](deploy/README.md) for prerequisites and usage.

## Tests

```powershell
uv run pytest -q
```
