"""Non-secret YAML configuration loading and validation (OPS-01, OPS-02).

Secrets (tokens, credentials) are never read from this file; they are
provisioned separately by the deployment environment.
"""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH_ENV_VAR = "BUZSAK_CONFIG"


class ConfigError(ValueError):
    """Raised for invalid or unsafe configuration (OPS-02)."""


@dataclasses.dataclass(frozen=True)
class DatabaseConfig:
    path: str
    busy_timeout_ms: int = 5000
    synchronous: str = "FULL"  # DB-01 initial durability baseline


@dataclasses.dataclass(frozen=True)
class ServerConfig:
    bind_host: str = "127.0.0.1"
    bind_port: int = 8000


@dataclasses.dataclass(frozen=True)
class PollingConfig:
    default_interval_seconds: float = 1.0
    stale_multiplier: float = 3.0  # POL-11 proposed default
    request_timeout_seconds: float = 5.0  # POL-03
    max_response_bytes: int = 65536  # POL-03 bounded response size
    backoff_base_seconds: float = 1.0  # POL-03 capped exponential backoff
    backoff_max_seconds: float = 60.0


@dataclasses.dataclass(frozen=True)
class ControlConfig:
    valve_enabled: bool = False
    command_lifetime_seconds: int = 12
    confirmation_timeout_seconds: float = 5.0
    max_attempts: int = 1
    per_device_queue_depth: int = 3


@dataclasses.dataclass(frozen=True)
class DeviceConfig:
    id: str
    kind: str
    address: str
    enabled: bool = False
    poll_interval_seconds: float | None = None
    label: str | None = None  # UI-only human-readable display name


@dataclasses.dataclass(frozen=True)
class AppConfig:
    database: DatabaseConfig
    server: ServerConfig
    polling: PollingConfig
    devices: tuple[DeviceConfig, ...]
    control: ControlConfig = dataclasses.field(default_factory=ControlConfig)


def _require_keys(data: dict[str, Any], keys: tuple[str, ...], where: str) -> None:
    missing = [key for key in keys if key not in data]
    if missing:
        raise ConfigError(
            f"{where}: missing required key(s): {', '.join(missing)}")


def _parse_database(data: dict[str, Any]) -> DatabaseConfig:
    _require_keys(data, ("path",), "database")
    path = data["path"]
    if not isinstance(path, str) or not path:
        raise ConfigError("database.path must be a non-empty string")
    busy_timeout_ms = int(data.get("busy_timeout_ms", 5000))
    if busy_timeout_ms <= 0:
        raise ConfigError("database.busy_timeout_ms must be positive")
    synchronous = str(data.get("synchronous", "FULL")).upper()
    if synchronous not in {"FULL", "NORMAL", "OFF"}:
        raise ConfigError(
            "database.synchronous must be one of FULL, NORMAL, OFF")
    return DatabaseConfig(path=path, busy_timeout_ms=busy_timeout_ms, synchronous=synchronous)


def _parse_server(data: dict[str, Any]) -> ServerConfig:
    bind_host = str(data.get("bind_host", "127.0.0.1"))
    bind_port = int(data.get("bind_port", 8000))
    if not (0 < bind_port < 65536):
        raise ConfigError("server.bind_port must be between 1 and 65535")
    return ServerConfig(bind_host=bind_host, bind_port=bind_port)


def _parse_polling(data: dict[str, Any]) -> PollingConfig:
    default_interval_seconds = float(data.get("default_interval_seconds", 1.0))
    if default_interval_seconds <= 0:
        raise ConfigError("polling.default_interval_seconds must be positive")
    stale_multiplier = float(data.get("stale_multiplier", 3.0))
    if stale_multiplier <= 1.0:
        raise ConfigError("polling.stale_multiplier must be greater than 1.0")
    request_timeout_seconds = float(data.get("request_timeout_seconds", 5.0))
    if request_timeout_seconds <= 0:
        raise ConfigError("polling.request_timeout_seconds must be positive")
    max_response_bytes = int(data.get("max_response_bytes", 65536))
    if max_response_bytes <= 0:
        raise ConfigError("polling.max_response_bytes must be positive")
    backoff_base_seconds = float(data.get("backoff_base_seconds", 1.0))
    if backoff_base_seconds <= 0:
        raise ConfigError("polling.backoff_base_seconds must be positive")
    backoff_max_seconds = float(data.get("backoff_max_seconds", 60.0))
    if backoff_max_seconds < backoff_base_seconds:
        raise ConfigError(
            "polling.backoff_max_seconds must be >= polling.backoff_base_seconds")
    return PollingConfig(
        default_interval_seconds=default_interval_seconds,
        stale_multiplier=stale_multiplier,
        request_timeout_seconds=request_timeout_seconds,
        max_response_bytes=max_response_bytes,
        backoff_base_seconds=backoff_base_seconds,
        backoff_max_seconds=backoff_max_seconds,
    )


def _parse_control(data: Any) -> ControlConfig:
    if not isinstance(data, dict):
        raise ConfigError("control must be a mapping")
    valve_enabled = data.get("valve_enabled", False)
    if not isinstance(valve_enabled, bool):
        raise ConfigError("control.valve_enabled must be a boolean")
    command_lifetime_seconds = int(data.get("command_lifetime_seconds", 12))
    if not 0 < command_lifetime_seconds <= 60:
        raise ConfigError(
            "control.command_lifetime_seconds must be between 1 and 60")
    confirmation_timeout_seconds = float(
        data.get("confirmation_timeout_seconds", 5.0))
    if confirmation_timeout_seconds <= 0:
        raise ConfigError(
            "control.confirmation_timeout_seconds must be positive")
    max_attempts = int(data.get("max_attempts", 1))
    if max_attempts != 1:
        raise ConfigError(
            "control.max_attempts must be 1 until replay safety is verified")
    per_device_queue_depth = int(data.get("per_device_queue_depth", 3))
    if not 1 <= per_device_queue_depth <= 3:
        raise ConfigError(
            "control.per_device_queue_depth must be between 1 and 3")
    return ControlConfig(
        valve_enabled=valve_enabled,
        command_lifetime_seconds=command_lifetime_seconds,
        confirmation_timeout_seconds=confirmation_timeout_seconds,
        max_attempts=max_attempts,
        per_device_queue_depth=per_device_queue_depth,
    )


def _parse_devices(data: list[Any]) -> tuple[DeviceConfig, ...]:
    devices: list[DeviceConfig] = []
    seen_ids: set[str] = set()
    for index, entry in enumerate(data):
        if not isinstance(entry, dict):
            raise ConfigError(f"devices[{index}] must be a mapping")
        _require_keys(entry, ("id", "kind", "address"), f"devices[{index}]")
        device_id = str(entry["id"])
        if device_id in seen_ids:
            raise ConfigError(f"duplicate device id: {device_id!r} (DEV-02)")
        seen_ids.add(device_id)
        interval = entry.get("poll_interval_seconds")
        label = entry.get("label")
        devices.append(
            DeviceConfig(
                id=device_id,
                kind=str(entry["kind"]),
                address=str(entry["address"]),
                enabled=bool(entry.get("enabled", False)),
                poll_interval_seconds=float(
                    interval) if interval is not None else None,
                label=str(label) if label is not None else None,
            )
        )
    return tuple(devices)


def load_config(path: str | Path) -> AppConfig:
    """Load and validate the non-secret YAML configuration file.

    Raises ConfigError on any structural or type problem so startup fails
    clearly instead of partially enabling control (OPS-02, OPS-03).
    """
    config_path = Path(path)
    try:
        raw_text = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read configuration file {config_path}: {exc}") from exc

    try:
        raw: Any = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(
            f"{config_path}: top-level document must be a mapping")

    _require_keys(raw, ("database",), "config")

    database = _parse_database(raw["database"])
    server = _parse_server(raw.get("server", {}))
    polling = _parse_polling(raw.get("polling", {}))
    control = _parse_control(raw.get("control", {}))
    devices_raw = raw.get("devices", [])
    if not isinstance(devices_raw, list):
        raise ConfigError("devices must be a list")
    devices = _parse_devices(devices_raw)

    return AppConfig(
        database=database, server=server, polling=polling, devices=devices,
        control=control)


def load_config_from_env() -> AppConfig:
    """Load the configuration path from `BUZSAK_CONFIG` (OPS-01, OPS-03).

    Fails clearly if the variable is unset rather than silently falling back
    to a guessed path, so invalid configuration never partially starts a
    service.
    """
    path = os.environ.get(CONFIG_PATH_ENV_VAR)
    if not path:
        raise ConfigError(
            f"environment variable {CONFIG_PATH_ENV_VAR} must point to the YAML config file"
        )
    return load_config(path)
