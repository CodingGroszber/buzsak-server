"""Garden PLC adapter contract: parsing for ``GET /api/state`` (DEV-01).

Ground truth verified directly against firmware source; see
docs/adapters/garden_plc.md for the inspected files, endpoints, JSON shapes,
and firmware quirks this module encodes. Only parsing and capability
metadata live here — no HTTP client, retry/backoff, or dispatch (ARC-07).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from buzsak_pi3_server.adapters.errors import AdapterParseError

DEVICE_KIND = "garden_plc"

# DEV-04: only these outputs are firmware-confirmed controllable relays.
# Anything else (switch_led, wifi_led) is read-only and must never be
# advertised or executed as a command target.
CONTROLLABLE_OUTPUTS: tuple[str, ...] = ("well_pump", "tank_pump")


@dataclasses.dataclass(frozen=True)
class DigitalInput:
    name: str
    label: str
    state: bool


@dataclasses.dataclass(frozen=True)
class DigitalOutput:
    name: str
    label: str
    state: bool
    controllable: bool


@dataclasses.dataclass(frozen=True)
class AnalogChannel:
    name: str
    label: str
    mv: int
    signal: bool
    bar: float | None = None
    liters: float | None = None
    low: bool | None = None

    @property
    def water_level_valid(self) -> bool:
        """DEV-05: the firmware always includes ``liters``/``low`` for the
        ``water_level`` channel even when ``signal`` is false (disconnected
        or zero-current sensor). Callers must gate on this property, not on
        the mere presence of the ``liters``/``low`` keys — see
        docs/adapters/garden_plc.md."""
        return self.signal and self.liters is not None


@dataclasses.dataclass(frozen=True)
class PlcState:
    firmware: str
    ip: str
    inputs: tuple[DigitalInput, ...]
    outputs: tuple[DigitalOutput, ...]
    analog: tuple[AnalogChannel, ...]

    def output(self, name: str) -> DigitalOutput:
        for output in self.outputs:
            if output.name == name:
                return output
        raise KeyError(name)

    def analog_channel(self, name: str) -> AnalogChannel:
        for channel in self.analog:
            if channel.name == name:
                return channel
        raise KeyError(name)


def _get_dict(data: Any, key: str, where: str) -> Any:
    if not isinstance(data, dict) or key not in data:
        raise AdapterParseError(f"{where}: missing required field {key!r}")
    return data[key]


def _get_str(data: dict[str, Any], key: str, where: str) -> str:
    value = _get_dict(data, key, where)
    if not isinstance(value, str):
        raise AdapterParseError(f"{where}: field {key!r} must be a string")
    return value


def _get_bool(data: dict[str, Any], key: str, where: str) -> bool:
    value = _get_dict(data, key, where)
    if not isinstance(value, bool):
        raise AdapterParseError(f"{where}: field {key!r} must be a boolean")
    return value


def _get_number(data: dict[str, Any], key: str, where: str) -> float:
    value = _get_dict(data, key, where)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AdapterParseError(f"{where}: field {key!r} must be a number")
    return value


def _get_list(data: dict[str, Any], key: str, where: str) -> list[Any]:
    value = _get_dict(data, key, where)
    if not isinstance(value, list):
        raise AdapterParseError(f"{where}: field {key!r} must be a list")
    return value


def _parse_input(item: Any, index: int) -> DigitalInput:
    where = f"inputs[{index}]"
    if not isinstance(item, dict):
        raise AdapterParseError(f"{where}: expected an object")
    return DigitalInput(
        name=_get_str(item, "name", where),
        label=_get_str(item, "label", where),
        state=_get_bool(item, "state", where),
    )


def _parse_output(item: Any, index: int) -> DigitalOutput:
    where = f"outputs[{index}]"
    if not isinstance(item, dict):
        raise AdapterParseError(f"{where}: expected an object")
    return DigitalOutput(
        name=_get_str(item, "name", where),
        label=_get_str(item, "label", where),
        state=_get_bool(item, "state", where),
        controllable=_get_bool(item, "controllable", where),
    )


def _parse_analog(item: Any, index: int) -> AnalogChannel:
    where = f"analog[{index}]"
    if not isinstance(item, dict):
        raise AdapterParseError(f"{where}: expected an object")
    name = _get_str(item, "name", where)
    signal = _get_bool(item, "signal", where)
    bar = float(_get_number(item, "bar", where)) if "bar" in item else None
    liters = float(_get_number(item, "liters", where)
                   ) if "liters" in item else None
    low = _get_bool(item, "low", where) if "low" in item else None
    return AnalogChannel(
        name=name,
        label=_get_str(item, "label", where),
        mv=int(_get_number(item, "mv", where)),
        signal=signal,
        bar=bar,
        liters=liters,
        low=low,
    )


def parse_state(payload: Any) -> PlcState:
    """Parse a ``GET /api/state`` JSON body into a :class:`PlcState`.

    Raises :class:`AdapterParseError` on any missing or mistyped field
    (DEV-06) rather than guessing or silently defaulting.
    """
    if not isinstance(payload, dict):
        raise AdapterParseError("state: expected a JSON object")

    firmware = _get_str(payload, "firmware", "state")
    ip = _get_str(payload, "ip", "state")
    inputs = tuple(
        _parse_input(item, i) for i, item in enumerate(_get_list(payload, "inputs", "state"))
    )
    outputs = tuple(
        _parse_output(item, i) for i, item in enumerate(_get_list(payload, "outputs", "state"))
    )
    analog = tuple(
        _parse_analog(item, i) for i, item in enumerate(_get_list(payload, "analog", "state"))
    )
    return PlcState(firmware=firmware, ip=ip, inputs=inputs, outputs=outputs, analog=analog)
