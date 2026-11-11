"""Cache of the activity timeline for periods that can no longer change.

A bucket (hour or day) older than ``STABLE_AFTER`` is computed once and kept:
the live feeds no longer add data that far back (AISHub minutes, VesselAPI 4 h
windows). Late data does exist — a VMS import, a Sentinel-1 scene analysed days
after its acquisition — so every write that lands in the past invalidates the
buckets it touches (:func:`invalidate`).
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, Iterable, Optional

from app.config import Settings
from app.db import db, read_db

logger = logging.getLogger(__name__)

STABLE_AFTER = datetime.timedelta(hours=6)


def stable_before(now: datetime.datetime) -> datetime.datetime:
    return now - STABLE_AFTER


def load(bucket: str, first: str, last: str, settings: Optional[Settings] = None) -> Dict[str, Dict[str, int]]:
    try:
        with read_db(settings) as conn:
            records = conn.execute("SELECT key, data FROM timeline_cache WHERE bucket = ? AND key >= ? AND key <= ?",
                                   (bucket, first, last)).fetchall()
    except Exception as exc:  # cache trouble never breaks the timeline: it is recomputed
        logger.warning("Timeline cache unreadable: %s", type(exc).__name__)
        return {}
    return {r[0]: json.loads(r[1]) for r in records}


def save(bucket: str, values: Dict[str, Dict[str, int]], settings: Optional[Settings] = None) -> None:
    if not values:
        return
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        with db(settings) as conn:
            for key, data in values.items():
                conn.execute(
                    "INSERT INTO timeline_cache (bucket, key, data, computed_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (bucket, key) DO UPDATE SET data = excluded.data, computed_at = excluded.computed_at",
                    (bucket, key, json.dumps(data), now),
                )
    except Exception as exc:
        logger.warning("Timeline cache not written: %s", type(exc).__name__)


def invalidate(moments: Iterable[Any], settings: Optional[Settings] = None) -> None:
    """Forget the cached hours / days between the earliest and latest of ``moments`` (ISO or datetime)."""
    from app.core.timeframe import parse_instant

    parsed = [m for m in (parse_instant(x) for x in moments) if m is not None]
    if not parsed:
        return
    low, high = min(parsed), max(parsed)
    try:
        with db(settings) as conn:
            conn.execute("DELETE FROM timeline_cache WHERE bucket = 'hour' AND key >= ? AND key <= ?",
                         (low.strftime("%Y-%m-%dT%H"), high.strftime("%Y-%m-%dT%H")))
            conn.execute("DELETE FROM timeline_cache WHERE bucket = 'day' AND key >= ? AND key <= ?",
                         (low.strftime("%Y-%m-%d"), high.strftime("%Y-%m-%d")))
    except Exception as exc:
        logger.warning("Timeline cache not invalidated: %s", type(exc).__name__)
