"""Activity timeline: what happened when, to drive the time cursor (live / replay) of the situation.

One bucketed series per source of the fused picture, from the database only
(fast, no external call): vessels heard (AIS / VMS), radar detections and
24/7-rule observations, incidents opened and closed, Sentinel-1 scenes analysed.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import viewer
from app.config import get_settings
from app.core import timeframe
from app.db import is_postgres, read_db
from app.soc import timeline_cache

router = APIRouter(tags=["timeline"])

BUCKETS = {"hour": (13, datetime.timedelta(hours=1)), "day": (10, datetime.timedelta(days=1))}
MAX_BUCKETS = 2400


def _key(column: str, size: int, timestamptz: bool) -> str:
    """SQL expression giving the ISO prefix of a timestamp (hour: 13 chars, day: 10) on SQLite and PostgreSQL."""
    if timestamptz:
        fmt = 'YYYY-MM-DD"T"HH24' if size == 13 else "YYYY-MM-DD"
        return f"to_char({column} AT TIME ZONE 'UTC', '{fmt}')"
    return f"substr({column}, 1, {size})"


def _series(conn: Any, sql: str, params: tuple) -> Dict[str, int]:
    return {str(r[0]): int(r[1]) for r in conn.execute(sql, params).fetchall() if r[0] is not None}


SERIES = ("vessels", "vms", "radar", "rules", "incidents_opened", "incidents_closed", "scenes", "ais_listening_minutes")


def _bucket_start(key: str, bucket: str) -> datetime.datetime:
    return datetime.datetime.strptime(key, "%Y-%m-%dT%H" if bucket == "hour" else "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)


def _compute(begin: datetime.datetime, stop: datetime.datetime, size: int, cfg: Any) -> Any:
    """Counts per bucket between ``begin`` and ``stop`` straight from the database."""
    low, high = begin.isoformat(), stop.isoformat()
    pg = is_postgres(cfg)
    ais_key = _key("ts", size, pg)
    with read_db() as conn:
        series = {
            "vessels": _series(conn, f"SELECT {ais_key}, COUNT(DISTINCT mmsi) FROM ais_positions "
                                     f"WHERE ts >= ? AND ts <= ? AND source != 'vms' GROUP BY 1", (low, high)),
            "vms": _series(conn, f"SELECT {ais_key}, COUNT(DISTINCT mmsi) FROM ais_positions "
                                 f"WHERE ts >= ? AND ts <= ? AND source = 'vms' GROUP BY 1", (low, high)),
            "radar": _series(conn, f"SELECT {_key('o.seen_at', size, False)}, COUNT(*) FROM incident_observations o "
                                   "WHERE o.seen_at >= ? AND o.seen_at <= ? AND o.vessel_id NOT LIKE 'AIS-%' GROUP BY 1",
                             (low, high)),
            "rules": _series(conn, f"SELECT {_key('o.seen_at', size, False)}, COUNT(*) FROM incident_observations o "
                                   "WHERE o.seen_at >= ? AND o.seen_at <= ? AND o.vessel_id LIKE 'AIS-%' GROUP BY 1",
                             (low, high)),
            "incidents_opened": _series(conn, f"SELECT {_key('created_at', size, False)}, COUNT(*) FROM incidents "
                                              "WHERE created_at >= ? AND created_at <= ? GROUP BY 1", (low, high)),
            "incidents_closed": _series(conn, f"SELECT {_key('closed_at', size, False)}, COUNT(*) FROM incidents "
                                              "WHERE closed_at >= ? AND closed_at <= ? GROUP BY 1", (low, high)),
            "scenes": _series(conn, f"SELECT {_key('processed_at', size, False)}, COUNT(*) FROM processed_scenes "
                                    "WHERE processed_at >= ? AND processed_at <= ? GROUP BY 1", (low, high)),
        }
    from app.sources import feed_log

    listening = feed_log.minutes_by_bucket(begin, stop, size)
    series["ais_listening_minutes"] = listening
    logged = bool(listening) or feed_log.coverage(begin, stop) is not None
    return series, logged


@router.get("/timeline")
def timeline(
    start: Optional[str] = Query(None, max_length=40, description="ISO start (default: end - 7 days)"),
    end: Optional[str] = Query(None, max_length=40, description="ISO end (default: now)"),
    bucket: str = Query("auto", pattern="^(auto|hour|day)$"),
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Counts per hour or day between ``start`` and ``end`` (within the kept history)."""
    cfg = get_settings()
    now = timeframe.utcnow()
    oldest = now - datetime.timedelta(days=cfg.ais_retention_days)
    stop = min(timeframe.parse_instant(end) or now, now)
    begin = max(timeframe.parse_instant(start) or stop - datetime.timedelta(days=7), oldest)
    if (start and timeframe.parse_instant(start) is None) or (end and timeframe.parse_instant(end) is None):
        raise HTTPException(status_code=422, detail="Bornes invalides (format ISO 8601 attendu)")
    if begin >= stop:
        raise HTTPException(status_code=422, detail="Début postérieur à la fin")
    if bucket == "auto":
        bucket = "hour" if stop - begin <= datetime.timedelta(days=7) else "day"
    size, step = BUCKETS[bucket]
    if (stop - begin) / step > MAX_BUCKETS:
        raise HTTPException(status_code=422, detail="Intervalle trop long pour ce pas de temps")
    low, high = begin.isoformat(), stop.isoformat()
    keys: List[str] = []
    cursor = begin.replace(minute=0, second=0, microsecond=0)
    if bucket == "day":
        cursor = cursor.replace(hour=0)
    while cursor <= stop:
        keys.append(cursor.strftime("%Y-%m-%dT%H" if bucket == "hour" else "%Y-%m-%d"))
        cursor += step
    # Buckets ended before the stability horizon are served from the cache; the others are computed.
    horizon = timeline_cache.stable_before(now)
    cached = timeline_cache.load(bucket, keys[0], keys[-1]) if keys else {}
    missing = [k for k in keys if k not in cached]
    computed: Dict[str, Dict[str, int]] = {}
    logged = None
    if missing:
        first = _bucket_start(missing[0], bucket)  # whole first bucket: a cached day is never partial
        series, logged = _compute(first, stop, size, cfg)
        computed = {k: {name: values.get(k, 0) for name, values in series.items()} for k in missing}
        timeline_cache.save(bucket, {k: v for k, v in computed.items() if _bucket_start(k, bucket) + step <= horizon})
    if logged is None:
        from app.sources import feed_log

        logged = feed_log.coverage(begin, stop) is not None
    with read_db() as conn:
        ais_oldest = conn.execute("SELECT MIN(ts) FROM ais_positions").fetchone()[0]
    buckets = [{"t": k, **(cached.get(k) or computed.get(k) or {})} for k in keys]
    return {
        "start": low,
        "end": high,
        "bucket": bucket,
        "buckets": buckets,
        "cached_buckets": len(cached),
        "bucket_minutes": int(step.total_seconds() // 60),
        # False: the collection log does not cover this period (older history) — gaps unknown.
        "collection_logged": logged,
        "history": {
            "oldest_replay": oldest.isoformat(),
            "ais_oldest": ais_oldest.isoformat() if isinstance(ais_oldest, datetime.datetime) else ais_oldest,
            "retention_days": cfg.ais_retention_days,
        },
    }
