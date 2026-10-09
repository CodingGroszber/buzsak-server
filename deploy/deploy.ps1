<#
.SYNOPSIS
    Repeatable deploy of Buzsak Pi 3 Server to production on rpi3 (OPS-06).

.DESCRIPTION
    Packages the current working tree's `src/`, exports pinned dependencies
    from `uv.lock`, and updates the single `~/buzsak-pi3-server/` release
    directory on the target in place (release layout: single overwritten
    directory). Validates the target's `config/production.yaml`, takes a
    minimal SQLite backup, applies migrations, and syncs the device catalog
    -- all before touching any running service.

    Installing/refreshing the systemd units and restarting/enabling the
    production services (and disabling the preview services they replace)
    only happens when `-ApplyServices` is passed, since that step affects
    live, already-running services and requires explicit approval
    (.github/copilot-instructions.md SS3). Omit it to update code, pinned
    dependencies, and the database only.

    Prerequisites (not managed by this script):
      - `config/production.yaml` must already exist on the target -- this
        script never creates or overwrites it (OPS-07: configuration stays
        outside the replaceable release directory).
      - The target user needs passwordless sudo for `systemctl` and for
        `cp` into /etc/systemd/system, since -ApplyServices runs those
        non-interactively over ssh.
      - `uv`, `tar`, `scp`, and `ssh` must be available locally; the ssh
        alias in -SshHost must already be configured (see
        ~/.ssh/config on the dev machine).

.PARAMETER SshHost
    SSH alias/host for the target Pi.

.PARAMETER RemotePath
    Absolute path of the release directory on the target.

.PARAMETER RemoteConfig
    Path (relative to RemotePath) of the non-secret YAML config to
    validate and run against.

.PARAMETER ApplyServices
    Also install/refresh the systemd unit files, disable the preview units
    this replaces, and enable+restart the production units, then health
    check the web/API. Omit for a code/deps/DB-only deploy that never
    touches a running service.

.PARAMETER HealthUrl
    URL checked after -ApplyServices restarts the web service.

.EXAMPLE
    ./deploy/deploy.ps1
    Updates code, pinned deps, validates config, backs up the DB, applies
    migrations, and syncs devices. Leaves running services untouched.

.EXAMPLE
    ./deploy/deploy.ps1 -ApplyServices
    Full OPS-06 deploy, including disabling the preview services and
    restarting the live production services.
#>
[CmdletBinding()]
param(
    [string]$SshHost = "rpi3",
    [string]$RemotePath = "/home/neulas/buzsak-pi3-server",
    [string]$RemoteConfig = "config/production.yaml",
    [switch]$ApplyServices,
    [string]$HealthUrl = "https://192.168.1.95/readyz"
)

$ErrorActionPreference = "Stop"

function Invoke-Remote {
    param([Parameter(Mandatory)][string]$Command)
    Write-Host "+ ssh $SshHost `"$Command`"" -ForegroundColor DarkGray
    ssh $SshHost $Command
    if ($LASTEXITCODE -ne 0) {
        throw "remote command failed (exit $LASTEXITCODE): $Command"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    # --- 1. Resolve the deployed version identifier ---
    # No git commits exist yet in this repo, so fall back to the
    # pyproject.toml version plus a UTC deploy timestamp.
    $pyprojectVersion = (Select-String -Path "pyproject.toml" -Pattern '^\s*version\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    $deployTimestamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $deployedVersion = "$pyprojectVersion+$deployTimestamp"
    Write-Host "Deploying version $deployedVersion to ${SshHost}:${RemotePath}" -ForegroundColor Cyan

    # --- 2. Stage a clean release payload locally ---
    $staging = Join-Path $repoRoot ".tmp/production"
    if (Test-Path $staging) { Remove-Item -Recurse -Force $staging }
    New-Item -ItemType Directory -Path $staging | Out-Null

    Copy-Item -Path "deploy/validate_config.py", "deploy/migrate.py", "deploy/sync_devices.py", "deploy/backup_db.py", "deploy/manage_credentials.py" -Destination $staging

    Write-Host "Exporting pinned dependencies from uv.lock..." -ForegroundColor Cyan
    # --no-emit-project: only src/ is copied to the target, not pyproject.toml,
    # so the root package itself must not appear as a (local path) requirement.
    uv export --no-dev --no-hashes --no-emit-project --quiet -o "$staging/requirements-lock.txt"
    if ($LASTEXITCODE -ne 0) { throw "uv export failed" }

    $srcTarball = Join-Path $staging "src.tar.gz"
    tar -czf $srcTarball -C $repoRoot src
    if ($LASTEXITCODE -ne 0) { throw "packaging src/ failed" }

    # --- 3. Copy the payload to the target and unpack it in place ---
    Write-Host "Copying release payload to ${SshHost}:${RemotePath} ..." -ForegroundColor Cyan
    Invoke-Remote "mkdir -p $RemotePath/data $RemotePath/config"
    $payloadFiles = Get-ChildItem -Path $staging -File | ForEach-Object { $_.FullName }
    scp @payloadFiles "${SshHost}:${RemotePath}/"
    if ($LASTEXITCODE -ne 0) { throw "scp of release payload failed" }
    Invoke-Remote "cd $RemotePath && tar -xzf src.tar.gz && rm -f src.tar.gz"

    # --- 4. Install pinned dependencies into an isolated venv on the target ---
    Write-Host "Installing pinned dependencies in the target's isolated venv..." -ForegroundColor Cyan
    Invoke-Remote "cd $RemotePath && (test -x .venv/bin/python || python3 -m venv .venv)"
    Invoke-Remote "cd $RemotePath && .venv/bin/pip install --quiet --upgrade -r requirements-lock.txt"

    # --- 5. Validate the target's configuration before touching data or services ---
    Write-Host "Validating $RemoteConfig on target..." -ForegroundColor Cyan
    Invoke-Remote "cd $RemotePath && BUZSAK_CONFIG=$RemoteConfig PYTHONPATH=src .venv/bin/python validate_config.py"

    # --- 6. Minimal backup before migrations (partial OPS-10) ---
    Write-Host "Backing up database before migrations..." -ForegroundColor Cyan
    Invoke-Remote "cd $RemotePath && BUZSAK_CONFIG=$RemoteConfig PYTHONPATH=src .venv/bin/python backup_db.py"

    # --- 7. Migrate schema and sync the device catalog ---
    Write-Host "Applying migrations..." -ForegroundColor Cyan
    Invoke-Remote "cd $RemotePath && BUZSAK_CONFIG=$RemoteConfig PYTHONPATH=src .venv/bin/python migrate.py"
    Write-Host "Syncing device catalog..." -ForegroundColor Cyan
    Invoke-Remote "cd $RemotePath && BUZSAK_CONFIG=$RemoteConfig PYTHONPATH=src .venv/bin/python sync_devices.py"

    # --- 8. Record the deployed application version (OPS-06) ---
    Invoke-Remote "echo '$deployedVersion' > $RemotePath/deployed_version.txt"

    if (-not $ApplyServices) {
        Write-Host "Code, dependencies, config validation, backup, migrations and device sync are up to date." -ForegroundColor Yellow
        Write-Host "Services were left untouched. Re-run with -ApplyServices to install units and restart them." -ForegroundColor Yellow
        return
    }

    # --- 9. Install/refresh the production systemd units ---
    Write-Host "Installing systemd unit files..." -ForegroundColor Cyan
    $systemdFiles = Get-ChildItem -Path "deploy/systemd" -Filter "*.service" | ForEach-Object { $_.FullName }
    scp @systemdFiles "${SshHost}:/tmp/"
    if ($LASTEXITCODE -ne 0) { throw "scp of systemd unit files failed" }
    Invoke-Remote "sudo cp /tmp/buzsak-poller.service /tmp/buzsak-dispatcher.service /tmp/buzsak-web.service /etc/systemd/system/ && sudo systemctl daemon-reload"

    # --- 10. Replace the preview deployment outright ---
    Write-Host "Disabling preview services..." -ForegroundColor Cyan
    Invoke-Remote "sudo systemctl disable --now buzsak-poller-preview.service buzsak-dispatcher-preview.service buzsak-web-preview.service"

    Write-Host "Enabling and restarting production services..." -ForegroundColor Cyan
    # `restart` (not `enable --now`, which is a no-op "start" on an already
    # running unit) so every deploy actually picks up new code, not just
    # the first cutover where the units didn't exist yet.
    Invoke-Remote "sudo systemctl enable buzsak-poller.service buzsak-dispatcher.service buzsak-web.service"
    Invoke-Remote "sudo systemctl restart buzsak-poller.service buzsak-dispatcher.service buzsak-web.service"

    # --- 11. Health check ---
    Write-Host "Checking $HealthUrl ..." -ForegroundColor Cyan
    $healthy = $false
    for ($i = 0; $i -lt 10; $i++) {
        try {
            $response = Invoke-WebRequest -Uri $HealthUrl -UseBasicParsing -TimeoutSec 5
            if ($response.StatusCode -eq 200) { $healthy = $true; break }
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    if (-not $healthy) {
        throw "health check against $HealthUrl did not return 200 after restart"
    }
    Write-Host "Deploy complete. Deployed version: $deployedVersion" -ForegroundColor Green
}
finally {
    Pop-Location
}
