"""Multi-sensor detections store and automatic watch (optical, thermal, night lights).

Detections from Sentinel-2, Landsat 8/9 and VIIRS are stored once per
(sensor, scene, position) in ``sensor_detections`` and feed the multi-sensor
corroboration of radar targets. ``sensor_scenes`` remembers what was already
analysed, so the hourly watch only reads new acquisitions. Each processed scene is audited
(``sensor.scan``).
"""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, Iterable, List, Optional

from app.config import Settings, get_settings
from app.core import audit
from app.db import db, read_db, rows

logger = logging.getLogger(__name__)

SENSOR_LABELS = {
    "s2_msi": "Sentinel-2 (optique 10 m)",
    "landsat_oli": "Landsat 8/9 (optique 30 m + thermique)",
    "viirs_dnb": "VIIRS (lumières nocturnes)",
}
MAX_SCENES_PER_TICK = 2
NIGHTS_BACK = 2


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def save(detections: Iterable[Dict[str, Any]], settings: Optional[Settings] = None) -> int:
    """Insert new detections (idempotent); returns how many were new."""
    stored = 0
    now = _now().isoformat()
    with db(settings) as conn:
        for d in detections:
            key = f"{d['sensor']}:{d['scene_id']}:{round(d['lat'], 4)}:{round(d['lon'], 4)}"[:200]
            inserted = conn.execute(
                "INSERT INTO sensor_detections (key, sensor, platform, scene_id, acquired_at, time_uncertainty_min, lat, lon, "
                "length_m, confidence, attributes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (key, d["sensor"], d.get("platform") or "", d["scene_id"], d["acquired_at"], int(d.get("time_uncertainty_min") or 0),
                 d["lat"], d["lon"], d.get("length_m"), d["confidence"],
                 json.dumps(d.get("attributes") or {}, ensure_ascii=False), now),
            ).rowcount
            stored += max(0, inserted or 0)
    return stored


def processed(sensor: str, scene_id: str, settings: Optional[Settings] = None) -> bool:
    with read_db(settings) as conn:
        return conn.execute("SELECT 1 FROM sensor_scenes WHERE sensor = ? AND scene_id = ?", (sensor, scene_id)).fetchone() is not None


def mark(sensor: str, scene_id: str, outcome: str, count: int, settings: Optional[Settings] = None, actor: str = "system") -> None:
    with db(settings) as conn:
        conn.execute(
            "INSERT INTO sensor_scenes (sensor, scene_id, processed_at, outcome, detections) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(sensor, scene_id) DO UPDATE SET processed_at = excluded.processed_at, outcome = excluded.outcome, "
            "detections = excluded.detections",
            (sensor, scene_id, _now().isoformat(), outcome, count),
        )
    audit.record("sensor.scan", actor=actor, target=f"{sensor}:{scene_id}", details={"outcome": outcome, "detections": count},
                 settings=settings)


def list_detections(start: datetime.datetime, end: datetime.datetime, sensors: Optional[List[str]] = None,
                    min_confidence: float = 0.0, with_chips: bool = False, settings: Optional[Settings] = None,
                    ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    clauses, params = ["acquired_at >= ?", "acquired_at <= ?", "confidence >= ?"], [start.isoformat(), end.isoformat(), min_confidence]
    if ids is not None:
        clauses.append(f"id IN ({','.join('?' * len(ids)) or 'NULL'})")
        params.extend(ids)
    if sensors:
        clauses.append(f"sensor IN ({','.join('?' * len(sensors))})")
        params.extend(sensors)
    with read_db(settings) as conn:
        result = rows(conn.execute(
            f"SELECT * FROM sensor_detections WHERE {' AND '.join(clauses)} ORDER BY acquired_at DESC LIMIT 5000", params).fetchall())
    for r in result:
        r["attributes"] = json.loads(r["attributes"] or "{}")
        if not with_chips:
            r["attributes"].pop("chip", None)
        r["sensor_label"] = SENSOR_LABELS.get(r["sensor"], r["sensor"])
    return result


# --------------------------------------------------------------------------- automatic watch


def watch_tick(settings: Optional[Settings] = None, now: Optional[datetime.datetime] = None) -> Dict[str, int]:
    """New VIIRS nights and new optical scenes over the EEZ (bounded work per call)."""
    cfg = settings or get_settings()
    moment = now or _now()
    summary = {"nights": 0, "optical_scenes": 0, "detections": 0}
    if not cfg.sensor_watch_enabled:
        return summary
    from app.sources import viirs_night

    for back in range(NIGHTS_BACK, -1, -1):
        day = (moment - datetime.timedelta(days=back)).date()
        for satellite in viirs_night.SATELLITES:
            scene_id = f"{satellite}:{day.isoformat()}"
            if processed("viirs_dnb", scene_id, cfg):
                continue
            pass_time = datetime.datetime.combine(day, datetime.time(*viirs_night.SATELLITES[satellite]["pass_utc"]),
                                                  tzinfo=datetime.timezone.utc)
            if moment < pass_time + datetime.timedelta(hours=6):
                continue  # GIBS publishes a few hours after the pass
            try:
                result = viirs_night.scan_night(satellite, day, cfg)
            except Exception as exc:
                logger.warning("VIIRS night %s failed: %s", scene_id, exc)
                continue
            if not result["available"]:
                if back >= NIGHTS_BACK:
                    mark("viirs_dnb", scene_id, "unavailable", 0, cfg)
                continue
            summary["detections"] += save(result["detections"], cfg)
            mark("viirs_dnb", scene_id, "ok", len(result["detections"]), cfg)
            summary["nights"] += 1
    from app.sources import multispectral

    budget = MAX_SCENES_PER_TICK
    for sensor, days in (("s2_msi", 3), ("landsat_oli", 8)):
        try:
            items = multispectral.search(sensor, cfg.eez_bbox, moment - datetime.timedelta(days=days), moment,
                                         cfg.optical_max_cloud, cfg, limit=30)
        except Exception as exc:
            logger.warning("Optical catalogue %s unavailable: %s", sensor, exc)
            continue
        for item in items:
            if budget <= 0:
                return summary
            if processed(sensor, item["id"], cfg):
                continue
            budget -= 1
            try:
                found = multispectral.scan_scene(item, sensor, cfg)
            except Exception as exc:
                logger.warning("Optical scene %s failed: %s", item["id"], exc)
                mark(sensor, item["id"], f"erreur : {str(exc)[:120]}", 0, cfg)
                continue
            summary["detections"] += save(found, cfg)
            mark(sensor, item["id"], "ok", len(found), cfg)
            summary["optical_scenes"] += 1
    return summary
