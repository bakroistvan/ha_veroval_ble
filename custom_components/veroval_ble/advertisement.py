"""Helpers for BPU26 advertisement freshness.

Home Assistant's scanner cache can keep delivering a
``BluetoothServiceInfoBleak`` (and keep ``coordinator.available`` True)
long after the cuff stopped advertising. ``service_info.time`` is the
scanner monotonic timestamp of the last real packet when the cache is
honest; the same stamp replayed is not a new sighting.
"""

from __future__ import annotations

import time


def advertisement_monotonic_time(service_info: object) -> float | None:
    """Return the scanner monotonic timestamp, or None if it is missing."""
    ad_time = getattr(service_info, "time", None)
    if isinstance(ad_time, (int, float)):
        return float(ad_time)
    return None


def advertisement_is_live(
    service_info: object,
    now: float | None = None,
    *,
    max_age: float,
    require_timestamp: bool = False,
    last_seen_stamp: float | None = None,
) -> bool:
    """Return True if *service_info* looks like a live scan result.

    A missing timestamp is stale when *require_timestamp* is True (config
    flow / pairing) and live when False (unit tests / older Home Assistant).
    A stamp equal to or older than *last_seen_stamp* is a cache replay.
    """
    if now is None:
        now = time.monotonic()
    ad_time = advertisement_monotonic_time(service_info)
    if ad_time is None:
        return not require_timestamp
    if last_seen_stamp is not None and ad_time <= last_seen_stamp:
        return False
    age = now - ad_time
    return 0 <= age <= max_age
