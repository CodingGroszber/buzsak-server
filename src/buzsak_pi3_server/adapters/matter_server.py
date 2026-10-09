"""Matter-server / Sonoff MiniD adapter contract (DEV-09).

Ground truth verified by connecting live (read-only) to the running
python-matter-server instance and inspecting its real ``get_nodes``
response; see docs/adapters/matter_server.md for the inspected protocol,
payload shape, and firmware/node quirks this module encodes. Only parsing
lives here — no WebSocket client, retry/backoff, or dispatch (ARC-07).
"""

from __future__ import annotations

import dataclasses
from typing import Any

from buzsak_pi3_server.adapters.errors import AdapterParseError

DEVICE_KIND = "sonoff_minid"

_VENDOR_NAME = "0/40/1"
_PRODUCT_NAME = "0/40/3"
_SERIAL_NUMBER = "0/40/15"
_ON_OFF = "1/6/0"


@dataclasses.dataclass(frozen=True)
class SonoffState:
    node_id: int
    available: bool
    on_off: bool | None
    vendor_name: str
    product_name: str
    serial_number: str


def find_node(nodes: Any, node_id: int) -> dict[str, Any]:
    """Picks one node's dict out of a ``get_nodes`` result list (DEV-01).

    The Matter-server always returns *all* commissioned nodes in one
    response (see docs/adapters/matter_server.md); callers filter by
    ``node_id`` since a verified single-node query does not exist.
    """
    if not isinstance(nodes, list):
        raise AdapterParseError("get_nodes result: expected a list")
    for node in nodes:
        if isinstance(node, dict) and node.get("node_id") == node_id:
            return node
    raise AdapterParseError(
        f"get_nodes result: no node with node_id={node_id}")


def parse_node(node: Any) -> SonoffState:
    if not isinstance(node, dict):
        raise AdapterParseError("matter node: expected a JSON object")

    node_id = node.get("node_id")
    if not isinstance(node_id, int):
        raise AdapterParseError("matter node: missing/invalid 'node_id'")

    available = node.get("available")
    if not isinstance(available, bool):
        raise AdapterParseError("matter node: missing/invalid 'available'")

    attributes = node.get("attributes")
    if not isinstance(attributes, dict):
        raise AdapterParseError("matter node: missing 'attributes'")

    if _ON_OFF not in attributes:
        raise AdapterParseError(
            f"matter node: missing attribute {_ON_OFF!r}")
    on_off = attributes[_ON_OFF]
    if on_off is not None and not isinstance(on_off, bool):
        raise AdapterParseError(
            f"matter node: attribute {_ON_OFF!r} must be a boolean or null")

    strings = {}
    for label, key in (
        ("vendor name", _VENDOR_NAME),
        ("product name", _PRODUCT_NAME),
        ("serial number", _SERIAL_NUMBER),
    ):
        value = attributes.get(key)
        if not isinstance(value, str):
            raise AdapterParseError(
                f"matter node: missing/invalid {label} attribute {key!r}")
        strings[key] = value

    return SonoffState(
        node_id=node_id,
        available=available,
        on_off=on_off,
        vendor_name=strings[_VENDOR_NAME],
        product_name=strings[_PRODUCT_NAME],
        serial_number=strings[_SERIAL_NUMBER],
    )
