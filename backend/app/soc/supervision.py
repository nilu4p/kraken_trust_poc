"""Technical supervision board: is the platform itself healthy, fast and complete?

One snapshot gathering what an operator of the platform (not of the sea) needs:
external sources (last success / failure, consecutive errors), AIS collection
coverage and rejected fixes, radar scans (duration, outcome, coverage), the
24/7 rules tick (age, duration), API response times per area, database and
disk usage, caches. Everything is read locally — no external call — so the
board stays available when the sources are not.
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.db import is_postgres, read_db, timescale_enabled

# --------------------------------------------------------------------------- API response times

_lock = threading.Lock()
_timings: Dict[str, Deque[Tuple[float, int]]] = defaultdict(lambda: deque(maxlen=500))
STARTED_AT = datetime.datetime.now(datetime.timezone.utc)


def area_of(path: str, prefix: str) -> Optional[str]:
    """``/api/v1/maritime/vessel/123`` → ``maritime``; tiles and evidence files are grouped."""
    if not path.startswith(prefix + "/"):
        return None
    head = path[len(prefix) + 1:].split("/", 1)[0]
    return head or None


def record_timing(area: str, seconds: float, status: int) -> None:
    with _lock:
        _timings[area].append((seconds, status))


def api_timings() -> List[Dict[str, Any]]:
    with _lock:
        snapshot = {area: list(values) for area, values in _timings.items()}
    result = []
    for area, values in snapshot.items():
        durations = sorted(v[0] for v in values)
        if not durations:
            continue
        pick = lambda q: durations[min(len(durations) - 1, int(q * len(durations)))]  # noqa: E731
        result.append({
            "area": area, "requests": len(values), "p50_ms": round(pick(0.5) * 1000), "p95_ms": round(pick(0.95) * 1000),
            "max_ms": round(durations[-1] * 1000), "errors": sum(1 for v in values if v[1] >= 500),
        })
    return sorted(result, key=lambda r: r["p95_ms"], reverse=True)


# --------------------------------------------------------------------------- storage


def _dir_size(path: Path, limit_files: int = 200_000) -> Optional[int]:
    total, count = 0, 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                count += 1
                if count > limit_files:
                    return None
                try:
                    total += os.path.getsize(os.path.join(root, name))
                except OSError:
                    pass
    except OSError:
        return None
    return total


def _database(cfg: Settings) -> Dict[str, Any]:
    info: Dict[str, Any] = {"engine": "postgresql" if is_postgres(cfg) else "sqlite"}
    tables = ("ais_positions", "ais_vessels", "incidents", "incident_events", "incident_observations", "feed_minutes",
              "audit_log", "watchlist", "custom_rules", "surveillance_zones", "timeline_cache")
    with read_db(cfg) as conn:
        counts = {}
        for table in tables:
            try:
                counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            except Exception:
                counts[table] = None
        info["rows"] = counts
        if info["engine"] == "postgresql":
            try:
                info["size_bytes"] = conn.execute("SELECT pg_database_size(current_database())").fetchone()[0]
                info["timescaledb"] = timescale_enabled(conn)
            except Exception:
                info["size_bytes"] = None
    if info["engine"] == "sqlite":
        path = Path(cfg.data_dir) / "kraken.db"
        info["size_bytes"] = sum(p.stat().st_size for p in Path(cfg.data_dir).glob("kraken.db*")) if path.exists() else None
    return info


def _disk(cfg: Settings) -> Dict[str, Any]:
    folders = {"preuves": cfg.preuves_dir, "cache": cfg.cache_dir, "backups": cfg.backup_dir, "data": cfg.data_dir}
    result: Dict[str, Any] = {name: _dir_size(Path(path)) for name, path in folders.items()}
    try:
        usage = shutil.disk_usage(cfg.data_dir)
        result["free_bytes"], result["total_bytes"] = usage.free, usage.total
    except OSError:
        result["free_bytes"] = result["total_bytes"] = None
    return result


# --------------------------------------------------------------------------- snapshot


def _rule_state(key: str, cfg: Settings) -> Tuple[Any, Optional[str]]:
    with read_db(cfg) as conn:
        row = conn.execute("SELECT value, updated_at FROM rule_state WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None, None
    try:
        return json.loads(row[0]), row[1]
    except ValueError:
        return None, row[1]


def _age_s(stamp: Optional[str], now: datetime.datetime) -> Optional[float]:
    from app.core.timeframe import parse_instant

    moment = parse_instant(stamp)
    return round((now - moment).total_seconds()) if moment else None


def _ai(cfg: Settings) -> Dict[str, Any]:
    """AI opinions (information only): configuration, xView3 service state, agreement with the operators."""
    from app.pipeline import ai_detector
    from app.pipeline.aggregation import get_all_kraken_geojson
    from app.sources import xview3

    try:
        features = [f for f in get_all_kraken_geojson(settings=cfg).get("features", [])
                    if (f.get("properties") or {}).get("type") == "dark_vessel"]
    except Exception:
        features = []
    return {
        "yolo": {"mode": cfg.ai_detector_mode, "evaluation": ai_detector.evaluation(features)},
        # Local service on the internal network (4 s max), not an external source.
        "xview3": {**xview3.status(cfg), "evaluation": ai_detector.evaluation(features, xview3.objectness_of)},
    }


def snapshot(settings: Optional[Settings] = None) -> Dict[str, Any]:
    from app.core import shared
    from app.sources import ais_store, feed_log, health
    from app.sources.aisstream import collector_status

    cfg = settings or get_settings()
    now = datetime.datetime.now(datetime.timezone.utc)
    t0 = time.monotonic()

    sources = []
    for name, entry in sorted(health.snapshot().items()):
        sources.append({"source": name, **entry, "success_age_s": _age_s(entry.get("last_success"), now),
                        "failure_age_s": _age_s(entry.get("last_failure"), now)})

    collection = {
        "coverage_30min": feed_log.coverage(now - datetime.timedelta(minutes=30), now, settings=cfg),
        "coverage_24h": feed_log.coverage(now - datetime.timedelta(hours=24), now, settings=cfg),
        "per_feed_24h": {f: feed_log.coverage(now - datetime.timedelta(hours=24), now, feeds=(f,), settings=cfg)
                         for f in feed_log.AIS_FEEDS},
        "gaps_24h": feed_log.gaps(now - datetime.timedelta(hours=24), now, min_minutes=10, settings=cfg),
        "state": shared.get_json("ais_collection_state", {}) or {},
        "stream": collector_status(),
        "rejected": ais_store.rejected(),
        "positions_1h": ais_store.stats(cfg)["positions_1h"],
    }

    runs, _ = _rule_state("scan_runs", cfg)
    runs = runs or []
    durations = [r["duration_s"] for r in runs if r.get("duration_s") is not None and r.get("status") == "done"]
    scans = {
        "runs": runs[:10],
        "last_success": next((r["finished_at"] for r in runs if r.get("status") == "done"), None),
        "average_duration_s": round(sum(durations) / len(durations), 1) if durations else None,
        "errors_recent": sum(1 for r in runs[:10] if r.get("status") == "error"),
    }

    last_rules, rules_at = _rule_state("last_run", cfg)
    rules = {"last_run": rules_at, "age_s": _age_s(rules_at, now), "duration_ms": (last_rules or {}).get("duration_ms"),
             "result": last_rules, "enabled": cfg.rules_enabled,
             # The 24/7 loop ticks every minute: older than 3 min = worker / beat / loop stalled.
             "stalled": cfg.rules_enabled and (_age_s(rules_at, now) or 10 ** 9) > 180}

    return {
        "ai": _ai(cfg),
        "generated_at": now.isoformat(),
        "process_started_at": STARTED_AT.isoformat(),
        "background_mode": cfg.background_mode,
        "environment": cfg.environment,
        "sources": sources,
        "collection": collection,
        "scans": scans,
        "rules": rules,
        "api": api_timings(),
        "database": _database(cfg),
        "disk": _disk(cfg),
        "retention_days": cfg.ais_retention_days,
        "built_in_ms": round((time.monotonic() - t0) * 1000),
    }
