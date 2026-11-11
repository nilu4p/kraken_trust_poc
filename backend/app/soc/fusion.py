"""Multi-sensor corroboration: does another sensor confirm what the radar saw?

Used by the incident decision support (block "Corroboration multi-capteurs"):
the observations around an incident are fused into contacts, and a dark contact
seen by two independent disciplines no longer counts as a doubtful echo.

Inputs over a time window (every observation keeps its origin):

| INT | Source | Position σ | Weight (probability the vessel is real) |
| --- | --- | --- | --- |
| ``SAR`` (IMINT radar) | sealed Sentinel-1 targets | 0.1 km | detection reliability (quality score) |
| ``EO`` (IMINT optical) | Sentinel-2 / Landsat detections | 0.05-0.1 km | detector confidence (cloud-penalised) |
| ``IR`` (thermal) | Landsat target warmer than the sea by ≥ 0.5 K | 0.1 km | 0.6 |
| ``NIGHT`` (night lights) | VIIRS DNB lights | 0.8 km, ± 1 h | detector confidence |
| ``GFW`` | Global Fishing Watch Sentinel-1 detections (daily) | 0.5 km, ± 12 h | 0.75 |
| ``AIS`` / ``VMS`` (cooperative) | self-reported positions | 0.1 km | 0.99 (identity claimed by the vessel) |

**Association.** Cooperative tracks are built per MMSI; a non-cooperative
observation is *explained* by the AIS/VMS track whose position at that instant
(interpolated or dead-reckoned) lies within its gate. The remaining
observations are chained in time by a nearest-neighbour multi-sensor tracker:
an observation joins the contact whose last observation could have been reached
at ``fusion_gate_speed_kn`` within the time difference (plus both position and
time uncertainties, within 24 h). Two detections of the **same acquisition**
are never merged (they are two vessels).

**Confidence.** Per INT the best probability is kept (repeated looks by the
same sensor are correlated); INTs are combined as independent evidence:
``P = 1 − Π (1 − p_INT)``. A dark contact seen by two independent disciplines
(radar + optical, radar + lights, optical + GFW…) is *corroborated*.

Pure read: nothing is stored; the same window always gives the same picture.
"""

from __future__ import annotations

import datetime
import hashlib
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.core.geo import NM_TO_KM, haversine_km
from app.db import read_db, rows

logger = logging.getLogger(__name__)

INT_LABELS = {
    "SAR": "Radar satellite (Sentinel-1)",
    "EO": "Optique satellite (Sentinel-2 / Landsat)",
    "IR": "Infrarouge thermique (Landsat TIRS)",
    "NIGHT": "Lumières nocturnes (VIIRS)",
    "GFW": "Détections radar Global Fishing Watch",
    "AIS": "AIS (déclaratif)",
    "VMS": "VMS (balise de pêche sous licence)",
}
COOPERATIVE = ("AIS", "VMS")
MAX_LINK_H = 24.0
# Corroboration ("two disciplines saw the same vessel") is stricter than tracking: same area,
# at the pace of a vessel at work (≈ 1.5 kn), within 12 h.
CORROBORATION_MAX_H = 12.0
CORROBORATION_BASE_KM = 5.0
CORROBORATION_KMH = 3.0
TRACK_EXTRAPOLATION_H = 3.0
AIS_SAMPLE_MIN = 20


def _parse(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        moment = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=datetime.timezone.utc)


def _obs(int_: str, *, at: datetime.datetime, lat: float, lon: float, sigma_km: float, p: float, source: str,
         ref: str, acquisition: str, time_unc_h: float = 0.0, detail: str = "", mmsi: Optional[str] = None,
         extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {"int": int_, "at": at, "lat": float(lat), "lon": float(lon), "sigma_km": sigma_km, "p": round(float(p), 2),
            "source": source, "ref": ref, "acquisition": acquisition, "time_unc_h": time_unc_h, "detail": detail,
            "mmsi": mmsi, **(extra or {})}


# --------------------------------------------------------------------------- collection


def _sar(start: datetime.datetime, end: datetime.datetime, cfg: Settings) -> List[Dict[str, Any]]:
    from app.pipeline.aggregation import get_all_kraken_geojson

    span = max(1, (datetime.datetime.now(datetime.timezone.utc) - start).days + 2)
    result = []
    for feature in get_all_kraken_geojson(cfg, max_age_days=span).get("features") or []:
        props = feature.get("properties") or {}
        if props.get("type") != "dark_vessel":
            continue
        at = _parse(props.get("scene_datetime"))
        if at is None or not start <= at <= end:
            continue
        lon, lat = (feature.get("geometry") or {}).get("coordinates") or (None, None)
        if lat is None:
            continue
        quality = props.get("quality_score")
        result.append(_obs(
            "SAR", at=at, lat=lat, lon=lon, sigma_km=0.1, p=quality if quality is not None else 0.6,
            source="Sentinel-1", ref=f"{props.get('alert_id')}:{props.get('vessel_id')}",
            acquisition=f"s1:{props.get('stac_source')}",
            detail=f"{props.get('vessel_id')} · ~{round(props['length_m'])} m" if props.get("length_m") else str(props.get("vessel_id")),
            extra={"incident_id": props.get("incident_id"), "incident_ref": props.get("incident_ref"),
                   "length_m": props.get("length_m")},
        ))
    return result


def _sensors(start: datetime.datetime, end: datetime.datetime, cfg: Settings) -> List[Dict[str, Any]]:
    from app.soc import sensors

    lookback = start - datetime.timedelta(hours=2)  # VIIRS nominal times are ± 1 h
    result = []
    for d in sensors.list_detections(lookback, end, settings=cfg):
        at = _parse(d["acquired_at"])
        if at is None:
            continue
        attrs = d["attributes"]
        if d["sensor"] == "viirs_dnb":
            result.append(_obs("NIGHT", at=at, lat=d["lat"], lon=d["lon"], sigma_km=0.8, p=d["confidence"],
                               source=d["platform"] or "VIIRS", ref=f"sensor:{d['id']}", acquisition=f"viirs:{d['scene_id']}",
                               time_unc_h=(d.get("time_uncertainty_min") or 60) / 60,
                               detail=f"{attrs.get('radiance')} nW/cm²/sr"))
            continue
        sigma = 0.05 if d["sensor"] == "s2_msi" else 0.1
        result.append(_obs("EO", at=at, lat=d["lat"], lon=d["lon"], sigma_km=sigma, p=d["confidence"],
                           source=sensors.SENSOR_LABELS.get(d["sensor"], d["sensor"]), ref=f"sensor:{d['id']}",
                           acquisition=f"{d['sensor']}:{d['scene_id']}",
                           detail=f"~{round(d['length_m'])} m" if d.get("length_m") else "", extra={"length_m": d.get("length_m")}))
        if (attrs.get("thermal_delta_k") or 0) >= 0.5:
            result.append(_obs("IR", at=at, lat=d["lat"], lon=d["lon"], sigma_km=0.1, p=0.6, source="Landsat TIRS",
                               ref=f"sensor:{d['id']}", acquisition=f"tirs:{d['scene_id']}",
                               detail=f"+{attrs['thermal_delta_k']} K / mer"))
    return result


def _gfw(start: datetime.datetime, end: datetime.datetime, cfg: Settings) -> List[Dict[str, Any]]:
    if not cfg.gfw_token:
        return []
    try:
        from app.sources.gfw_4wings import sar_detections

        days = max(1, math.ceil((end - start).total_seconds() / 86400) + 1)
        data = sar_detections(days, cfg, end=end.date())
    except Exception as exc:
        logger.warning("GFW SAR unavailable for fusion: %s", exc)
        return []
    result = []
    for f in data.get("features") or []:
        props = f["properties"]
        day = _parse(f"{props.get('date')}T12:00:00+00:00")
        if day is None or not start - datetime.timedelta(hours=12) <= day <= end + datetime.timedelta(hours=12):
            continue
        lon, lat = f["geometry"]["coordinates"]
        result.append(_obs("GFW", at=day, lat=lat, lon=lon, sigma_km=0.5, p=0.75, source="Global Fishing Watch",
                           ref=f"gfw:{props.get('date')}:{round(lat, 3)}:{round(lon, 3)}", acquisition=f"gfw:{props.get('date')}",
                           time_unc_h=12, detail="apparié AIS" if props.get("matched_ais") else "sans AIS",
                           mmsi=str(props["mmsi"]) if props.get("mmsi") else None))
    return result


def _cooperative(start: datetime.datetime, end: datetime.datetime, cfg: Settings) -> Dict[str, List[Dict[str, Any]]]:
    """AIS / VMS fixes per MMSI, one every ``AIS_SAMPLE_MIN`` minutes (plus the last one)."""
    west, south, east, north = cfg.eez_bbox
    with read_db(cfg) as conn:
        fixes = rows(conn.execute(
            "SELECT mmsi, ts, lat, lon, sog, cog, source FROM ais_positions WHERE ts >= ? AND ts <= ? "
            "AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? ORDER BY mmsi, ts",
            ((start - datetime.timedelta(hours=TRACK_EXTRAPOLATION_H)).isoformat(),
             (end + datetime.timedelta(hours=TRACK_EXTRAPOLATION_H)).isoformat(), south, north, west, east)).fetchall())
    tracks: Dict[str, List[Dict[str, Any]]] = {}
    latest: Dict[str, Dict[str, Any]] = {}
    for fix in fixes:
        at = _parse(fix["ts"])
        if at is None:
            continue
        mmsi = str(fix["mmsi"])
        entry = {**fix, "at": at}
        track = tracks.setdefault(mmsi, [])
        if track and (at - track[-1]["at"]).total_seconds() < AIS_SAMPLE_MIN * 60:
            latest[mmsi] = entry  # kept only if it ends the track
            continue
        track.append(entry)
        latest.pop(mmsi, None)
    for mmsi, entry in latest.items():
        tracks[mmsi].append(entry)
    return tracks


def track_position(track: List[Dict[str, Any]], at: datetime.datetime) -> Optional[Tuple[float, float, float]]:
    """(lat, lon, σ km) of a cooperative track at ``at``: interpolated, or dead-reckoned up to 3 h beyond its ends."""
    if not track:
        return None
    if at <= track[0]["at"]:
        ref, hours = track[0], (at - track[0]["at"]).total_seconds() / 3600
    elif at >= track[-1]["at"]:
        ref, hours = track[-1], (at - track[-1]["at"]).total_seconds() / 3600
    else:
        for a, b in zip(track, track[1:]):
            if a["at"] <= at <= b["at"]:
                span = (b["at"] - a["at"]).total_seconds() or 1.0
                w = (at - a["at"]).total_seconds() / span
                gap_h = span / 3600
                return a["lat"] + w * (b["lat"] - a["lat"]), a["lon"] + w * (b["lon"] - a["lon"]), 0.2 + 0.5 * gap_h
        return None
    if abs(hours) > TRACK_EXTRAPOLATION_H:
        return None
    from app.sources.ais_store import dead_reckon

    lat, lon = dead_reckon(ref["lat"], ref["lon"], ref.get("sog"), ref.get("cog"), hours)
    speed = float(ref.get("sog") or 0)
    return lat, lon, 0.2 + 0.25 * speed * NM_TO_KM * abs(hours)


# --------------------------------------------------------------------------- association


def _reachable(a: Dict[str, Any], b: Dict[str, Any], gate_kmh: float) -> Optional[float]:
    """Normalised association distance between two observations (None if out of gate)."""
    if a["acquisition"] == b["acquisition"]:
        return None  # same acquisition: two distinct objects
    dt = abs((a["at"] - b["at"]).total_seconds()) / 3600
    if dt > MAX_LINK_H:
        return None
    gate = a["sigma_km"] + b["sigma_km"] + gate_kmh * (dt + a["time_unc_h"] + b["time_unc_h"]) + 0.2
    d = haversine_km(a["lat"], a["lon"], b["lat"], b["lon"])
    return d / gate if d <= gate else None


def _contact_id(observations: List[Dict[str, Any]], mmsi: Optional[str]) -> str:
    seed = f"mmsi:{mmsi}" if mmsi else min(o["ref"] for o in observations)
    return "C-" + hashlib.sha1(seed.encode()).hexdigest()[:8].upper()


def corroborates(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Did two observations of *different* disciplines see the same vessel (same area, close in time)?"""
    if a["int"] == b["int"]:
        return False
    dt = abs((a["at"] - b["at"]).total_seconds()) / 3600
    if dt > CORROBORATION_MAX_H:
        return False
    limit = a["sigma_km"] + b["sigma_km"] + CORROBORATION_BASE_KM + CORROBORATION_KMH * dt
    return haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]) <= limit


def _summarise(observations: List[Dict[str, Any]], mmsi: Optional[str], identity: Dict[str, Any]) -> Dict[str, Any]:
    best: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for o in observations:
        best[o["int"]] = max(best.get(o["int"], 0.0), o["p"])
        counts[o["int"]] = counts.get(o["int"], 0) + 1
    # Disciplines that confirm one another; a track linked only loosely (far apart in time and space)
    # stays a track, it is not corroboration.
    confirmed = {o["int"] for o in observations for other in observations if corroborates(o, other)}
    if mmsi:
        confirmed |= {k for k in best if k in COOPERATIVE}
    strongest = max(best, key=lambda k: best[k])
    weighed = confirmed | {strongest}
    confidence = 1.0 - math.prod(1.0 - min(best[k], 0.99) for k in weighed)
    non_coop = [k for k in best if k not in COOPERATIVE]
    corroborating = [k for k in non_coop if k in confirmed]
    ordered = sorted(observations, key=lambda o: o["at"])
    last = ordered[-1]
    if mmsi and any(k in COOPERATIVE for k in best):
        status = "cooperative"
    elif len(corroborating) >= 2:
        status = "dark_corroborated"
    else:
        status = "dark_single"
    incidents = sorted({o["incident_id"] for o in observations if o.get("incident_id")})
    ints = [{"int": k, "label": INT_LABELS.get(k, k), "p": round(best[k], 2), "count": counts[k], "corroborated": k in confirmed}
            for k in sorted(best, key=lambda k: -best[k])]
    if status == "cooperative":
        why = f"Navire identifié ({'/'.join(k for k in best if k in COOPERATIVE)} MMSI {mmsi})"
        if non_coop:
            why += f", vu aussi par {', '.join(non_coop)}"
    elif status == "dark_corroborated":
        why = f"Sans AIS ni VMS, confirmé par {len(corroborating)} disciplines indépendantes : {', '.join(corroborating)}"
    elif len(non_coop) >= 2:
        why = (f"Sans AIS ni VMS, piste reliant {', '.join(non_coop)} à distance ou à plusieurs heures d'intervalle : "
               f"même navire possible, non confirmé")
    else:
        why = f"Sans AIS ni VMS, vu par une seule discipline ({', '.join(non_coop)}) : à confirmer"
    return {
        "id": _contact_id(observations, mmsi), "status": status, "confidence": round(min(confidence, 0.999), 3),
        "ints": ints, "int_count": len(best), "explanation": why,
        "first_seen": ordered[0]["at"].isoformat(), "last_seen": last["at"].isoformat(),
        "last": {"lat": round(last["lat"], 5), "lon": round(last["lon"], 5), "sigma_km": last["sigma_km"]},
        "identity": {"mmsi": mmsi, **identity} if mmsi else None,
        "incident_ids": incidents,
        "observations": [{**{k: v for k, v in o.items() if k not in ("at",)}, "at": o["at"].isoformat()} for o in ordered],
        "track": [[round(o["lat"], 5), round(o["lon"], 5)] for o in ordered],
    }


def fuse(start: datetime.datetime, end: datetime.datetime, *, include_ais_only: bool = False,
         settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Fused contacts of the window, with per-discipline statistics."""
    cfg = settings or get_settings()
    collected: List[Dict[str, Any]] = []
    sources_status: Dict[str, str] = {}
    for name, collector in (("SAR", _sar), ("EO/IR/NIGHT", _sensors), ("GFW", _gfw)):
        try:
            found = collector(start, end, cfg)
            collected.extend(found)
            sources_status[name] = f"{len(found)} observation(s)"
        except Exception as exc:
            logger.exception("Fusion collector %s failed", name)
            sources_status[name] = f"indisponible ({type(exc).__name__})"
    tracks = _cooperative(start, end, cfg)
    sources_status["AIS/VMS"] = f"{len(tracks)} navire(s)"
    gate_kmh = cfg.fusion_gate_speed_kn * NM_TO_KM

    explained: Dict[str, List[Dict[str, Any]]] = {}
    remaining: List[Dict[str, Any]] = []
    # Time order; simultaneous observations by decreasing weight (deterministic, best evidence first).
    for o in sorted(collected, key=lambda o: (o["at"], -o["p"], o["ref"])):
        if o.get("mmsi") and o["mmsi"] in tracks:
            explained.setdefault(o["mmsi"], []).append(o)
            continue
        best_mmsi, best_score = None, None
        for mmsi, track in tracks.items():
            if not (track[0]["at"] - datetime.timedelta(hours=TRACK_EXTRAPOLATION_H) <= o["at"]
                    <= track[-1]["at"] + datetime.timedelta(hours=TRACK_EXTRAPOLATION_H)):
                continue
            position = track_position(track, o["at"])
            if position is None:
                continue
            gate = 3 * (o["sigma_km"] + position[2]) + gate_kmh * o["time_unc_h"] + 0.3
            d = haversine_km(o["lat"], o["lon"], position[0], position[1])
            if d <= gate and (best_score is None or d / gate < best_score):
                best_mmsi, best_score = mmsi, d / gate
        if best_mmsi:
            explained.setdefault(best_mmsi, []).append(o)
        else:
            remaining.append(o)

    # Dark contacts: nearest-neighbour chaining in time order.
    dark: List[List[Dict[str, Any]]] = []
    for o in remaining:
        best_index, best_score = None, None
        for index, contact in enumerate(dark):
            if any(c["acquisition"] == o["acquisition"] for c in contact):
                continue
            score = _reachable(contact[-1], o, gate_kmh)
            if score is not None and (best_score is None or score < best_score):
                best_index, best_score = index, score
        if best_index is None:
            dark.append([o])
        else:
            dark[best_index].append(o)

    statics = _statics(list(tracks), cfg)
    contacts = []
    ais_only = 0
    for mmsi, track in tracks.items():
        coop = [_obs("VMS" if f.get("source") == "vms" else "AIS", at=f["at"], lat=f["lat"], lon=f["lon"], sigma_km=0.1, p=0.99,
                     source=str(f.get("source") or "aisstream"), ref=f"ais:{mmsi}:{f['at'].isoformat()}",
                     acquisition=f"ais:{mmsi}:{f['at'].isoformat()}", mmsi=mmsi)
                for f in track if start <= f["at"] <= end]
        others = explained.get(mmsi, [])
        if not coop and not others:
            continue
        if not others:
            ais_only += 1
            if not include_ais_only:
                continue
        contacts.append(_summarise(coop + others, mmsi, statics.get(mmsi, {})))
    for observations in dark:
        contacts.append(_summarise(observations, None, {}))
    contacts.sort(key=lambda c: ({"dark_corroborated": 0, "dark_single": 1, "cooperative": 2}[c["status"]], -c["confidence"]))
    stats = {
        "contacts": len(contacts),
        "dark_corroborated": sum(1 for c in contacts if c["status"] == "dark_corroborated"),
        "dark_single": sum(1 for c in contacts if c["status"] == "dark_single"),
        "cooperative_corroborated": sum(1 for c in contacts if c["status"] == "cooperative"),
        "ais_only_vessels": ais_only,
        "observations_by_int": {k: sum(1 for o in collected if o["int"] == k) for k in INT_LABELS if any(o["int"] == k for o in collected)},
    }
    return {"start": start.isoformat(), "end": end.isoformat(), "gate_speed_kn": cfg.fusion_gate_speed_kn,
            "contacts": contacts, "stats": stats, "sources": sources_status, "int_labels": INT_LABELS}


def _statics(mmsis: List[str], cfg: Settings) -> Dict[str, Dict[str, Any]]:
    if not mmsis:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    with read_db(cfg) as conn:
        for chunk in range(0, len(mmsis), 500):
            part = mmsis[chunk:chunk + 500]
            for r in rows(conn.execute(
                    f"SELECT mmsi, name, flag, ship_type FROM ais_vessels WHERE mmsi IN ({','.join('?' * len(part))})", part).fetchall()):
                result[str(r["mmsi"])] = {"name": r.get("name") or None, "flag": r.get("flag"), "ship_type": r.get("ship_type")}
    return result


def around(lat: float, lon: float, radius_km: float, start: datetime.datetime, end: datetime.datetime,
           settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Contacts with at least one observation within ``radius_km`` of a point (nearest first)."""
    picture = fuse(start, end, include_ais_only=True, settings=settings)
    result = []
    for contact in picture["contacts"]:
        distance = min(haversine_km(lat, lon, o["lat"], o["lon"]) for o in contact["observations"])
        if distance <= radius_km:
            result.append({**contact, "distance_km": round(distance, 2)})
    result.sort(key=lambda c: c["distance_km"])
    return result


def corroboration(incident: Dict[str, Any], at: Optional[datetime.datetime] = None,
                  settings: Optional[Settings] = None) -> Dict[str, Any]:
    """The fused contact that carries an incident, and how many disciplines back it."""
    moment = at or datetime.datetime.now(datetime.timezone.utc)
    seen = _parse(incident.get("last_seen")) or moment
    start = min(seen, moment) - datetime.timedelta(hours=36)
    nearby = around(incident["lat"], incident["lon"], 25.0, start, moment, settings)
    own = [c for c in nearby if incident["id"] in c["incident_ids"]]
    best = own[0] if own else (nearby[0] if nearby and nearby[0]["distance_km"] <= 3.0 else None)
    if best is None:
        return {"contact": None, "nearby": nearby[:8], "corroborated": False,
                "summary": "Aucune autre discipline n'a observé la zone sur la période."}
    non_coop = [i["int"] for i in best["ints"] if i["int"] not in COOPERATIVE]
    return {
        "contact": best, "nearby": [c for c in nearby if c["id"] != best["id"]][:8],
        "corroborated": best["status"] == "dark_corroborated" or (best["status"] == "cooperative" and bool(non_coop)),
        "summary": f"{best['explanation']} — confiance fusionnée {round(best['confidence'] * 100)} %.",
    }

