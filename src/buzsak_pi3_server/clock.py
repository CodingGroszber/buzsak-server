"""Monotonic-vs-wall clock separation (POL-06).

In-process intervals, timeouts, and deadlines must use a monotonic clock so
they are immune to wall-clock jumps (NTP steps, manual changes). Wall time is
only used for stored/observed timestamps, always in UTC.
"""

from __future__ import annotations

import dataclasses
import time
from datetime import datetime, timezone


@dataclasses.dataclass(frozen=True)
class ClockSnapshot:
    monotonic_seconds: float
    wall_utc: datetime


class Clock:
    """Thin wrapper so callers can inject a fake clock in tests."""

    def monotonic(self) -> float:
        return time.monotonic()

    def utc_now(self) -> datetime:
        return datetime.now(timezone.utc)

    def snapshot(self) -> ClockSnapshot:
        return ClockSnapshot(monotonic_seconds=self.monotonic(), wall_utc=self.utc_now())


def wall_clock_jumped(
    before: ClockSnapshot,
    after: ClockSnapshot,
    tolerance_seconds: float = 2.0,
) -> bool:
    """Detect a significant wall-clock change relative to monotonic elapsed time.

    A backward or forward jump beyond `tolerance_seconds` means the wall
    clock was stepped (NTP correction, manual change) and must not be used to
    extend command lifetime or claim freshness (POL-06).
    """
    monotonic_elapsed = after.monotonic_seconds - before.monotonic_seconds
    wall_elapsed = (after.wall_utc - before.wall_utc).total_seconds()
    return abs(wall_elapsed - monotonic_elapsed) > tolerance_seconds
