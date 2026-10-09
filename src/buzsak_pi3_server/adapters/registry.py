"""Adapter registry: reconciles config `kind` spellings with adapters (DEV-01).

`config.yaml` uses human-authored, hyphenated kind values (e.g.
`"garden-plc"`), while adapter modules define underscored `DEVICE_KIND`
constants (e.g. `"garden_plc"`) and every existing DB row/test uses that
underscored form. This module is the single place that reconciles the two
spellings so the poller and `device_registration.py` never guess at a
mapping (backlog.md "kind-string inconsistency").
"""

from __future__ import annotations

from buzsak_pi3_server.adapters import garden_plc, matter_server, valve_controller


class UnknownDeviceKindError(ValueError):
    """Raised when a config `kind` does not match any registered adapter."""


# Every accepted spelling (config-style hyphenated, and the adapter's own
# canonical underscored DEVICE_KIND) maps to the canonical underscored kind
# used everywhere in the database.
_ALIASES: dict[str, str] = {
    "garden-plc": garden_plc.DEVICE_KIND,
    garden_plc.DEVICE_KIND: garden_plc.DEVICE_KIND,
    "valve-controller": valve_controller.DEVICE_KIND,
    valve_controller.DEVICE_KIND: valve_controller.DEVICE_KIND,
    "sonoff-minid": matter_server.DEVICE_KIND,
    matter_server.DEVICE_KIND: matter_server.DEVICE_KIND,
}


def canonical_kind(raw_kind: str) -> str:
    """Resolves a config-supplied `kind` to its canonical underscored form.

    Raises UnknownDeviceKindError rather than guessing or defaulting, so an
    unrecognized kind fails startup/sync clearly (DEV-06 spirit).
    """
    try:
        return _ALIASES[raw_kind]
    except KeyError:
        raise UnknownDeviceKindError(
            f"unrecognized device kind {raw_kind!r}; no adapter is registered for it"
        ) from None
