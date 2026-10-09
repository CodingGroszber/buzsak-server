"""Per-device polling loop (POL-01, POL-02, POL-06, POL-07, ARC-01, ARC-06).

One dedicated thread per enabled device (wired in `__main__.py`): this
bounds concurrency (POL-02) naturally -- an unreachable device only ever
has one poll in flight on its own thread and never blocks any other
device's polling (ARC-06) -- without needing a shared thread pool sized for
what is still a small, fixed device fleet.
"""

from __future__ import annotations

import dataclasses
import logging
import sqlite3
import threading
from typing import Any, Callable
from urllib.parse import urljoin

from buzsak_pi3_server import db
from buzsak_pi3_server import observations as observations_module
from buzsak_pi3_server.adapters import catalog, matter_server
from buzsak_pi3_server.adapters.errors import AdapterParseError
from buzsak_pi3_server.adapters.registry import canonical_kind
from buzsak_pi3_server.clock import Clock
from buzsak_pi3_server.config import AppConfig, DeviceConfig
from buzsak_pi3_server.poller import health
from buzsak_pi3_server.poller import matter_client
from buzsak_pi3_server.poller.backoff import compute_backoff_seconds
from buzsak_pi3_server.poller.errors import PollTransportError
from buzsak_pi3_server.poller.extraction import extract_observations
from buzsak_pi3_server.poller.http_client import fetch_json

logger = logging.getLogger(__name__)

_STATE_PATH = "api/state"

ConnectionFactory = Callable[[], sqlite3.Connection]


@dataclasses.dataclass(frozen=True)
class PollOutcome:
    success: bool
    consecutive_failures: int  # 0 on success


def _iso(clock: Clock) -> str:
    return clock.utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _fetch_payload(kind: str, device: DeviceConfig, app_config: AppConfig) -> Any:
    """Retrieves the raw payload for one device (ARC-06: per-kind transport).

    Returns whatever `extract_observations(kind, ...)` expects for that
    kind: an HTTP JSON body for the two direct devices, or a single
    already-node-id-filtered Matter node dict for `sonoff_minid` (DEV-09).
    """
    if kind == matter_server.DEVICE_KIND:
        ws_url, node_id = matter_client.parse_device_address(device.address)
        nodes = matter_client.fetch_nodes(
            ws_url,
            timeout_seconds=app_config.polling.request_timeout_seconds,
            max_bytes=app_config.polling.max_response_bytes,
        )
        return matter_server.find_node(nodes, node_id)

    url = urljoin(device.address, _STATE_PATH)
    return fetch_json(
        url,
        timeout_seconds=app_config.polling.request_timeout_seconds,
        max_bytes=app_config.polling.max_response_bytes,
    )


def _record_failure(
    connection_factory: ConnectionFactory,
    device_id: str,
    clock: Clock,
    error: str,
) -> PollOutcome:
    """Records a poll failure in device_health; never raises (ARC-06).

    If the health write itself fails (e.g. a locked database), the thread
    must still back off and retry rather than crash: fall back to treating
    it as a single failure so backoff still progresses instead of letting
    the write error escape and kill this device's thread.
    """
    observed_at = _iso(clock)
    try:
        connection = connection_factory()
        try:
            consecutive_failures = health.record_poll_failure(
                connection, device_id, observed_at=observed_at, error=error)
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - last-resort guard, see docstring
        logger.exception(
            "failed to record poll failure for device %s; treating as a "
            "single failure so backoff still progresses", device_id)
        consecutive_failures = 1
    return PollOutcome(success=False, consecutive_failures=consecutive_failures)


def poll_once(
    connection_factory: ConnectionFactory,
    device: DeviceConfig,
    app_config: AppConfig,
    clock: Clock,
) -> PollOutcome:
    """Runs one poll cycle for `device`, never raising (ARC-06).

    Delegates to `_poll_once_unguarded` for the actual work, then catches
    anything that escapes its specific `except` clauses -- a 2026-10-05
    incident on the Pi showed an unanticipated exception type silently
    ending a device's worker thread (daemon threads just vanish) while
    `buzsak-poller.service` stayed "active", so every poll cycle must
    degrade to a bounded failure instead of ever propagating.
    """
    try:
        return _poll_once_unguarded(connection_factory, device, app_config, clock)
    except Exception as exc:  # noqa: BLE001 - last-resort guard, see docstring
        logger.exception(
            "device %s poll crashed with an unexpected error; degrading to a "
            "poll failure instead of letting it kill this device's thread",
            device.id,
        )
        return _record_failure(connection_factory, device.id, clock, f"unexpected error: {exc}")


def _poll_once_unguarded(
    connection_factory: ConnectionFactory,
    device: DeviceConfig,
    app_config: AppConfig,
    clock: Clock,
) -> PollOutcome:
    """Runs one poll cycle for `device`.

    Network I/O happens before any database connection is opened, and each
    connection is closed immediately after the writes it needs (ARC-04:
    never hold a transaction open during device network I/O).
    """
    kind = canonical_kind(device.kind)

    try:
        payload = _fetch_payload(kind, device, app_config)
        parsed = extract_observations(kind, payload)
    except (PollTransportError, AdapterParseError) as exc:
        logger.warning("poll failed for device %s: %s", device.id, exc)
        return _record_failure(connection_factory, device.id, clock, str(exc))

    observed_at = _iso(clock)
    categories = {
        parameter.parameter_id: parameter.category
        for parameter in catalog.CATALOG[kind].parameters
    }
    connection = connection_factory()
    try:
        try:
            for item in parsed:
                observations_module.record_observation(
                    connection,
                    device_id=device.id,
                    parameter_id=item.parameter_id,
                    category=categories[item.parameter_id],
                    value=item.value,
                    value_type=item.value_type,
                    quality=item.quality,
                    observed_at=observed_at,
                )
            health.record_poll_success(
                connection, device.id, observed_at=observed_at)
        except sqlite3.Error as exc:
            # A successful device read must never take down this device's
            # polling thread over a transient local storage error (ARC-06,
            # DB-06): log and back off instead of letting it propagate.
            logger.error(
                "poll succeeded for device %s but recording observations failed: %s",
                device.id, exc,
            )
            return PollOutcome(success=False, consecutive_failures=1)
    finally:
        connection.close()
    return PollOutcome(success=True, consecutive_failures=0)


def run_device_loop(
    device: DeviceConfig,
    app_config: AppConfig,
    stop_event: threading.Event,
    *,
    clock: Clock | None = None,
    connection_factory: ConnectionFactory | None = None,
) -> None:
    """Polls `device` until `stop_event` is set.

    Sequential by construction: the next poll never starts until the
    previous one (network I/O plus DB writes) has finished, so a slow or
    unreachable device cannot overlap polls against itself (POL-02).
    `stop_event.wait(timeout=...)` both sleeps between polls and reacts to
    shutdown immediately, instead of a plain `time.sleep()` loop.
    """
    clock = clock or Clock()
    connection_factory = connection_factory or (
        lambda: db.connect(app_config.database))
    interval = device.poll_interval_seconds or app_config.polling.default_interval_seconds

    while not stop_event.is_set():
        outcome = poll_once(connection_factory, device, app_config, clock)
        if outcome.success:
            wait_seconds = interval
        else:
            wait_seconds = compute_backoff_seconds(
                outcome.consecutive_failures,
                base_seconds=app_config.polling.backoff_base_seconds,
                max_seconds=app_config.polling.backoff_max_seconds,
            )
        stop_event.wait(timeout=wait_seconds)
