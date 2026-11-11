"""Consolidate per-alert GeoJSON files into a single FeatureCollection."""

from __future__ import annotations

import datetime
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from app.config import Settings, get_settings
from app.core.c2pa import validate_proof
from app.soc.incidents import detection_index
from app.soc.watchlist import Matcher, summary

logger = logging.getLogger(__name__)


LEGACY_IMAGE_NAMES = ("radar_crop.jpg", "preuve_scellee_c2pa.jpg")


def _proof_image_url(dossier: Path) -> str:
    for name in LEGACY_IMAGE_NAMES:
        if (dossier / name).is_file():
            return f"/api/v1/evidence/files/{dossier.name}/{name}"
    return ""


_proof_cache: Dict[str, Tuple[Tuple[Any, ...], str]] = {}
# Parsed kraken_alerts.geojson per dossier, reused while the file is unchanged (mtime + size).
_geojson_cache: Dict[str, Tuple[Tuple[int, int], Dict[str, Any]]] = {}


def _read_geojson(path: Path) -> Dict[str, Any]:
    stat = path.stat()
    key = (stat.st_mtime_ns, stat.st_size)
    hit = _geojson_cache.get(str(path))
    if hit and hit[0] == key:
        return hit[1]
    data = json.loads(path.read_text(encoding="utf-8"))
    _geojson_cache[str(path)] = (key, data)
    return data


def cached_proof_status(dossier: Path, settings: Optional[Settings] = None) -> str:
    """Proof status, recomputed only when a file of the dossier or the KRAKEN trust anchors change."""
    cfg = settings or get_settings()
    roots = Path(cfg.keys_dir) / "rootCA_cert.pem"
    anchor = (roots.stat().st_mtime_ns if roots.is_file() else 0, str(cfg.keys_dir))
    signature = (anchor, tuple(sorted((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in dossier.iterdir() if p.is_file())))
    hit = _proof_cache.get(str(dossier))
    if hit and hit[0] == signature:
        return hit[1]
    status = validate_proof(dossier, cfg)
    _proof_cache[str(dossier)] = (signature, status)
    return status


def _too_old(props: Dict[str, Any], cutoff: Optional[datetime.datetime]) -> bool:
    """Older than the display window, unless its incident is still open."""
    if cutoff is None or (props.get("incident_status") and props["incident_status"] != "closed"):
        return False
    from app.pipeline.detection import _parse_time

    seen = _parse_time(props.get("scene_datetime") or props.get("timestamp"))
    return seen is not None and seen < cutoff


def sealed_at(dossier: Path) -> Optional[datetime.datetime]:
    """When a proof dossier was sealed (``alerte_YYYYmmdd_HHMMSS_id``): from then on the platform knew it."""
    try:
        return datetime.datetime.strptime("_".join(dossier.name.split("_")[1:3]), "%Y%m%d_%H%M%S").replace(
            tzinfo=datetime.timezone.utc)
    except ValueError:
        return None


def get_all_kraken_geojson(settings: Optional[Settings] = None, max_age_days: Optional[int] = None,
                           at: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Merge ``preuves/alerte_*/kraken_alerts.geojson`` in chronological order, as known at ``at`` (now when None).

    Dark vessels: one per (scene, position) — a re-scan of the same scene does
    not duplicate a target (the first, incident-linked analysis is kept) — and
    older than ``max_age_days`` (before the reference instant) only while their
    incident is open. In a replay, only dossiers sealed by ``at`` are used and
    incidents carry their status at ``at``. AIS tracks are de-duplicated by MMSI
    (latest position wins), footprints by scene id.
    """
    cfg = settings or get_settings()
    reference = at or datetime.datetime.now(datetime.timezone.utc)
    cutoff = reference - datetime.timedelta(days=max_age_days) if max_age_days else None
    seen_targets: set = set()
    master: Dict[str, Any] = {"type": "FeatureCollection", "features": []}
    search_root = Path(cfg.preuves_dir)
    if not search_root.is_dir():
        return master

    index = detection_index(cfg, at)
    # Alerts whose incident is still open (at the reference instant): always shown, whatever their age.
    open_alerts = {alert for (alert, _target), info in index.items()
                   if info.get("incident_status") and info["incident_status"] != "closed"}
    matcher = Matcher(cfg)
    ais_by_mmsi: Dict[str, Dict[str, Any]] = {}
    scenes: Dict[str, Dict[str, Any]] = {}
    geojson_files = sorted(search_root.glob("alerte_*/kraken_alerts.geojson"), key=lambda p: p.stat().st_mtime)
    for file_path in geojson_files:
        sealed = sealed_at(file_path.parent)
        if at is not None and (sealed or reference) > at:
            continue  # analysed after the reference instant: not known yet
        if cutoff is not None and sealed is not None and sealed < cutoff \
                and file_path.parent.name.rsplit("_", 1)[-1] not in open_alerts:
            continue  # sealed before the window (so acquired before it too) and no open incident: never read
        try:
            data = _read_geojson(file_path)
            features = data.get("features") or []
            c2pa_status = cached_proof_status(file_path.parent, cfg)
            image_url = _proof_image_url(file_path.parent)
            for cached_feature in features:
                # Copy before annotating: the parsed file is cached and must stay as sealed
                # (a replay's incident status must never leak into the next live response).
                feature = {**cached_feature, "properties": dict(cached_feature.get("properties") or {})}
                props = feature["properties"]
                if props.get("type") == "ais_vessel":
                    props["watchlist"] = summary(matcher.match(mmsi=props.get("mmsi"), name=props.get("name")))
                    key = str(props.get("mmsi") or feature.get("geometry", {}).get("coordinates"))
                    ais_by_mmsi[key] = feature
                    continue
                if props.get("type") == "s1_scene":
                    scenes[str(props.get("scene_id"))] = feature
                    continue
                props["c2pa_status"] = c2pa_status
                props.update(index.get((str(props.get("alert_id")), str(props.get("vessel_id"))), {}))
                coords = (feature.get("geometry") or {}).get("coordinates") or [0, 0]
                target_key = (props.get("stac_source"), round(float(coords[0]), 3), round(float(coords[1]), 3))
                if target_key in seen_targets or _too_old(props, cutoff):
                    continue
                seen_targets.add(target_key)
                # Image URLs point to the authenticated evidence API, never to a public static mount.
                for key in ("image_url", "sheet_url"):
                    if str(props.get(key, "")).startswith("/preuves/"):
                        props[key] = "/api/v1/evidence/files/" + props[key][len("/preuves/"):]
                if image_url:
                    props.setdefault("image_url", image_url)
                master["features"].append(feature)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", file_path, exc)
    master["features"].extend(ais_by_mmsi.values())
    master["features"].extend(scenes.values())
    return master
