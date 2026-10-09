"""Applies one poll result to observations_latest/observations_history
(POL-07 through POL-12).

Cadence gating for continuous metrics (POL-09: "at most one selected fresh
sample per metric per cadence interval") is the caller's responsibility —
the poller only calls :func:`record_observation` for a continuous metric
when its configured cadence has elapsed. This module only implements the
per-call value/history policy, not scheduling (ARC-07).
"""

from __future__ import annotations

import dataclasses
import sqlite3

# Categories whose history is change-only: initial valid observation, then
# only subsequent observed value changes (POL-08, POL-10). "counter" policy
# is explicitly deferred (POL-10: "explicit per metric") and defaults to
# change-only here until a metric-specific policy is implemented.
_CHANGE_ONLY_CATEGORIES = frozenset(
    {"boolean", "configuration", "identity", "counter", "diagnostic"}
)

# A poll result is either good, or explains why it isn't; "stale" is a
# derived/display-time quality (POL-11), never written by this function.
_WRITABLE_QUALITIES = frozenset({"good", "unavailable", "invalid"})


class ObservationError(ValueError):
    """Raised for invalid inputs to :func:`record_observation`."""


@dataclasses.dataclass(frozen=True)
class ObservationResult:
    value_changed: bool
    history_appended: bool
    revision: int


def record_observation(
    connection: sqlite3.Connection,
    *,
    device_id: str,
    parameter_id: str,
    category: str,
    value: str | None,
    value_type: str,
    quality: str,
    observed_at: str,
) -> ObservationResult:
    """Upserts ``observations_latest`` and appends to
    ``observations_history`` as required for ``category``.

    ``value``/``value_type`` are only stored when ``quality == "good"``;
    an invalid/unavailable poll refreshes ``observed_at``/``quality`` but
    retains the last valid value (POL-07).

    Runs as one transaction: the read-then-write must not interleave with
    a concurrent writer for the same (device_id, parameter_id), and the
    latest-row upsert and history-row insert must not partially apply.
    """
    if quality not in _WRITABLE_QUALITIES:
        raise ObservationError(
            f"quality must be one of {sorted(_WRITABLE_QUALITIES)}, got {quality!r}"
        )

    connection.execute("BEGIN IMMEDIATE")
    try:
        previous = connection.execute(
            "SELECT value, value_type, revision FROM observations_latest "
            "WHERE device_id = ? AND parameter_id = ?",
            (device_id, parameter_id),
        ).fetchone()

        is_valid = quality == "good"
        is_first_observation = previous is None
        value_changed = is_valid and (
            is_first_observation or (value, value_type) != (
                previous[0], previous[1])
        )
        revision = (previous[2] if previous else 0) + \
            (1 if value_changed else 0)

        if is_first_observation:
            connection.execute(
                "INSERT INTO observations_latest "
                "(device_id, parameter_id, value, value_type, quality, observed_at, last_changed_at, revision) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    device_id,
                    parameter_id,
                    value if is_valid else None,
                    value_type,
                    quality,
                    observed_at,
                    observed_at if is_valid else None,
                    revision,
                ),
            )
        elif is_valid:
            connection.execute(
                "UPDATE observations_latest SET "
                "value = ?, value_type = ?, quality = ?, observed_at = ?, "
                "last_changed_at = CASE WHEN ? THEN ? ELSE last_changed_at END, "
                "revision = ? "
                "WHERE device_id = ? AND parameter_id = ?",
                (
                    value,
                    value_type,
                    quality,
                    observed_at,
                    value_changed,
                    observed_at,
                    revision,
                    device_id,
                    parameter_id,
                ),
            )
        else:
            connection.execute(
                "UPDATE observations_latest SET quality = ?, observed_at = ? "
                "WHERE device_id = ? AND parameter_id = ?",
                (quality, observed_at, device_id, parameter_id),
            )

        history_appended = False
        if is_valid:
            if category in _CHANGE_ONLY_CATEGORIES:
                history_appended = is_first_observation or value_changed
            else:
                history_appended = True  # "continuous": one row per caller-gated cadence tick

        if history_appended:
            connection.execute(
                "INSERT INTO observations_history "
                "(device_id, parameter_id, value, value_type, quality, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (device_id, parameter_id, value, value_type, quality, observed_at),
            )
    except Exception:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")

    return ObservationResult(
        value_changed=value_changed, history_appended=history_appended, revision=revision
    )
