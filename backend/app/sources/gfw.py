"""Global Fishing Watch Events API v3 — free token (https://globalfishingwatch.org/our-apis/).

Pulls the behavioural events that matter for IUU monitoring inside the EEZ:
AIS gaps (transponder switched off), fishing, encounters (possible
transshipment), loitering and port visits (landings in the ports of the
territory: where a vessel fishing in the EEZ unloads). Without ``GFW_TOKEN`` every call returns an
empty, ``available=False`` result.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.sources.mid import flag_name
from app.sources.http import cache_read, cache_write, post_json

logger = logging.getLogger(__name__)

EVENTS_URL = "https://gateway.api.globalfishingwatch.org/v3/events"
EEZ_REGION_DATASET = "public-eez-areas"

DATASETS: Dict[str, str] = {
    "gap": "public-global-gaps-events:latest",
    "fishing": "public-global-fishing-events:latest",
    "encounter": "public-global-encounters-events:latest",
    "loitering": "public-global-loitering-events:latest",
    "port_visit": "public-global-port-visits-events:latest",
}
# GFW port visit confidence: 2 = port entry or exit only, 3-4 = the vessel stopped in port.
MIN_PORT_CONFIDENCE = 3

PAGE_SIZE = 200
MAX_EVENTS_PER_TYPE = 600


def _position(entry: Dict[str, Any]) -> Optional[Dict[str, float]]:
    pos = entry.get("position") or {}
    lat, lon = pos.get("lat"), pos.get("lon")
    if lat is None or lon is None:
        gap = entry.get("gap") or {}
        off = gap.get("offPosition") or {}
        lat, lon = off.get("lat"), off.get("lon")
    if lat is None or lon is None:
        return None
    return {"lat": float(lat), "lon": float(lon)}


def parse_event(entry: Dict[str, Any], kind: str) -> Optional[Dict[str, Any]]:
    """Normalise one GFW event into a GeoJSON Feature (None if no position)."""
    position = _position(entry)
    if position is None:
        return None
    visit = entry.get("port_visit") or entry.get("portVisit") or {}
    if kind == "port_visit" and int(visit.get("confidence") or MIN_PORT_CONFIDENCE) < MIN_PORT_CONFIDENCE:
        return None
    vessel = entry.get("vessel") or {}
    gap = entry.get("gap") or {}
    props: Dict[str, Any] = {
        "type": "gfw_event",
        "event_type": kind,
        "event_id": entry.get("id"),
        "start": entry.get("start"),
        "end": entry.get("end"),
        "vessel_name": vessel.get("name"),
        "mmsi": vessel.get("ssvid"),
        "flag": flag_name(vessel.get("flag")),
        "flag_code": vessel.get("flag"),
        "vessel_type": vessel.get("type"),
        "source": "globalfishingwatch.org",
    }
    if kind == "gap":
        props.update(
            {
                "gap_hours": gap.get("durationHours"),
                "gap_distance_km": gap.get("distanceKm"),
                "intentional_disabling": gap.get("intentionalDisabling"),
            }
        )
    if kind == "port_visit":
        anchorage = visit.get("startAnchorage") or visit.get("intermediateAnchorage") or {}
        props.update(
            {
                "port_name": anchorage.get("topDestination") or anchorage.get("name"),
                "anchorage": anchorage.get("name"),
                "port_flag": flag_name(anchorage.get("flag")),
                "port_hours": visit.get("durationHrs"),
            }
        )
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [position["lon"], position["lat"]]},
        "properties": props,
    }


def port_visits(start: datetime.date, end: datetime.date, settings: Optional[Settings] = None) -> Optional[List[Dict[str, Any]]]:
    """Port visits (vessel stopped in port) in the EEZ between two days; None when GFW is unavailable."""
    cfg = settings or get_settings()
    if not cfg.gfw_token:
        return None
    key = f"ports:{cfg.eez_mrgid}:{start}:{end}"
    cached = cache_read("gfw_events", key, 12 * 3600, cfg)
    if cached is not None:
        return cached
    features = _fetch_type("port_visit", start.isoformat(), (end + datetime.timedelta(days=1)).isoformat(), cfg)
    if features is not None:
        cache_write("gfw_events", key, features, cfg)
    return features


def _fetch_type(kind: str, start: str, end_day: str, cfg: Settings) -> Optional[List[Dict[str, Any]]]:
    features: List[Dict[str, Any]] = []
    offset = 0
    while offset < MAX_EVENTS_PER_TYPE:
        data = post_json(
            EVENTS_URL,
            {
                "datasets": [DATASETS[kind]],
                "startDate": start,
                "endDate": end_day,
                "region": {"dataset": EEZ_REGION_DATASET, "id": cfg.eez_mrgid},
            },
            params={"limit": PAGE_SIZE, "offset": offset},
            headers={"Authorization": f"Bearer {cfg.gfw_token}"},
            settings=cfg,
            source="global_fishing_watch",
        )
        if data is None:
            return None if offset == 0 else features
        for entry in data.get("entries") or []:
            feature = parse_event(entry, kind)
            if feature:
                features.append(feature)
        next_offset = data.get("nextOffset")
        if not next_offset or next_offset <= offset:
            break
        offset = int(next_offset)
    return features


def fetch_events(settings: Optional[Settings] = None, end: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """GFW events inside the EEZ over the ``gfw_lookback_days`` before ``end`` (now when None).

    Events starting after ``end`` are dropped: a replay, or the risk of a past
    radar pass, only uses what had happened by then.
    """
    cfg = settings or get_settings()
    empty: Dict[str, Any] = {"type": "FeatureCollection", "features": [], "available": False, "counts": {}}
    if not cfg.gfw_token:
        empty["reason"] = "GFW_TOKEN non configuré"
        return empty

    limit = end
    today = (end.date() if end else datetime.date.today())
    start = (today - datetime.timedelta(days=cfg.gfw_lookback_days)).isoformat()
    end_day = (today + datetime.timedelta(days=1)).isoformat()
    key = f"{cfg.eez_mrgid}:{start}:{end_day}"
    cached = cache_read("gfw_events", key, 3 * 3600, cfg)
    if cached:
        return _until(cached, limit)

    features: List[Dict[str, Any]] = []
    counts: Dict[str, int] = {}
    any_ok = False
    for kind in DATASETS:
        result = _fetch_type(kind, start, end_day, cfg)
        if result is None:
            continue
        any_ok = True
        counts[kind] = len(result)
        features.extend(result)

    if not any_ok:
        empty["reason"] = "API Global Fishing Watch injoignable ou token invalide"
        return empty

    payload = {
        "type": "FeatureCollection",
        "features": features,
        "available": True,
        "counts": counts,
        "period": {"start": start, "end": end_day},
        "source": "globalfishingwatch.org",
    }
    cache_write("gfw_events", key, payload, cfg)
    logger.info("GFW events loaded: %s", counts)
    return _until(payload, limit)


def _until(payload: Dict[str, Any], limit: Optional[datetime.datetime]) -> Dict[str, Any]:
    """Keep only the events started at or before ``limit`` (the cache covers whole days)."""
    if limit is None:
        return payload
    from app.core.timeframe import parse_instant

    kept = [f for f in payload.get("features") or []
            if (parse_instant(f["properties"].get("start")) or limit) <= limit]
    counts: Dict[str, int] = {}
    for f in kept:
        counts[f["properties"]["event_type"]] = counts.get(f["properties"]["event_type"], 0) + 1
    return {**payload, "features": kept, "counts": counts}
