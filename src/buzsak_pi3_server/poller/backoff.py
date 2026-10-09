"""Capped exponential backoff with jitter (POL-03).

Acts as this project's circuit-breaker/cool-down: once `max_seconds` is
reached, a persistently unreachable device is retried at a steady, capped
cadence instead of hammering it or growing the delay without bound.
"""

from __future__ import annotations

import random
from typing import Callable


def compute_backoff_seconds(
    consecutive_failures: int,
    *,
    base_seconds: float,
    max_seconds: float,
    rand: Callable[[], float] = random.random,
) -> float:
    """Returns the delay before the next retry after `consecutive_failures`.

    Uses "equal jitter": half of the capped exponential delay is fixed, half
    is randomized, so many failing devices never retry in lockstep but the
    delay also never collapses to near-zero. `rand` is injectable for
    deterministic tests.
    """
    if consecutive_failures <= 0:
        return 0.0
    exponent = consecutive_failures - 1
    capped = min(max_seconds, base_seconds * (2 ** exponent))
    half = capped / 2
    return half + half * rand()
