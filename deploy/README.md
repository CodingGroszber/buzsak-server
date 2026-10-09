# Deployment (OPS-06)

Repeatable SSH deploy tooling for the production `buzsak-poller`,
`buzsak-dispatcher`, and `buzsak-web` services on `rpi3`, run from the
Windows dev machine.

## What it does

`deploy.ps1` updates the single `~/buzsak-pi3-server/` release directory on
the target in place (release layout: single overwritten directory, not
timestamped releases). Order of operations, matching OPS-06:

1. Package `src/` and export pinned dependencies from `uv.lock`
   (`uv export --no-dev --no-hashes`).
2. Copy the payload to the target and unpack it into the release
   directory, leaving `data/` and `config/` untouched (OPS-07).
3. Install the pinned dependencies into the target's `.venv` (created on
   first run) -- an isolated environment separate from the system Python.
4. Validate `config/production.yaml` on the target (`validate_config.py`)
   and abort before touching data or services if it is invalid.
5. Take a minimal local SQLite backup (`backup_db.py`, sqlite3's online
   `.backup()` API) before migrating. This is a partial OPS-10 only: no
   retention policy, no off-device copy, no restore drill yet.
6. Apply schema migrations (`migrate.py`) and sync the device catalog
   (`sync_devices.py`).
7. Record the deployed version (`pyproject.toml` version + UTC deploy
   timestamp, since the repo has no git commits yet) to
   `deployed_version.txt` in the release directory.

Steps 8-11 (installing systemd units, disabling the preview services this
replaces, enabling/restarting the production services, and health
checking `/readyz`) only run when `-ApplyServices` is passed, since they
affect already-running live services.

## Prerequisites

- `config/production.yaml` must already exist on the target. This script
  never creates or overwrites it -- configuration stays outside the
  replaceable release directory (OPS-07). The initial file must be placed
  on the Pi by hand (e.g. adapted from `config/preview.yaml`'s device
  list); it is intentionally never committed to this repo, matching how
  `config/preview.yaml` is handled.
- The target user (`neulas`) needs passwordless sudo for `systemctl` and
  for `cp` into `/etc/systemd/system/`, since `-ApplyServices` runs those
  non-interactively over `ssh`.
- `uv`, `tar`, `scp`, and `ssh` available locally, with the `rpi3` alias
  already configured (`~/.ssh/config` on the dev machine).

## Authenticated control rollout

Caddy is installed and active on the Pi, bound only to `192.168.1.95:443`.
It uses its internal CA and proxies to Gunicorn on `127.0.0.1:8080`;
port 8080 is no longer reachable from the LAN. Nginx still owns port 80
and currently returns 502; it is a separate legacy listener. Use
`https://192.168.1.95` for this application. The verified Caddy root CA is
at `.tmp/caddy-root.crt` on the development machine; import it into each
authorized client's trust store.

`BUZSAK_SESSION_SECRET` is provisioned in
`/etc/buzsak-pi3-server/web.env` (root-owned, mode 0600). A live-test
operator credential is stored on the Pi at
`/home/neulas/.buzsak-operator-token` (mode 0600); its credential ID is at
`/home/neulas/.buzsak-operator-credential-id`. To use the browser login,
retrieve the token directly in your own terminal with
`ssh rpi3 'cat /home/neulas/.buzsak-operator-token'`; do not paste it into
chat, source control, or logs. Issue a separate credential per additional
client/user and revoke it with `manage_credentials.py revoke <credential_id>`.

Production `control.valve_enabled` is true under the approved authenticated
policy. The live test toggled only valve-controller `relay4` (LIGHT): fresh
telemetry confirmed the test state, then confirmed restoration to its
original `false` state. No water-valve output or mode was changed. The
accepted policy remains a 12-second command lifetime, one transmission
attempt, a 5-second telemetry confirmation window, offline-at-submit
rejection, and per-device queue limit 3. Ambiguous outcomes are never
automatically resent; interrupted attempts become `uncertain` and block
further commands on that device pending admin reconciliation.

After migration 0003 is deployed, issue client credentials on the Pi:

```sh
cd /home/neulas/buzsak-pi3-server
BUZSAK_CONFIG=config/production.yaml PYTHONPATH=src .venv/bin/python \
   manage_credentials.py issue android-owner operator
```

The token is printed once. Store it in the client's protected credential
store; do not place it in source control, shell history, logs, or chat. Use
`manage_credentials.py revoke <credential_id>` to revoke it. Browser login
exchanges the token for a revocable Secure/HttpOnly/SameSite session; the
browser does not persist the bearer token.

Only after HTTPS trust and operator credentials are verified should
`control.valve_enabled` be set true in the protected production YAML. The
accepted initial policy is a 12-second command lifetime, one transmission
attempt, a 5-second telemetry confirmation window, offline-at-submit
rejection, and a per-device queue limit of 3. `set_mode` confirms only when
the mode reads back and all relays are off. Ambiguous outcomes are never
automatically resent; interrupted attempts become `uncertain` and block
further commands on that device pending admin reconciliation.

The full automated suite uses a local simulated valve controller. One
scoped relay4 live test has also passed; all other live actions still require
their own explicit approval.

## Usage

```powershell
# Code, pinned deps, config validation, backup, migrations, device sync.
# Never touches a running service.
./deploy/deploy.ps1

# Full deploy: also installs the systemd units, disables the preview
# services it replaces, restarts the production services, and health
# checks /readyz. Requires explicit approval before running against the
# live Pi (.github/copilot-instructions.md SS3).
./deploy/deploy.ps1 -ApplyServices
```

## Still open

- Verify the approved LAN subnet/firewall policy and ensure no router
   forwarding exposes the service (SEC-01). Each additional client must
   import the Caddy root CA and receive its own credential.
- Admin reconciliation API/UI for `uncertain` commands and browser operator
   provisioning policy need completion before control is operationally done.
- OPS-08 through OPS-14 (structured logging/monitoring, full backup
  retention/off-device copy, restore drills, controlled shutdown,
  disk-full handling) remain open -- see `backlog.md`.
- Git-commit-based version identifiers are deferred until the repo starts
  taking commits; `deployed_version.txt` uses the `pyproject.toml`
  version plus a UTC timestamp in the meantime.
