"""Tests for buzsak_pi3_server.adapters.matter_server (DEV-09, DEV-01)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.adapters.matter_server import find_node, parse_node

FIXTURES = Path(__file__).parent / "fixtures" / "matter_server"


def _load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_find_node_picks_the_matching_node_id():
    nodes = _load("get_nodes_ok.json")

    node = find_node(nodes, 3)

    assert node["node_id"] == 3


def test_find_node_raises_when_node_id_is_absent():
    nodes = _load("get_nodes_ok.json")

    with pytest.raises(AdapterParseError):
        find_node(nodes, 999)


def test_parse_node_extracts_on_off_and_identity_fields():
    nodes = _load("get_nodes_ok.json")

    state = parse_node(find_node(nodes, 1))

    assert state.node_id == 1
    assert state.available is True
    assert state.on_off is False
    assert state.vendor_name == "SONOFF"
    assert state.product_name == "SONOFF MINI-D Wi-Fi Smart Switch"
    assert state.serial_number == "25517000034110"


def test_parse_node_treats_null_on_off_as_none_not_false():
    node = _load("node_on_off_null.json")

    state = parse_node(node)

    assert state.on_off is None


def test_parse_node_raises_when_attributes_missing():
    node = _load("malformed_missing_attributes.json")

    with pytest.raises(AdapterParseError):
        parse_node(node)
