"""Persistent vessel picture: identities and position history from every feed (SQLite).

Fed continuously by the aisstream.io collector, the AISHub poller and VMS
imports (each position keeps its ``source``) so that:
- SAR passes are correlated with the AIS fix nearest in time (dead-reckoned to
  the acquisition instant) instead of a 20 s live snapshot;
- 24/7 rules (foreign fishing, AIS gaps, encounters, watchlist) run between
  satellite passes.
"""

from __future__ import annotations

import datetime
import logging
import math
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.config import Settings, get_settings
from app.db import db, is_postgres, read_db, rows, timescale_enabled
from app.sources.mid import flag_from_mmsi

logger = logging.getLogger(__name__)

_NEWER = "(ais_vessels.last_lat IS NULL OR excluded.last_seen >= ais_vessels.last_seen)"
SOURCE_LABELS = {"aisstream": "aisstream.io", "aishub": "AISHub", "vesselapi": "VesselAPI", "vms": "VMS"}
_last_store: Dict[str, float] = {}
_lock = threading.Lock()


def _iso(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).isoformat()


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def parse_ts(value: Any) -> Optional[datetime.datetime]:
    from app.pipeline.detection import _parse_time

    return _parse_time(value)


def should_store(mmsi: str, interval_s: int) -> bool:
    """Throttle: at most one stored position per vessel per ``interval_s``."""
    now = time.monotonic()
    with _lock:
        last = _last_store.get(mmsi)
        if last is not None and now - last < interval_s:
            return False
        _last_store[mmsi] = now
        if len(_last_store) > 50_000:
            _last_store.clear()
        return True


def store_positions(
    points: Iterable[Dict[str, Any]], zone_of: Any = None, settings: Optional[Settings] = None, source: str = "aisstream"
) -> int:
    """Insert positions and refresh vessel rows. ``zone_of(lon, lat)`` tags the legal zone.

    The same fix heard by two feeds is stored once (primary key mmsi + ts).
    A point may carry its own ``source`` and ``flag`` (VMS: flag state declared).
    """
    points = list(points)
    if not points:
        return 0
    cfg = settings or get_settings()
    current = _now()
    now = _iso(current)
    oldest = current - datetime.timedelta(days=cfg.ais_retention_days)
    stored = 0
    late: List[datetime.datetime] = []  # fixes landing in periods the timeline already cached
    with db(settings) as conn:
        for p in points:
            # One timestamp format everywhere (ISO 8601 UTC): every window query compares them as text,
            # and aisstream's "2026-10-20 10:00:00.1 +0000 UTC" sorted before same-day ISO bounds.
            parsed = parse_ts(p.get("ts")) if p.get("ts") else current
            feed = p.get("source") or source
            reason = _reject_reason(conn, p, parsed, current, oldest)
            if reason:
                _count_rejected(feed, reason)
                continue
            ts = _iso(parsed)
            stored += 1
            if parsed < current - datetime.timedelta(hours=6):
                late.append(parsed)
            conn.execute(
                "INSERT INTO ais_positions (mmsi, ts, lat, lon, sog, cog, heading, nav_status, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (p["mmsi"], ts, p["lat"], p["lon"], p.get("sog"), p.get("cog"), p.get("heading"), p.get("nav_status"), feed),
            )
            zone = zone_of(p["lon"], p["lat"]) if zone_of else None
            # Only a newer fix moves the vessel (feeds arrive out of order); a static-only row has no position yet.
            conn.execute(
                "INSERT INTO ais_vessels (mmsi, name, flag, first_seen, last_seen, last_lat, last_lon, last_sog, last_zone, last_source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(mmsi) DO UPDATE SET "
                "last_lat = CASE WHEN " + _NEWER + " THEN excluded.last_lat ELSE ais_vessels.last_lat END, "
                "last_lon = CASE WHEN " + _NEWER + " THEN excluded.last_lon ELSE ais_vessels.last_lon END, "
                "last_sog = CASE WHEN " + _NEWER + " THEN excluded.last_sog ELSE ais_vessels.last_sog END, "
                "last_zone = CASE WHEN " + _NEWER + " "
                "THEN COALESCE(excluded.last_zone, ais_vessels.last_zone) ELSE ais_vessels.last_zone END, "
                "last_source = CASE WHEN " + _NEWER + " THEN excluded.last_source ELSE ais_vessels.last_source END, "
                "last_seen = CASE WHEN ais_vessels.last_lat IS NULL THEN excluded.last_seen "
                "WHEN excluded.last_seen > ais_vessels.last_seen THEN excluded.last_seen ELSE ais_vessels.last_seen END, "
                "flag = COALESCE(ais_vessels.flag, excluded.flag), "
                "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE ais_vessels.name END",
                (p["mmsi"], (p.get("name") or "").strip()[:60], p.get("flag") or flag_from_mmsi(p["mmsi"]), ts, ts,
                 p["lat"], p["lon"], p.get("sog"), zone, feed),
            )
    if late:
        from app.soc import timeline_cache

        timeline_cache.invalidate([min(late), max(late)], settings)
    return stored


MAX_PLAUSIBLE_KNOTS = 50.0      # faster than any fishing / cargo vessel: a position jump, not a voyage
MIN_JUMP_KM = 2.0               # below this, timing jitter between feeds is not a glitch
FUTURE_TOLERANCE = datetime.timedelta(minutes=5)
REJECTED: Dict[str, Dict[str, int]] = {}


def _count_rejected(feed: str, reason: str) -> None:
    with _lock:
        per_feed = REJECTED.setdefault(feed, {})
        per_feed[reason] = per_feed.get(reason, 0) + 1


def _reject_reason(conn: Any, p: Dict[str, Any], parsed: Optional[datetime.datetime],
                   now: datetime.datetime, oldest: datetime.datetime) -> Optional[str]:
    """Why a fix must not enter the history (None = keep it). Bad fixes would fake tracks and alerts."""
    if parsed is None:
        return "horodatage illisible"
    if parsed > now + FUTURE_TOLERANCE:
        return "horodatage dans le futur"
    if parsed < oldest:
        return "hors de l'historique conservé"
    try:
        lat, lon = float(p["lat"]), float(p["lon"])
    except (TypeError, ValueError, KeyError):
        return "position illisible"
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return "position invalide"
    last = conn.execute("SELECT last_lat, last_lon, last_seen FROM ais_vessels WHERE mmsi = ?", (p["mmsi"],)).fetchone()
    if last is None or last[0] is None:
        return None
    previous = parse_ts(last[2])
    if previous is None:
        return None
    from app.core.geo import haversine_km

    distance = haversine_km(lat, lon, float(last[0]), float(last[1]))
    hours = abs((parsed - previous).total_seconds()) / 3600.0
    if distance > MIN_JUMP_KM and distance / 1.852 > MAX_PLAUSIBLE_KNOTS * max(hours, 1 / 60):
        return "saut de position impossible"
    return None


def dimension_length(a: Any, b: Any) -> Optional[float]:
    """Vessel length from the AIS reference-point dimensions A (to bow) + B (to stern), in metres."""
    try:
        bow, stern = float(a), float(b)
    except (TypeError, ValueError):
        return None
    length = bow + stern
    # 0 = not available; 511 = "511 m or more" placeholder: neither is a measurement.
    return length if 3 <= length <= 500 and bow < 511 and stern < 511 else None


def store_static(mmsi: str, *, name: str = "", imo: Any = None, call_sign: str = "", ship_type: Any = None,
                 length_m: Optional[float] = None, settings: Optional[Settings] = None) -> None:
    now = _iso(_now())  # static rows: first_seen / last_seen are ISO like every other timestamp
    imo_text = str(imo) if imo and str(imo).isdigit() and len(str(imo)) == 7 else None
    length = round(float(length_m), 1) if length_m and 3 <= float(length_m) <= 500 else None
    with db(settings) as conn:
        conn.execute(
            "INSERT INTO ais_vessels (mmsi, name, imo, call_sign, ship_type, flag, first_seen, last_seen, length_m) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(mmsi) DO UPDATE SET "
            "name = CASE WHEN excluded.name != '' THEN excluded.name ELSE ais_vessels.name END, "
            "imo = COALESCE(excluded.imo, ais_vessels.imo), call_sign = COALESCE(NULLIF(excluded.call_sign, ''), ais_vessels.call_sign), "
            "ship_type = COALESCE(excluded.ship_type, ais_vessels.ship_type), "
            "length_m = COALESCE(excluded.length_m, ais_vessels.length_m)",
            (mmsi, (name or "").strip()[:60], imo_text, (call_sign or "").strip()[:20],
             int(ship_type) if isinstance(ship_type, (int, float)) or str(ship_type or "").isdigit() else None,
             flag_from_mmsi(mmsi), now, now, length),
        )


def purge(settings: Optional[Settings] = None) -> int:
    cfg = settings or get_settings()
    cutoff = _iso(_now() - datetime.timedelta(days=cfg.ais_retention_days))
    with db(cfg) as conn:
        dropped = 0
        if is_postgres(cfg) and timescale_enabled(conn):
            # Whole daily chunks first (instant, no table bloat), then the partial chunk.
            dropped = len(conn.execute("SELECT drop_chunks('ais_positions', older_than => ?::timestamptz)", (cutoff,)).fetchall())
        deleted = conn.execute("DELETE FROM ais_positions WHERE ts < ?", (cutoff,)).rowcount
        if dropped:
            logger.info("AIS retention: %s daily chunk(s) dropped", dropped)
        conn.execute("DELETE FROM ais_vessels WHERE last_seen < ?", (cutoff,))
    from app.sources import feed_log

    feed_log.purge(cfg)
    return deleted


def vessels(since_hours: float = 24, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    cutoff = _iso(_now() - datetime.timedelta(hours=since_hours))
    with read_db(settings) as conn:
        return rows(conn.execute("SELECT * FROM ais_vessels WHERE last_seen >= ? ORDER BY last_seen DESC", (cutoff,)).fetchall())


def tracks(since_hours: float = 6, mmsi: Optional[str] = None, settings: Optional[Settings] = None,
           until: Optional[datetime.datetime] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Fixes of the ``since_hours`` before ``until`` (now by default), per vessel, oldest first."""
    end = until or _now()
    cutoff = _iso(end - datetime.timedelta(hours=since_hours))
    query = "SELECT * FROM ais_positions WHERE ts >= ? AND ts <= ?"
    params: Tuple[Any, ...] = (cutoff, _iso(end))
    if mmsi:
        query += " AND mmsi = ?"
        params += (mmsi,)
    with read_db(settings) as conn:
        result: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows(conn.execute(query + " ORDER BY mmsi, ts", params).fetchall()):
            result.setdefault(r["mmsi"], []).append(r)
    return result


def picture_at(at: Optional[datetime.datetime], hours: float, settings: Optional[Settings] = None,
               zone_of: Any = None) -> List[Dict[str, Any]]:
    """Vessels as they were known at ``at`` (now when None): last fix in ``(at - hours, at]``.

    Same row shape as :func:`vessels` (``last_lat``, ``last_seen``, ``last_source``…)
    so that live and replay share one code path. Nothing after ``at`` is used:
    a replay shows what the platform could know at that instant.
    """
    end = at or _now()
    low, high = _iso(end - datetime.timedelta(hours=hours)), _iso(end)
    with read_db(settings) as conn:
        fixes = rows(conn.execute(
            "SELECT p.mmsi, p.ts, p.lat, p.lon, p.sog, p.cog, p.source FROM ais_positions p "
            "JOIN (SELECT mmsi, MAX(ts) AS ts FROM ais_positions WHERE ts > ? AND ts <= ? GROUP BY mmsi) l "
            "ON l.mmsi = p.mmsi AND l.ts = p.ts",
            (low, high),
        ).fetchall())
        statics = {r["mmsi"]: r for r in rows(conn.execute(
            "SELECT * FROM ais_vessels WHERE mmsi IN (SELECT DISTINCT mmsi FROM ais_positions WHERE ts > ? AND ts <= ?)",
            (low, high),
        ).fetchall())}
    result = []
    for fix in sorted(fixes, key=lambda f: f["ts"], reverse=True):
        vessel = dict(statics.get(fix["mmsi"]) or {"mmsi": fix["mmsi"], "flag": flag_from_mmsi(fix["mmsi"])})
        vessel.update({
            "last_lat": fix["lat"], "last_lon": fix["lon"], "last_sog": fix["sog"], "last_cog": fix["cog"],
            "last_seen": fix["ts"], "last_source": fix["source"],
        })
        if zone_of is not None:
            vessel["last_zone"] = zone_of(fix["lon"], fix["lat"])
        result.append(vessel)
    return result


def dead_reckon(lat: float, lon: float, sog_knots: Optional[float], cog_deg: Optional[float], hours: float) -> Tuple[float, float]:
    """Project a position along its course (flat-earth approximation, fine for a few hours)."""
    if not sog_knots or cog_deg is None or cog_deg >= 360:
        return lat, lon
    distance_km = float(sog_knots) * 1.852 * hours
    rad = math.radians(float(cog_deg))
    dlat = distance_km * math.cos(rad) / 110.57
    dlon = distance_km * math.sin(rad) / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


def positions_at(scene_time: Any, window_h: float, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Per vessel, the fix nearest to ``scene_time`` within ±window, dead-reckoned to that instant.

    Returned dicts have the shape expected by ``correlate_with_ais`` (time_utc =
    scene time, so no extra time-based radius is added after the projection).
    """
    instant = parse_ts(scene_time)
    if instant is None:
        return []
    low = _iso(instant - datetime.timedelta(hours=window_h))
    high = _iso(instant + datetime.timedelta(hours=window_h))
    with read_db(settings) as conn:
        fixes = rows(
            conn.execute(
                "SELECT p.*, v.name, v.ship_type, v.flag, v.length_m FROM ais_positions p LEFT JOIN ais_vessels v ON v.mmsi = p.mmsi "
                "WHERE p.ts BETWEEN ? AND ?",
                (low, high),
            ).fetchall()
        )
    best: Dict[str, Tuple[float, Dict[str, Any]]] = {}
    for fix in fixes:
        ts = parse_ts(fix["ts"])
        if ts is None:
            continue
        delta_h = (instant - ts).total_seconds() / 3600.0
        if fix["mmsi"] not in best or abs(delta_h) < abs(best[fix["mmsi"]][0]):
            best[fix["mmsi"]] = (delta_h, fix)
    result = []
    for mmsi, (delta_h, fix) in best.items():
        lat, lon = dead_reckon(fix["lat"], fix["lon"], fix["sog"], fix["cog"], delta_h)
        result.append(
            {
                "mmsi": mmsi, "name": fix.get("name") or "AIS", "lat": lat, "lon": lon,
                "sog_knots": fix["sog"], "cog_deg": fix["cog"], "time_utc": _iso(instant),
                "fix_age_h": round(abs(delta_h), 2), "flag": fix.get("flag"), "ship_type": fix.get("ship_type"),
                "length_m": fix.get("length_m"),
                "source": f"{SOURCE_LABELS.get(fix.get('source') or 'aisstream', fix.get('source'))} (historique)",
                "feed": fix.get("source") or "aisstream",
            }
        )
    return result


def rejected() -> Dict[str, Dict[str, int]]:
    """Fixes refused since start, per feed and reason (bad clocks, glitches, spoofed jumps)."""
    with _lock:
        return {feed: dict(reasons) for feed, reasons in REJECTED.items()}


def stats(settings: Optional[Settings] = None) -> Dict[str, Any]:
    hour_ago = _iso(_now() - datetime.timedelta(hours=1))
    with read_db(settings) as conn:
        positions_1h = conn.execute("SELECT COUNT(*) FROM ais_positions WHERE ts >= ?", (hour_ago,)).fetchone()[0]
        vessels_1h = conn.execute("SELECT COUNT(*) FROM ais_vessels WHERE last_seen >= ?", (hour_ago,)).fetchone()[0]
        total = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM ais_positions").fetchone()
        day_ago = _iso(_now() - datetime.timedelta(hours=24))
        by_source = {
            r[0]: {"positions_24h": r[1], "vessels_24h": r[2], "latest": r[3]}
            for r in conn.execute(
                "SELECT source, COUNT(*), COUNT(DISTINCT mmsi), MAX(ts) FROM ais_positions WHERE ts >= ? GROUP BY source", (day_ago,)
            ).fetchall()
        }
    return {"positions_1h": positions_1h, "vessels_1h": vessels_1h, "positions_total": total[0],
            "oldest": total[1], "latest": total[2], "by_source": by_source, "rejected": rejected()}
