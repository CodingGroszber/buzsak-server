"""Tests for buzsak_pi3_server.dashboard.queries (UI-01, UI-02, POL-11).

These exercise build_dashboard_state() directly against a migrated SQLite
file, independent of Flask, to isolate the read-only query layer.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from buzsak_pi3_server import db, schema
from buzsak_pi3_server.config import (
    AppConfig,
    ControlConfig,
    DatabaseConfig,
    DeviceConfig,
    PollingConfig,
    ServerConfig,
)
from buzsak_pi3_server.dashboard.queries import build_dashboard_state


class _FixedClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def utc_now(self) -> datetime:
        return self._now


@pytest.fixture
def connection(tmp_path):
    config = DatabaseConfig(path=str(tmp_path / "buzsak.sqlite3"))
    conn = db.connect(config)
    schema.apply_migrations(conn)
    yield conn
    conn.close()


def _app_config(
    devices: tuple[DeviceConfig, ...] = (),
    *,
    control: ControlConfig | None = None,
) -> AppConfig:
    return AppConfig(
        database=DatabaseConfig(path=":memory:"),
        server=ServerConfig(),
        polling=PollingConfig(
            default_interval_seconds=1.0, stale_multiplier=3.0),
        devices=devices,
        control=control or ControlConfig(),
    )


def test_devices_group_into_the_three_fixed_parties(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('valve-controller', 'valve_controller', 'http://192.168.1.109/', 0, "
        "datetime('now'), datetime('now'))"
    )

    state = build_dashboard_state(connection, _app_config())

    labels = [party["label"] for party in state["parties"]]
    assert labels == ["PUMP", "GREENHOUSE", "GARAGE"]

    plc_party, valve_party, matter_party = state["parties"]
    assert plc_party["configured"] is True
    assert [d["id"] for d in plc_party["devices"]] == ["garden-plc"]
    assert [d["id"] for d in valve_party["devices"]] == ["valve-controller"]
    assert matter_party["configured"] is False
    assert matter_party["devices"] == []
    assert "note" in matter_party


def test_parameter_without_observation_reports_no_data(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO parameters (device_id, parameter_id, category) "
        "VALUES ('garden-plc', 'well_pump', 'boolean')"
    )

    state = build_dashboard_state(connection, _app_config())
    parameter = state["parties"][0]["devices"][0]["parameters"][0]

    assert parameter["has_data"] is False
    assert parameter["quality"] == "unavailable"
    assert parameter["value"] is None
    assert parameter["stale"] is False


def test_fresh_good_observation_is_not_stale(connection):
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO parameters (device_id, parameter_id, category) "
        "VALUES ('garden-plc', 'well_pump', 'boolean')"
    )
    connection.execute(
        "INSERT INTO observations_latest "
        "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
        "VALUES ('garden-plc', 'well_pump', 'true', 'bool', 'good', ?, ?, 1)",
        ("2026-09-30T11:59:58Z", "2026-09-30T11:59:58Z"),
    )

    state = build_dashboard_state(
        connection, _app_config(), clock=_FixedClock(now))
    parameter = state["parties"][0]["devices"][0]["parameters"][0]

    assert parameter["has_data"] is True
    assert parameter["quality"] == "good"
    assert parameter["stale"] is False


def test_observation_older_than_stale_threshold_is_flagged(connection):
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO parameters (device_id, parameter_id, category) "
        "VALUES ('garden-plc', 'well_pump', 'boolean')"
    )
    connection.execute(
        "INSERT INTO observations_latest "
        "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
        "VALUES ('garden-plc', 'well_pump', 'true', 'bool', 'good', ?, ?, 1)",
        ("2026-09-30T11:00:00Z", "2026-09-30T11:00:00Z"),
    )
    # default_interval_seconds=1.0 * stale_multiplier=3.0 -> stale after 3s;
    # this observation is an hour old.
    app_config = _app_config(
        devices=(DeviceConfig(id="garden-plc",
                 kind="garden-plc", address="x", enabled=True),)
    )

    state = build_dashboard_state(
        connection, app_config, clock=_FixedClock(now))
    parameter = state["parties"][0]["devices"][0]["parameters"][0]

    assert parameter["stale"] is True


def test_capabilities_are_present_but_disabled(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO capabilities (device_id, action_id, params_schema) "
        "VALUES ('garden-plc', 'set_output', '{\"name\": \"well_pump\", \"state\": \"bool\"}')"
    )

    state = build_dashboard_state(connection, _app_config())
    capability = state["parties"][0]["devices"][0]["capabilities"][0]

    assert capability["action_id"] == "set_output"
    assert capability["params_schema"] == {
        "name": "well_pump", "state": "bool"}
    assert capability["enabled"] is False
    assert "disabled_reason" in capability


def test_device_health_defaults_to_unknown_without_a_health_row(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )

    state = build_dashboard_state(connection, _app_config())
    health = state["parties"][0]["devices"][0]["health"]

    assert health["status"] == "unknown"
    assert health["consecutive_failures"] == 0


def test_device_health_is_degraded_when_last_success_is_stale(connection):
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES ('garden-plc', 'healthy', '2026-09-30T11:59:00Z', 0, "
        "'2026-09-30T11:59:00Z')"
    )
    app_config = _app_config((
        DeviceConfig(id="garden-plc", kind="garden-plc",
                     address="http://192.168.1.94/", enabled=True),
    ))

    state = build_dashboard_state(
        connection, app_config, clock=_FixedClock(now))
    health = state["parties"][0]["devices"][0]["health"]

    assert health["status"] == "degraded"
    assert health["stale"] is True


def test_disabled_device_health_is_not_downgraded_by_age(connection):
    now = datetime(2026, 9, 30, 12, 0, 0, tzinfo=timezone.utc)
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('garden-plc', 'garden_plc', 'http://192.168.1.94/', 0, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO device_health "
        "(device_id, status, last_success_at, consecutive_failures, updated_at) "
        "VALUES ('garden-plc', 'healthy', '2026-09-30T11:00:00Z', 0, "
        "'2026-09-30T11:00:00Z')"
    )
    app_config = _app_config((
        DeviceConfig(id="garden-plc", kind="garden-plc",
                     address="http://192.168.1.94/", enabled=False),
    ))

    state = build_dashboard_state(
        connection, app_config, clock=_FixedClock(now))
    health = state["parties"][0]["devices"][0]["health"]

    assert health["status"] == "healthy"
    assert health["stale"] is False


def test_matter_party_becomes_real_once_sonoff_devices_are_registered(connection):
    # DEV-09: once sonoff_minid devices exist, the Matter party must show
    # real device data instead of the DEV-08 not-configured placeholder.
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO parameters (device_id, parameter_id, category) "
        "VALUES ('sonoff-1', 'on_off', 'boolean')"
    )

    state = build_dashboard_state(connection, _app_config())

    labels = [party["label"] for party in state["parties"]]
    assert labels == ["PUMP", "GREENHOUSE", "GARAGE"]
    matter_party = state["parties"][2]
    assert matter_party["configured"] is True
    assert [d["id"] for d in matter_party["devices"]] == ["sonoff-1"]
    assert "note" not in matter_party


def test_sonoff_pulse_capability_is_enabled_unlike_every_other_capability(connection):
    # DEV-10: the one deliberate exception to "everything is disabled".
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO capabilities (device_id, action_id, params_schema) "
        "VALUES ('sonoff-1', 'pulse', '{}')"
    )

    state = build_dashboard_state(connection, _app_config())
    matter_party = state["parties"][2]
    capability = matter_party["devices"][0]["capabilities"][0]

    assert capability["action_id"] == "pulse"
    assert capability["enabled"] is True
    assert capability["disabled_reason"] is None


def test_valve_capabilities_are_disabled_by_default(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('valve-controller', 'valve_controller', 'http://192.168.1.109/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO capabilities (device_id, action_id, params_schema) VALUES "
        "('valve-controller', 'set_output', '{}'), "
        "('valve-controller', 'set_mode', '{}')"
    )

    state = build_dashboard_state(connection, _app_config())
    capabilities = state["parties"][1]["devices"][0]["capabilities"]

    assert all(not capability["enabled"] for capability in capabilities)
    assert all("HTTPS" in capability["disabled_reason"]
               for capability in capabilities)


def test_valve_capability_metadata_is_enabled_only_by_deployment_gate(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('valve-controller', 'valve_controller', 'http://192.168.1.109/', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO capabilities (device_id, action_id, params_schema) "
        "VALUES ('valve-controller', 'set_output', '{}')"
    )
    app_config = _app_config(
        devices=(DeviceConfig(
            id="valve-controller", kind="valve-controller",
            address="http://192.168.1.109/", enabled=True),),
        control=ControlConfig(valve_enabled=True),
    )

    state = build_dashboard_state(connection, app_config)
    capability = state["parties"][1]["devices"][0]["capabilities"][0]

    assert capability["enabled"] is True
    assert capability["effect"] == "relay_state_only"
    assert capability["retry_safe"] is True
    assert capability["lifetime_s"] == 12
    assert capability["requires"]["fresh"] is True


def test_last_pulse_is_none_without_any_pulse_requests(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )

    state = build_dashboard_state(connection, _app_config())

    assert state["parties"][2]["devices"][0]["last_pulse"] is None


def test_last_pulse_reflects_the_most_recent_request(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )
    connection.execute(
        "INSERT INTO pulse_requests (device_id, status, requested_at, expires_at) "
        "VALUES ('sonoff-1', 'succeeded', '2026-01-01T00:00:00Z', '2026-01-01T00:01:00Z')"
    )
    connection.execute(
        "INSERT INTO pulse_requests (device_id, status, requested_at, expires_at) "
        "VALUES ('sonoff-1', 'failed', '2026-01-01T00:05:00Z', '2026-01-01T00:06:00Z')"
    )

    state = build_dashboard_state(connection, _app_config())
    last_pulse = state["parties"][2]["devices"][0]["last_pulse"]

    assert last_pulse["status"] == "failed"


def test_device_label_falls_back_to_id_when_not_configured(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )

    state = build_dashboard_state(connection, _app_config())

    assert state["parties"][2]["devices"][0]["label"] == "sonoff-1"


def test_device_label_uses_the_configured_display_name(connection):
    connection.execute(
        "INSERT INTO devices (id, kind, address, enabled, created_at, updated_at) "
        "VALUES ('sonoff-1', 'sonoff_minid', 'ws://192.168.1.95:5580/ws#node_id=1', 1, "
        "datetime('now'), datetime('now'))"
    )
    devices = (
        DeviceConfig(id="sonoff-1", kind="sonoff-minid",
                     address="ws://192.168.1.95:5580/ws#node_id=1",
                     enabled=True, label="GARAGE-RIGHT"),
    )

    state = build_dashboard_state(connection, _app_config(devices))

    assert state["parties"][2]["devices"][0]["label"] == "GARAGE-RIGHT"
