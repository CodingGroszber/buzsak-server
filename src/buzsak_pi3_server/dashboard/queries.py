"""Read-only queries backing the dashboard JSON contract (UI-01, UI-02).

Every function here only reads (`SELECT`); nothing in this module ever
writes to the database or contacts a device (ARC-03, UI-05). Staleness is
computed here, at read time, rather than stored (POL-11): the database
only ever records `observed_at` and `quality` as reported by the poller.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from buzsak_pi3_server.clock import Clock
from buzsak_pi3_server.config import AppConfig, PollingConfig

# Party membership is fixed to the three parties the initial UI shows
# (requirements.md Section 3: two active adapters plus a deferred Matter
# integration). `kind` matches the `devices.kind` column as written by
# adapters' DEVICE_KIND constants (underscored), not config.yaml's
# hyphenated `kind` strings — see backlog.md "Kind-string inconsistency".
_PLC_KIND = "garden_plc"
_VALVE_KIND = "valve_controller"
_MATTER_KIND = "sonoff_minid"

_CONTROL_DISABLED_REASON = (
    "Read-only initial release (UI-06); command dispatch is not implemented yet."
)

# DEV-10: the one narrow, verified exception -- everything else stays
# disabled with `_CONTROL_DISABLED_REASON` above.
_ENABLED_CAPABILITIES = {(_MATTER_KIND, "pulse")}


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fetch_devices(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        "SELECT d.id, d.kind, d.address, d.enabled, "
        "h.status, h.last_success_at, h.last_error, h.consecutive_failures "
        "FROM devices d LEFT JOIN device_health h ON h.device_id = d.id "
        "ORDER BY d.id"
    ).fetchall()
    return [
        {
            "id": row[0],
            "kind": row[1],
            "address": row[2],
            "enabled": bool(row[3]),
            "health_status": row[4] or "unknown",
            "last_success_at": row[5],
            "last_error": row[6],
            "consecutive_failures": row[7] if row[7] is not None else 0,
        }
        for row in rows
    ]


def _fetch_parameters(
    connection: sqlite3.Connection,
    device_id: str,
    stale_after_seconds: float,
    now: datetime,
) -> list[dict]:
    rows = connection.execute(
        "SELECT p.parameter_id, p.category, p.unit, "
        "o.value, o.value_type, o.quality, o.observed_at, o.last_changed_at, o.revision "
        "FROM parameters p LEFT JOIN observations_latest o "
        "ON o.device_id = p.device_id AND o.parameter_id = p.parameter_id "
        "WHERE p.device_id = ? ORDER BY p.parameter_id",
        (device_id,),
    ).fetchall()

    parameters = []
    for parameter_id, category, unit, value, value_type, quality, observed_at, last_changed_at, revision in rows:
        has_data = observed_at is not None
        stale = (
            has_data
            and quality == "good"
            and (now - _parse_iso(observed_at)).total_seconds() > stale_after_seconds
        )
        parameters.append(
            {
                "id": parameter_id,
                "category": category,
                "unit": unit,
                "value": value,
                "value_type": value_type,
                "quality": quality if has_data else "unavailable",
                "stale": stale,
                "observed_at": observed_at,
                "last_changed_at": last_changed_at,
                "revision": revision if revision is not None else 0,
                "has_data": has_data,
            }
        )
    return parameters


def _fetch_capabilities(
    connection: sqlite3.Connection,
    device_id: str,
    kind: str,
    app_config: AppConfig,
) -> list[dict]:
    rows = connection.execute(
        "SELECT action_id, params_schema FROM capabilities WHERE device_id = ? ORDER BY action_id",
        (device_id,),
    ).fetchall()

    capabilities = []
    for action_id, params_schema_raw in rows:
        try:
            params_schema = json.loads(params_schema_raw)
        except (TypeError, ValueError):
            params_schema = params_schema_raw
        valve_action = kind == _VALVE_KIND and action_id in {
            "set_output", "set_mode"}
        enabled = (
            (kind, action_id) in _ENABLED_CAPABILITIES
            or (valve_action and app_config.control.valve_enabled)
        )
        capability = {
            "action_id": action_id,
            "params_schema": params_schema,
            "enabled": enabled,
            "disabled_reason": (
                None if enabled else (
                    "Valve control is disabled by deployment configuration; "
                    "trusted HTTPS and operator authentication are required."
                    if valve_action else _CONTROL_DISABLED_REASON
                )
            ),
        }
        if valve_action:
            capability["retry_safe"] = True
            capability["lifetime_s"] = app_config.control.command_lifetime_seconds
            if action_id == "set_output":
                capability["effect"] = "relay_state_only"
                capability["requires"] = {
                    "controllable": True,
                    "mode": "manual",
                    "exception_parameter": "relayN_always_manual",
                    "fresh": True,
                }
                capability["confirmation"] = (
                    "fresh relay state matches request and mode is manual "
                    "unless relay is always_manual"
                )
            else:
                capability["side_effects"] = ["closes_all_relays"]
                capability["confirmation"] = (
                    "fresh mode matches request and all relays are off"
                )
        capabilities.append(capability)
    return capabilities


def _fetch_latest_pulse(connection: sqlite3.Connection, device_id: str) -> dict | None:
    row = connection.execute(
        "SELECT status, requested_at, executed_at, error FROM pulse_requests "
        "WHERE device_id = ? ORDER BY id DESC LIMIT 1",
        (device_id,),
    ).fetchone()
    if row is None:
        return None
    status, requested_at, executed_at, error = row
    return {
        "status": status,
        "requested_at": requested_at,
        "executed_at": executed_at,
        "error": error,
    }


def _stale_after_seconds(
    device_id: str, app_config: AppConfig, polling: PollingConfig
) -> float:
    override = next(
        (d.poll_interval_seconds for d in app_config.devices if d.id == device_id),
        None,
    )
    interval = override if override is not None else polling.default_interval_seconds
    return interval * polling.stale_multiplier


def _device_label(device_id: str, app_config: AppConfig) -> str:
    # UI-only display name (e.g. "GARAGE-RIGHT"); falls back to the id when
    # config.yaml sets none.
    configured = next(
        (d.label for d in app_config.devices if d.id == device_id), None)
    return configured or device_id


def _health_status(device: dict, stale_after_seconds: float, now: datetime) -> tuple[str, bool]:
    last_success_at = device["last_success_at"]
    stale = bool(
        device["enabled"]
        and last_success_at
        and (now - _parse_iso(last_success_at)).total_seconds() > stale_after_seconds
    )
    status = device["health_status"]
    if stale and status == "healthy":
        status = "degraded"
    return status, stale


def _build_party(
    label: str,
    kind: str,
    devices: list[dict],
    connection: sqlite3.Connection,
    app_config: AppConfig,
    now: datetime,
) -> dict:
    device_payloads = []
    for device in devices:
        stale_after = _stale_after_seconds(
            device["id"], app_config, app_config.polling)
        health_status, health_stale = _health_status(device, stale_after, now)
        device_payloads.append(
            {
                "id": device["id"],
                "label": _device_label(device["id"], app_config),
                "address": device["address"],
                "enabled": device["enabled"],
                "health": {
                    "status": health_status,
                    "last_success_at": device["last_success_at"],
                    "last_error": device["last_error"],
                    "consecutive_failures": device["consecutive_failures"],
                    "stale": health_stale,
                },
                "parameters": _fetch_parameters(connection, device["id"], stale_after, now),
                "capabilities": _fetch_capabilities(
                    connection, device["id"], device["kind"], app_config),
                "last_pulse": _fetch_latest_pulse(connection, device["id"]),
            }
        )
    return {
        "id": kind,
        "label": label,
        "kind": kind,
        "configured": True,
        "devices": device_payloads,
    }


def _matter_placeholder_party() -> dict:
    # DEV-08: a Matter integration that is not yet configured must appear
    # as not configured, not falsely healthy or offline; used only when no
    # sonoff_minid devices are registered yet (e.g. before sync_devices()).
    return {
        "id": "matter",
        "label": "GARAGE",
        "kind": "matter",
        "configured": False,
        "devices": [],
        "note": "Not configured: Matter-server integration is deferred (DEV-08).",
    }


def _matter_party(
    devices: list[dict],
    connection: sqlite3.Connection,
    app_config: AppConfig,
    now: datetime,
) -> dict:
    # DEV-09: read-only telemetry for the two commissioned Sonoff MiniD
    # switches is now active; fall back to the DEV-08 placeholder only if
    # no such devices have been registered in this deployment.
    sonoff_devices = [d for d in devices if d["kind"] == _MATTER_KIND]
    if not sonoff_devices:
        return _matter_placeholder_party()
    return _build_party("GARAGE", "matter", sonoff_devices, connection, app_config, now)


def build_dashboard_state(
    connection: sqlite3.Connection,
    app_config: AppConfig,
    *,
    clock: Clock | None = None,
) -> dict:
    """Returns the full JSON-serializable dashboard state (UI-01, UI-02).

    This is the one function the dashboard's HTTP route calls; it is also
    the contract a future native client would consume directly (see
    dashboard/__init__.py).
    """
    clock = clock or Clock()
    now = clock.utc_now()
    devices = _fetch_devices(connection)

    parties = [
        _build_party(
            "PUMP", _PLC_KIND, [d for d in devices if d["kind"] == _PLC_KIND],
            connection, app_config, now,
        ),
        _build_party(
            "GREENHOUSE", _VALVE_KIND, [
                d for d in devices if d["kind"] == _VALVE_KIND],
            connection, app_config, now,
        ),
        _matter_party(devices, connection, app_config, now),
    ]

    return {"generated_at": _iso(now), "parties": parties}
