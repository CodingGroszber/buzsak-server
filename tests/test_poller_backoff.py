"""Tests for buzsak_pi3_server.poller.backoff (POL-03)."""

from __future__ import annotations

from buzsak_pi3_server.poller.backoff import compute_backoff_seconds


def test_zero_failures_means_no_delay():
    assert compute_backoff_seconds(
        0, base_seconds=1.0, max_seconds=60.0) == 0.0


def test_delay_grows_exponentially_before_the_cap():
    # rand() = 0 isolates the fixed half of "equal jitter" for a clean check.
    first = compute_backoff_seconds(
        1, base_seconds=1.0, max_seconds=60.0, rand=lambda: 0.0)
    second = compute_backoff_seconds(
        2, base_seconds=1.0, max_seconds=60.0, rand=lambda: 0.0)
    third = compute_backoff_seconds(
        3, base_seconds=1.0, max_seconds=60.0, rand=lambda: 0.0)

    assert first == 0.5   # half of base_seconds * 2**0 = 1.0
    assert second == 1.0  # half of 2.0
    assert third == 2.0   # half of 4.0


def test_delay_never_exceeds_the_cap():
    delay = compute_backoff_seconds(
        50, base_seconds=1.0, max_seconds=60.0, rand=lambda: 1.0)
    assert delay == 60.0


def test_jitter_stays_within_the_capped_delay():
    delay = compute_backoff_seconds(
        3, base_seconds=1.0, max_seconds=60.0, rand=lambda: 1.0)
    # capped = 4.0; equal jitter means result is in [2.0, 4.0].
    assert 2.0 <= delay <= 4.0
