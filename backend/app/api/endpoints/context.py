"""Open-data context: sea state, GFW events, Sentinel-1 coverage, optical imagery, point dossier."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import viewer
from app.core import timeframe
from app.core.geo import haversine_km
from app.pipeline.ingestion import search_recent_scenes
from app.soc import incidents
from app.sources.gfw import fetch_events
from app.sources.marine_regions import classify_zone, zone_geometries
from app.sources.open_meteo import DEFAULT_POINT, sea_state, sea_state_at
from app.sources.optical import search_optical
from app.soc.watchlist import Matcher, summary
from app.sources.erddap import ocean_context
from app.sources.shom import distances_km, fishing_limits

router = APIRouter(tags=["context"])


@router.get("/environment")
def environment(
    lat: float = Query(DEFAULT_POINT[0], ge=-90, le=90),
    lon: float = Query(DEFAULT_POINT[1], ge=-180, le=180),
    at: Optional[str] = timeframe.AT_QUERY,
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Wind, waves, currents and SST + SAR detection reliability at the reference instant (Open-Meteo)."""
    when = timeframe.resolve(at)
    state = sea_state(lat, lon) if when is None else sea_state_at(lat, lon, timeframe.iso(when))
    return {**state, "reference": timeframe.describe(when)}


@router.get("/events")
def gfw_events(at: Optional[str] = timeframe.AT_QUERY, _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Global Fishing Watch events in the EEZ started before the reference instant, flagged when on the watchlist."""
    when = timeframe.resolve(at)
    data = {**fetch_events(end=when), "time": timeframe.describe(when)}
    matcher = Matcher()
    for feature in data.get("features", []):
        props = feature.setdefault("properties", {})
        props["watchlist"] = summary(matcher.match(mmsi=props.get("mmsi"), name=props.get("vessel_name")))
    return data


@router.get("/fishing-limits")
def fishing_limit_lines(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Official SHOM / DPMA fishing limits for French Guiana (100 NM, 3 NM)."""
    return fishing_limits()


@router.get("/scenes")
def recent_scenes(limit: int = Query(20, ge=1, le=100), at: Optional[str] = timeframe.AT_QUERY,
                  _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Sentinel-1 GRD acquisitions over the EEZ up to the reference instant (footprints + metadata)."""
    when = timeframe.resolve(at)
    return {**search_recent_scenes(limit=limit, before=when), "time": timeframe.describe(when)}


@router.get("/imagery")
def optical_imagery(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    around: Optional[str] = Query(None, max_length=40, description="ISO date (default: now), e.g. the SAR pass"),
    days: int = Query(3, ge=0, le=30),
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Pléiades / Pléiades Neo / SPOT / WorldView (CCM) and Sentinel-2 acquisitions over a point."""
    return search_optical(lat, lon, around=around, days=days)


@router.get("/point")
def point_dossier(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    at: Optional[str] = timeframe.AT_QUERY,
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Right-click dossier for any point at the reference instant: legal zone, MPA, ocean, sea state,
    incidents (status at that instant) and GFW events before it.

    The external sources are queried in parallel: the dossier takes as long as the
    slowest source, not the sum of all of them.
    """
    when = timeframe.resolve(at)
    from concurrent.futures import ThreadPoolExecutor

    def protected_area() -> Any:
        try:
            from app.sources.wdpa import MpaIndex

            return MpaIndex().find(lat, lon)
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        f_zones = pool.submit(zone_geometries)
        f_events = pool.submit(lambda: fetch_events(end=when).get("features", []))
        f_mpa = pool.submit(protected_area)
        f_ocean = pool.submit(ocean_context, lat, lon, None, when)
        f_sea = pool.submit(lambda: sea_state(round(lat, 2), round(lon, 2)) if when is None
                            else sea_state_at(round(lat, 2), round(lon, 2), timeframe.iso(when)))
        f_limits = pool.submit(distances_km, lat, lon)
        zone = classify_zone(lon, lat, f_zones.result())
        events, mpa, ocean, sea, limits = f_events.result(), f_mpa.result(), f_ocean.result(), f_sea.result(), f_limits.result()
    nearby = []
    states = incidents.state_at(when) if when is not None else None
    for incident in incidents.list_incidents(limit=2000):
        if states is not None:
            if incident["id"] not in states:
                continue  # opened after the reference instant
            state = states[incident["id"]]
            incident = {**incident, "status": state["status"], "severity": state["severity"],
                        "status_label": incidents.STATUSES[state["status"]]}
        distance = haversine_km(lat, lon, incident["lat"], incident["lon"])
        if distance <= 25:
            nearby.append({k: incident[k] for k in ("id", "ref", "status", "status_label", "severity", "title")} | {"distance_km": round(distance, 1)})
    counts: Dict[str, int] = {}
    for feature in events:
        coords = feature["geometry"]["coordinates"]
        if haversine_km(lat, lon, coords[1], coords[0]) <= 25:
            kind = feature["properties"].get("event_type", "other")
            counts[kind] = counts.get(kind, 0) + 1
    return {
        "lat": lat,
        "lon": lon,
        "zone": zone,
        "mpa": mpa,
        "ocean": ocean,
        "fishing_limits_km": limits,
        "sea_state": sea,
        "incidents": sorted(nearby, key=lambda i: i["distance_km"]),
        "gfw_events_25km": counts,
        "time": timeframe.describe(when),
    }
