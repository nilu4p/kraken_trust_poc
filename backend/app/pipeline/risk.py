"""Explainable IUU risk scoring for dark vessels (0-100 + human-readable reasons).

Fuses the SAR evidence with open-data context:
- legal zone from Marine Regions (territorial sea > contiguous zone > EEZ),
- Global Fishing Watch behaviour nearby (AIS gaps, encounters, loitering, fishing),
- Open-Meteo sea state (detection reliability),
- Copernicus Marine ocean (thermal fronts, chlorophyll) and WDPA marine protected areas,
- target size (artisanal canoe vs. industrial trawler).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.core.geo import haversine_km

ZONE_POINTS = {"territorial": 25, "contiguous": 15, "eez": 10}
ZONE_LABELS = {
    "territorial": "Dans la mer territoriale (12 NM)",
    "contiguous": "Dans la zone contiguë (24 NM)",
    "eez": "Dans la ZEE (200 NM)",
}
EVENT_POINTS = {"gap": 15, "encounter": 10, "loitering": 8, "fishing": 5}
EVENT_LABELS = {
    "gap": "coupure(s) AIS",
    "encounter": "rencontre(s) en mer (transbordement ?)",
    "loitering": "flânage(s) suspect(s)",
    "fishing": "activité(s) de pêche déclarée(s)",
}


MIN_PIXELS_FOR_SIZE = 6
TURBID_DEPTH_M = 20.0


def risk_level(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 55:
        return "high"
    if score >= 35:
        return "medium"
    return "low"


# An AIS "gap" of weeks is an out-of-coverage or laid-up vessel, not a tactical switch-off.
MAX_TACTICAL_GAP_HOURS = 168


def nearby_events(
    lat: float,
    lon: float,
    events: List[Dict[str, Any]],
    radius_km: float,
    exclude_mmsi: Optional[set] = None,
) -> Dict[str, int]:
    """Count GFW events of each type within ``radius_km`` of a point.

    Ignored: events of authorised (licensed) vessels — their legal activity is not
    a risk indicator — and AIS gaps longer than a week.
    """
    counts: Dict[str, int] = {}
    for feature in events:
        props = feature.get("properties") or {}
        if exclude_mmsi and str(props.get("mmsi") or "") in exclude_mmsi:
            continue
        if props.get("event_type") == "gap" and float(props.get("gap_hours") or 0) > MAX_TACTICAL_GAP_HOURS:
            continue
        coords = (feature.get("geometry") or {}).get("coordinates") or []
        if len(coords) < 2:
            continue
        if haversine_km(lat, lon, float(coords[1]), float(coords[0])) <= radius_km:
            kind = (feature.get("properties") or {}).get("event_type", "other")
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def score_vessel(
    vessel: Dict[str, Any],
    *,
    zone: Optional[str],
    events: List[Dict[str, Any]],
    sea_state: Optional[Dict[str, Any]] = None,
    ais_source: str = "unavailable",
    quality: Optional[Dict[str, Any]] = None,
    settings: Optional[Settings] = None,
    exclude_mmsi: Optional[set] = None,
) -> Dict[str, Any]:
    """Return ``{score, level, reasons, factors}`` for a dark vessel."""
    cfg = settings or get_settings()
    reasons: List[str] = []
    confidence = float(vessel.get("confidence") or 0.0)

    score = 30.0 * confidence
    reasons.append(f"Écho radar sans AIS (confiance {confidence:.0%})")

    if zone in ZONE_POINTS:
        score += ZONE_POINTS[zone]
        reasons.append(ZONE_LABELS[zone])

    counts = nearby_events(vessel["lat"], vessel["lon"], events, cfg.gap_proximity_km, exclude_mmsi)
    for kind, points in EVENT_POINTS.items():
        n = counts.get(kind, 0)
        if n:
            score += min(points * 1.5, points + 2 * (n - 1))
            reasons.append(
                f"{n} {EVENT_LABELS[kind]} GFW à moins de {cfg.gap_proximity_km:.0f} km "
                f"({cfg.gfw_lookback_days} j)"
            )

    length = vessel.get("length_m")
    pixels = vessel.get("pixels")
    # A 2-5 pixel blob has no measurable length: its "size" is speckle / radar spread.
    if length is not None and (pixels is None or pixels >= MIN_PIXELS_FOR_SIZE):
        if length >= 40:
            score += 10
            reasons.append(f"Cible de grande taille (~{length:.0f} m) : navire industriel probable")
        elif length >= 15:
            score += 5
            reasons.append(f"Cible moyenne (~{length:.0f} m)")

    ocean = vessel.get("ocean") or {}
    depth = ocean.get("depth_m")
    if ocean.get("productive_waters") and depth is not None and depth < TURBID_DEPTH_M:
        # Amazon-fed coastal waters: sediments make satellite chlorophyll unreliable (case-2 waters).
        reasons.append(
            f"Chlorophylle élevée non interprétée (eaux côtières turbides, {depth:.0f} m de fond) : pas d'indice de zone de pêche"
        )
    elif ocean.get("productive_waters"):
        score += 5
        reasons.append(f"Eaux productives (chlorophylle {ocean['chlorophyll_mg_m3']:.1f} mg/m³) : zone de pêche probable")
    if ocean.get("thermal_front"):
        score += 5
        reasons.append(
            f"Front thermique (gradient {ocean['sst_gradient_c_10km']:.1f} °C/10 km, Copernicus Marine) : zone d'agrégation des poissons"
        )
    mpa = vessel.get("mpa")
    if mpa:
        score += 10
        reasons.append(f"Dans l'aire marine protégée « {mpa['name']} » ({mpa.get('designation') or 'WDPA'})")
    limits = vessel.get("fishing_limits") or {}
    if limits.get("fishing_3nm") is not None and limits["fishing_3nm"] < 6:
        score += 5
        reasons.append(f"À {limits['fishing_3nm']:.0f} km de la limite des 3 milles (zone côtière réglementée)")
    hotspot = int(vessel.get("dark_hotspot") or 0)
    if hotspot:
        score += min(12, 3 * hotspot)
        reasons.append(f"{hotspot} détection(s) SAR GFW sans AIS à moins de 10 km sur 30 j : zone d'activité non déclarée récurrente")
    mismatch = vessel.get("size_mismatch")
    if mismatch:
        score += 10
        reasons.append(
            f"Écho de ~{mismatch['radar_m']} m apparié à l'AIS de {mismatch.get('name') or mismatch['mmsi']} "
            f"(MMSI {mismatch['mmsi']}), déclaré {mismatch['declared_m']} m : second navire sans AIS ou identité AIS douteuse"
        )
    nearby_ais = vessel.get("foreign_fishing_nearby")
    if nearby_ais:
        score += 5
        reasons.append(f"Navire de pêche étranger vu à l'AIS à proximité : {nearby_ais}")
    watch = vessel.get("watchlist")
    if watch:
        score += 30
        reasons.append(f"Navire inscrit sur liste de surveillance ({watch.get('source')}) : {watch.get('reason') or watch.get('name')}")

    factor = 1.0
    reliability = (sea_state or {}).get("sar_reliability") or {}
    # When a detection quality is given it already accounts for sea state.
    if quality is None and reliability.get("level") in {"low", "medium"}:
        factor = float(reliability.get("factor", 1.0))
        reasons.append(f"Pondération météo ×{factor:.2f} : {reliability.get('reason')}")

    if ais_source == "mock":
        factor *= 0.85
        reasons.append("AIS simulé (pas de flux AIS réel) : corrélation à confirmer")
    elif ais_source == "unavailable":
        factor *= 0.85
        reasons.append("Aucune position AIS connue au moment du passage : l'absence d'AIS n'est pas démontrée")

    if quality is not None:
        q = float(quality.get("score", 1.0))
        factor *= 0.5 + 0.5 * q
        if q < 0.7:
            reasons.append(f"Fiabilité de détection {q:.0%} : pondération ×{0.5 + 0.5 * q:.2f}")

    final = int(round(max(0.0, min(100.0, score * factor))))
    return {
        "score": final,
        "level": risk_level(final),
        "reasons": reasons,
        "factors": {"zone": zone, "nearby_events": counts, "weather_factor": factor},
    }
