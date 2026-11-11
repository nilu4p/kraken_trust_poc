"""Continuous maritime picture: fused AIS / VMS, tracks, vessel profile, GFW SAR / effort, MPAs, ocean, coverage."""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.api.deps import supervisor, viewer
from app.config import get_settings
from app.core import audit, timeframe
from app.db import db, read_db, rows
from app.soc.watchlist import Matcher, summary
from app.sources import ais_store, cmems, gfw_4wings, health, vesselapi, vms, wdpa
from app.sources.aisstream import collector_status
from app.soc import authorized as authorized_svc
from app.soc import incidents as incidents_svc
from app.sources.mid import flag_name, is_french_flag

router = APIRouter(prefix="/maritime", tags=["maritime"])


def _flag(vessel: Dict[str, Any]) -> Any:
    return vessel.get("flag") or flag_name(vessel.get("gfw_flag"))


def _category(vessel: Dict[str, Any], listed: bool, licensed: bool = False) -> str:
    """Same logic as the 24/7 rules: a licensed fishing vessel is not a "foreign fisher"."""
    if listed:
        return "listed"
    flag = _flag(vessel)
    foreign = bool(flag) and not is_french_flag(flag)
    fishing = vessel.get("ship_type") == 30 or bool(vessel.get("gfw_geartype"))
    if foreign and fishing:
        return "licensed" if licensed else "foreign_fishing"
    if foreign:
        return "foreign"
    return "french" if flag else "unknown"


TRACK_STEP_S = 300  # map tracks: one point every 5 min is enough at operational zoom levels


def _thin(points: list) -> list:
    """[[lon, lat], ...] keeping one fix per TRACK_STEP_S (first and last always kept)."""
    kept, last = [], None
    for index, p in enumerate(points):
        try:
            ts = datetime.datetime.fromisoformat(str(p["ts"]).replace("Z", "+00:00"))
        except ValueError:
            ts = None
        final = index == len(points) - 1
        if final or last is None or ts is None or (ts - last).total_seconds() >= TRACK_STEP_S:
            kept.append([round(p["lon"], 5), round(p["lat"], 5)])
            last = ts or last
    return kept


def _licence(vessel: Dict[str, Any], matcher: Any, vms: set) -> Any:
    """Valid licence (authorised list) or VMS reporting, else None."""
    entry = matcher.match(mmsi=vessel.get("mmsi"), imo=vessel.get("imo"), call_sign=vessel.get("call_sign"))
    if entry:
        return authorized_svc.licence_summary(entry)
    return {"licence": "VMS", "valid_until": None, "name": None, "flag": None} if vessel.get("mmsi") in vms else None


@router.get("/ais")
def ais_picture(
    hours: float = Query(2, ge=0.25, le=72),
    track_hours: float = Query(6, ge=0, le=72),
    at: Optional[str] = timeframe.AT_QUERY,
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Vessels heard in the ``hours`` before the reference instant (last position) with their track and category.

    Live and replay share one code path: only fixes up to the instant are used.
    """
    when = timeframe.resolve(at)
    matcher = Matcher()
    authorized = authorized_svc.AuthorizedMatcher()
    vms_licensed = vms.licensed_mmsis()
    history = ais_store.tracks(since_hours=track_hours, until=when) if track_hours else {}
    zone_of = None
    if when is not None:
        from app.sources.marine_regions import classify_zone, zone_geometries

        zones = zone_geometries()
        zone_of = lambda lon, lat: classify_zone(lon, lat, zones)  # noqa: E731
    features = []
    for v in ais_store.picture_at(when, hours, zone_of=zone_of):
        if v.get("last_lat") is None:
            continue
        hit = summary(matcher.match(mmsi=v["mmsi"], imo=v.get("imo"), name=v.get("name"), call_sign=v.get("call_sign")))
        track = _thin(history.get(v["mmsi"], []))
        licence = _licence(v, authorized, vms_licensed)
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [v["last_lon"], v["last_lat"]]},
                "properties": {
                    "type": "ais_live",
                    "mmsi": v["mmsi"],
                    "name": v.get("name") or "",
                    "flag": _flag(v),
                    "ship_type": v.get("ship_type"),
                    "fishing": v.get("ship_type") == 30 or bool(v.get("gfw_geartype")),
                    "gear": v.get("gfw_geartype"),
                    "sog_knots": v.get("last_sog"),
                    "zone": v.get("last_zone"),
                    "last_seen": v["last_seen"],
                    "feed": v.get("last_source") or "aisstream",
                    "category": _category(v, bool(hit), bool(licence)),
                    "licence": licence,
                    "watchlist": hit,
                    "track": track if len(track) > 1 else [],
                },
            }
        )
    end = timeframe.instant(when)
    start = end - datetime.timedelta(hours=hours)
    from app.sources import feed_log

    collection = {
        # Share of the window during which an AIS feed was listening (None = not logged yet):
        # below ~50 %, an empty sea on the map means "no data", not "no vessel".
        "coverage": feed_log.coverage(start, end),
        "gaps": feed_log.gaps(start, end, min_minutes=10),
    }
    return {"type": "FeatureCollection", "features": features, "stats": ais_store.stats(),
            "collection": collection, "time": timeframe.describe(when)}


@router.get("/gfw-sar")
def gfw_sar(days: int = Query(30, ge=1, le=90), at: Optional[str] = timeframe.AT_QUERY,
            _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """GFW Sentinel-1 vessel detections in the EEZ over the ``days`` before the reference instant."""
    when = timeframe.resolve(at)
    return {**gfw_4wings.sar_detections(days=days, end=when.date() if when else None), "time": timeframe.describe(when)}


@router.get("/fishing-effort")
def fishing_effort(days: int = Query(30, ge=1, le=90), at: Optional[str] = timeframe.AT_QUERY,
                   _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """AIS apparent fishing hours by flag (heat layer) over the ``days`` before the reference instant."""
    when = timeframe.resolve(at)
    return {**gfw_4wings.fishing_effort(days=days, end=when.date() if when else None), "time": timeframe.describe(when)}


@router.get("/activity-report")
def activity_report(
    start: Optional[datetime.date] = Query(None, description="Premier jour (défaut : 90 jours avant la fin)"),
    end: Optional[datetime.date] = Query(None, description="Dernier jour (défaut : jour de l'instant de référence)"),
    at: Optional[str] = timeframe.AT_QUERY,
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Fishing activity report of the EEZ over a period (GFW area report): hours by day, flag, gear and vessel,
    port visits; vessels flagged when watchlisted or licensed."""
    when = timeframe.resolve(at)
    end = min(end or timeframe.instant(when).date(), datetime.date.today())
    start = start or end - datetime.timedelta(days=90)
    if start >= end:
        raise HTTPException(status_code=422, detail="La date de début doit précéder la date de fin")
    if (end - start).days > gfw_4wings.REPORT_MAX_DAYS:
        raise HTTPException(status_code=422, detail=f"Période limitée à {gfw_4wings.REPORT_MAX_DAYS} jours")
    report = gfw_4wings.activity_report(start, end)
    if report.get("available"):
        matcher, authorized = Matcher(), authorized_svc.AuthorizedMatcher()
        vms_licensed = vms.licensed_mmsis()
        for vessel in report["vessels"]:
            vessel["watchlist"] = summary(matcher.match(mmsi=vessel.get("mmsi"), name=vessel.get("name")))
            vessel["licence"] = _licence({"mmsi": vessel.get("mmsi")}, authorized, vms_licensed) if vessel.get("mmsi") else None
        from app.sources.gfw import port_visits

        visits = port_visits(start, end)
        ports: Dict[str, Dict[str, Any]] = {}
        for feature in visits or []:
            p = feature["properties"]
            port = ports.setdefault(p.get("port_name") or p.get("anchorage") or "Port inconnu",
                                    {"visits": 0, "foreign_visits": 0, "vessels": set()})
            port["visits"] += 1
            port["foreign_visits"] += bool(p.get("flag_code")) and not is_french_flag(p.get("flag_code"))
            port["vessels"].add(p.get("mmsi") or p.get("vessel_name"))
        report["ports"] = None if visits is None else sorted(
            ({"port": name, "visits": v["visits"], "foreign_visits": v["foreign_visits"], "vessels": len(v["vessels"])}
             for name, v in ports.items()), key=lambda r: -r["visits"])
    return {**report, "time": timeframe.describe(when)}


@router.get("/isobaths")
def isobaths(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Depth contours of the EEZ (20 m to 4 000 m, NOAA ETOPO grid): shelf, shelf break, slope."""
    from app.sources.erddap import isobaths as contours

    return contours()


@router.get("/coverage")
def coverage(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Temporal coverage of the surveillance: what is watched, how fresh, by which feed."""
    cfg = get_settings()
    with read_db() as conn:
        last_scene = conn.execute("SELECT scene_id, processed_at FROM processed_scenes ORDER BY processed_at DESC LIMIT 1").fetchone()
        scenes_7d = conn.execute(
            "SELECT COUNT(*) FROM processed_scenes WHERE processed_at >= ?",
            ((datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=7)).isoformat(),),
        ).fetchone()[0]
        rules = conn.execute("SELECT value, updated_at FROM rule_state WHERE key = 'last_run'").fetchone()
    ais = ais_store.stats()
    return {
        "history_days": cfg.ais_retention_days,
        "sar": {
            "last_scene": dict(last_scene) if last_scene else None,
            "scenes_7d": scenes_7d,
            "auto_scan_interval_minutes": cfg.auto_scan_interval_minutes,
        },
        "ais": {**ais, "collector": bool(cfg.aisstream_api_key and cfg.ais_collector_enabled),
                "stream": collector_status(),
                "feeds": {"aisstream": bool(cfg.aisstream_api_key), "aishub": bool(cfg.aishub_username),
                          "vesselapi": bool(cfg.vesselapi_api_key and cfg.vesselapi_poll_minutes),
                          "vms": "vms" in (ais.get("by_source") or {})}},
        "rules": {"last_run": rules["updated_at"] if rules else None, "result": json.loads(rules["value"]) if rules else None,
                  "enabled": cfg.rules_enabled},
        "gfw": {"configured": bool(cfg.gfw_token)},
        "notifications": {"configured": bool(cfg.alert_webhook_url), **(health.snapshot().get("webhook") or {})},
        "fusion": {"opensanctions": bool(cfg.opensanctions_api_key), "gfw_insights": bool(cfg.gfw_token),
                   "cmems": cfg.cmems_enabled, "wdpa": True,
                   "vesselapi": vesselapi.usage(cfg) if cfg.vesselapi_api_key else None},
    }


@router.get("/mpa")
def marine_protected_areas(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Marine protected areas around the EEZ (WDPA, keyless)."""
    return wdpa.protected_areas()


@router.get("/ocean-layers")
def ocean_layers(at: Optional[str] = timeframe.AT_QUERY, _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Copernicus Marine WMTS overlays (SST, chlorophyll, currents) at the reference instant."""
    when = timeframe.resolve(at)
    return {"layers": cmems.tile_layers(when) if get_settings().cmems_enabled else {}, "time": timeframe.describe(when)}


@router.get("/vessel/{mmsi}")
def vessel_profile(
    mmsi: str = Path(..., min_length=3, max_length=20, pattern=r"^[A-Za-z0-9-]+$"),
    at: Optional[str] = timeframe.AT_QUERY,
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Fused profile of one vessel at the reference instant: identity, feeds and track of the 24 h before,
    watchlist, registry, GFW insights, sanctions (current intelligence), incidents (status at that instant)."""
    when = timeframe.resolve(at)
    end = timeframe.instant(when)
    with read_db() as conn:
        vessel = conn.execute("SELECT * FROM ais_vessels WHERE mmsi = ?", (mmsi,)).fetchone()
        if vessel is None:
            raise HTTPException(status_code=404, detail="Navire inconnu de l'historique")
        vessel = dict(vessel)
        since = (end - datetime.timedelta(hours=24)).isoformat()
        feeds = {r[0]: {"positions_24h": r[1], "last": r[2]} for r in conn.execute(
            "SELECT source, COUNT(*), MAX(ts) FROM ais_positions WHERE mmsi = ? AND ts >= ? AND ts <= ? GROUP BY source",
            (mmsi, since, end.isoformat()))}
        licensed = conn.execute("SELECT 1 FROM ais_positions WHERE mmsi = ? AND source = 'vms' AND ts <= ? LIMIT 1",
                                (mmsi, end.isoformat())).fetchone()
        linked = rows(conn.execute(
            "SELECT DISTINCT i.id, i.ref, i.title, i.status, i.severity, i.kind, i.last_seen FROM incidents i "
            "JOIN incident_observations o ON o.incident_id = i.id WHERE o.vessel_id = ? ORDER BY i.last_seen DESC LIMIT 20",
            (f"AIS-{mmsi}"[:20],)).fetchall())
        if when is not None:
            last = conn.execute(
                "SELECT ts, lat, lon, sog, source FROM ais_positions WHERE mmsi = ? AND ts <= ? ORDER BY ts DESC LIMIT 1",
                (mmsi, end.isoformat())).fetchone()
            if last is None:
                raise HTTPException(status_code=404, detail="Navire non encore entendu à cet instant")
            vessel.update(last_seen=last["ts"], last_lat=last["lat"], last_lon=last["lon"], last_sog=last["sog"],
                          last_source=last["source"])
    if when is not None:
        states = incidents_svc.state_at(when)
        linked = [{**i, "status": states[i["id"]]["status"], "severity": states[i["id"]]["severity"]}
                  for i in linked if i["id"] in states]
    hit = summary(Matcher().match(mmsi=mmsi, imo=vessel.get("imo"), name=vessel.get("name"), call_sign=vessel.get("call_sign")))
    licence = _licence(vessel, authorized_svc.AuthorizedMatcher(), {mmsi} if licensed else set())
    vessel["flag"] = _flag(vessel)
    try:
        intel = json.loads(vessel.pop("intel", None) or "{}")
    except ValueError:
        intel = {}
    if "registry" not in intel and get_settings().vesselapi_api_key:
        # On demand (one budgeted call per vessel, cached 30 days): merchant ships are not looked up 24/7.
        record = vesselapi.vessel_record(mmsi)
        if record is not None:
            intel["registry"] = record
            with db() as conn:
                conn.execute("UPDATE ais_vessels SET intel = ? WHERE mmsi = ?", (json.dumps(intel), mmsi))
    track = ais_store.tracks(since_hours=24, mmsi=mmsi, until=when).get(mmsi, [])
    return {
        "vessel": vessel,
        "category": _category(vessel, bool(hit), bool(licence)),
        "licensed_vms": bool(licensed),
        "licence": licence,
        "feeds": feeds,
        "track": [{"ts": p["ts"], "lat": p["lat"], "lon": p["lon"], "sog": p["sog"], "source": p.get("source")} for p in track],
        "watchlist": hit,
        "intel": intel,
        "intel_note": "renseignement actuel (registre, GFW, sanctions) : non reconstitué à l'instant du rejeu" if when else None,
        "incidents": linked,
        "time": timeframe.describe(when),
    }


@router.post("/vms/import")
async def import_vms(request: Request, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    """VMS positions (NAF messages or CSV export), raw body, max 10 MB — fused with the AIS history."""
    from app.sources.marine_regions import classify_zone, zone_geometries

    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide")
    zones = zone_geometries()
    try:
        report = vms.import_positions(data, zone_of=lambda lon, lat: classify_zone(lon, lat, zones))
    except vms.VmsFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record("vms.import", actor=user["username"], details={k: report[k] for k in ("format", "positions", "vessels")})
    return report
