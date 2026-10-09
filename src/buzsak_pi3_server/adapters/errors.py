"""Adapter parsing errors (DEV-06).

A malformed or unexpectedly shaped device response must produce a visible,
distinguishable error rather than being silently coerced or ignored.
"""

from __future__ import annotations


class AdapterParseError(ValueError):
    """Raised when a device response does not match its documented contract."""
