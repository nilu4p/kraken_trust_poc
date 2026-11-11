"""Collection log: which minutes each feed was actually listening (live and replay).

An empty map can mean "no vessel" or "no data". Deciding between them needs
to know whether the collectors were up: every minute a feed is connected (or
covered by a poll window) is recorded in ``feed_minutes`` with the number of
messages received — 0 included, which is "listening, nobody heard".

Used to qualify the AIS absence at a radar pass (an echo is only "without AIS"
if the AIS feeds were listening), to warn in replay, and to draw collection
gaps on the timeline. Counters are kept in memory and flushed in batches.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Dict, Iterable, List, Optional, Tuple

from app.config import Settings, get_settings
from app.db import db, read_db

logger = logging.getLogger(__name__)

AIS_FEEDS = ("aisstream", "aishub", "vesselapi")
_lock = threading.Lock()
_pending: Dict[Tuple[str, str], int] = {}


def minute_key(when: datetime.datetime) -> str:
    """``2026-10-21T14:05`` (UTC minute)."""
    return when.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M")


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def mark(feed: str, messages: int = 0, when: Optional[datetime.datetime] = None) -> None:
    """The feed was listening during this minute (and received ``messages``)."""
    key = (feed, minute_key(when or _now()))
    with _lock:
        _pending[key] = _pending.get(key, 0) + int(messages)


def mark_span(feed: str, start: datetime.datetime, end: datetime.datetime, messages: int = 0) -> None:
    """A poll covered ``[start, end]``: every minute of it was observed (messages counted at the end)."""
    cursor = start.replace(second=0, microsecond=0)
    while cursor < end:
        mark(feed, 0, cursor)
        cursor += datetime.timedelta(minutes=1)
    mark(feed, messages, end)


def flush(settings: Optional[Settings] = None) -> int:
    """Write the pending minutes. Never raises (collection must go on)."""
    with _lock:
        items = list(_pending.items())
        _pending.clear()
    if not items:
        return 0
    try:
        with db(settings) as conn:
            for (feed, minute), count in items:
                conn.execute(
                    "INSERT INTO feed_minutes (feed, minute, messages) VALUES (?, ?, ?) "
                    "ON CONFLICT (feed, minute) DO UPDATE SET messages = feed_minutes.messages + excluded.messages",
                    (feed, minute, count),
                )
    except Exception as exc:
        logger.warning("Collection log not written (%s)", type(exc).__name__)
        with _lock:
            for key, count in items:
                _pending[key] = _pending.get(key, 0) + count
        return 0
    return len(items)


def purge(settings: Optional[Settings] = None) -> None:
    cfg = settings or get_settings()
    cutoff = minute_key(_now() - datetime.timedelta(days=cfg.ais_retention_days))
    with db(cfg) as conn:
        conn.execute("DELETE FROM feed_minutes WHERE minute < ?", (cutoff,))


def logged_before(moment: datetime.datetime, feeds: Iterable[str] = AIS_FEEDS, settings: Optional[Settings] = None) -> bool:
    """Was any feed logged before ``moment``? (False right after the first start: no history to judge.)"""
    feeds = tuple(feeds)
    flush(settings)
    marks = ", ".join("?" for _ in feeds)
    with read_db(settings) as conn:
        return conn.execute(f"SELECT 1 FROM feed_minutes WHERE feed IN ({marks}) AND minute < ? LIMIT 1",
                            (*feeds, minute_key(moment))).fetchone() is not None


def coverage(start: datetime.datetime, end: datetime.datetime, feeds: Iterable[str] = AIS_FEEDS,
             settings: Optional[Settings] = None) -> Optional[float]:
    """Share (0–1) of the minutes of ``[start, end]`` during which at least one of ``feeds`` listened.

    None when nothing was ever logged (history older than the collection log):
    unknown, not zero.
    """
    feeds = tuple(feeds)
    flush(settings)
    total = max(1, int((end - start).total_seconds() // 60))
    marks = ", ".join("?" for _ in feeds)
    with read_db(settings) as conn:
        if conn.execute(f"SELECT 1 FROM feed_minutes WHERE feed IN ({marks}) LIMIT 1", feeds).fetchone() is None:
            return None
        covered = conn.execute(
            f"SELECT COUNT(DISTINCT minute) FROM feed_minutes WHERE feed IN ({marks}) AND minute >= ? AND minute < ?",
            (*feeds, minute_key(start), minute_key(end)),
        ).fetchone()[0]
    return round(min(1.0, covered / total), 3)


def minutes_by_bucket(start: datetime.datetime, end: datetime.datetime, size: int,
                      feeds: Iterable[str] = AIS_FEEDS, settings: Optional[Settings] = None) -> Dict[str, int]:
    """Listening minutes per time bucket (``size`` = ISO prefix length: 13 = hour, 10 = day)."""
    feeds = tuple(feeds)
    flush(settings)
    marks = ", ".join("?" for _ in feeds)
    with read_db(settings) as conn:
        result = conn.execute(
            f"SELECT substr(minute, 1, {int(size)}), COUNT(DISTINCT minute) FROM feed_minutes "
            f"WHERE feed IN ({marks}) AND minute >= ? AND minute <= ? GROUP BY 1",
            (*feeds, minute_key(start), minute_key(end)),
        ).fetchall()
    return {str(r[0]): int(r[1]) for r in result}


def gaps(start: datetime.datetime, end: datetime.datetime, min_minutes: int = 15,
         feeds: Iterable[str] = AIS_FEEDS, settings: Optional[Settings] = None) -> List[Dict[str, str]]:
    """Collection gaps of at least ``min_minutes`` in ``[start, end]`` (empty when never logged)."""
    feeds = tuple(feeds)
    flush(settings)
    marks = ", ".join("?" for _ in feeds)
    with read_db(settings) as conn:
        if conn.execute(f"SELECT 1 FROM feed_minutes WHERE feed IN ({marks}) LIMIT 1", feeds).fetchone() is None:
            return []
        seen = {r[0] for r in conn.execute(
            f"SELECT DISTINCT minute FROM feed_minutes WHERE feed IN ({marks}) AND minute >= ? AND minute < ?",
            (*feeds, minute_key(start), minute_key(end))).fetchall()}
    result, gap_start, cursor = [], None, start.replace(second=0, microsecond=0)
    while cursor < end:
        if minute_key(cursor) not in seen:
            gap_start = gap_start or cursor
        elif gap_start is not None:
            if (cursor - gap_start).total_seconds() >= min_minutes * 60:
                result.append({"start": gap_start.isoformat(), "end": cursor.isoformat()})
            gap_start = None
        cursor += datetime.timedelta(minutes=1)
    if gap_start is not None and (end - gap_start).total_seconds() >= min_minutes * 60:
        result.append({"start": gap_start.isoformat(), "end": end.isoformat()})
    return result
