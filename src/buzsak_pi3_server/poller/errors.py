"""Poller-specific errors (POL-03).

Kept separate from `adapters.errors.AdapterParseError`: this module covers
transport/network failures (unreachable, timeout, bad status, oversized or
non-JSON body), while `AdapterParseError` covers a reachable device
returning a shape that doesn't match its documented contract. The scheduler
treats both as "poll failed" but logs them distinctly.
"""

from __future__ import annotations


class PollTransportError(Exception):
    """Raised when a device cannot be reached or its response is unusable
    before adapter parsing ever begins."""
