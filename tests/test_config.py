from __future__ import annotations

import pytest

from buzsak_pi3_server.config import ConfigError, load_config


def test_load_config_parses_example_file(example_config_path):
    config = load_config(example_config_path)

    assert config.database.path.endswith(".sqlite3")
    assert config.database.synchronous == "FULL"
    assert config.polling.default_interval_seconds == 1.0
    assert config.control.valve_enabled is False
    assert config.control.command_lifetime_seconds == 12
    assert config.control.confirmation_timeout_seconds == 5.0
    assert config.control.max_attempts == 1
    assert config.control.per_device_queue_depth == 3
    assert len(config.devices) == 4
    assert {device.id for device in config.devices} == {
        "garden-plc", "valve-controller", "sonoff-1", "sonoff-2"}
    assert all(device.enabled is False for device in config.devices)


def test_missing_database_key_raises(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text("server:\n  bind_port: 8000\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="database"):
        load_config(bad_config)


def test_duplicate_device_id_raises(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        """
database:
  path: "/tmp/db.sqlite3"
devices:
  - id: "dup"
    kind: "garden-plc"
    address: "http://example/"
  - id: "dup"
    kind: "valve-controller"
    address: "http://example2/"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicate device id"):
        load_config(bad_config)


def test_invalid_port_raises(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        """
database:
  path: "/tmp/db.sqlite3"
server:
  bind_port: 70000
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="bind_port"):
        load_config(bad_config)


def test_control_queue_depth_cannot_exceed_approved_bound(tmp_path):
    bad_config = tmp_path / "bad.yaml"
    bad_config.write_text(
        "database:\n  path: '/tmp/db.sqlite3'\ncontrol:\n  per_device_queue_depth: 4\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="per_device_queue_depth"):
        load_config(bad_config)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "does_not_exist.yaml")


def test_device_label_is_optional_and_defaults_to_none(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
database:
  path: "/tmp/db.sqlite3"
devices:
  - id: "sonoff-1"
    kind: "sonoff-minid"
    address: "ws://example/ws#node_id=1"
    label: "GARAGE-RIGHT"
  - id: "sonoff-2"
    kind: "sonoff-minid"
    address: "ws://example/ws#node_id=3"
""",
        encoding="utf-8",
    )

    config = load_config(config_path)
    labels = {device.id: device.label for device in config.devices}

    assert labels == {"sonoff-1": "GARAGE-RIGHT", "sonoff-2": None}
