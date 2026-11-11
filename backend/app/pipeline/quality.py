"""Detection quality (0-1): how much a radar target can be trusted, with explicit flags.

Separate from the IUU *risk*: quality answers "is this really a ship?",
risk answers "how suspicious is this ship?". Signals used:
- local CFAR SNR, blob size and elongation (ships are elongated, speckle is not),
- dual-polarisation confirmation (target visible in both VH/HV and VV/HH),
- swath edge position (noisy near/far range),
- sea state (Open-Meteo),
- multi-temporal persistence: a target seen at the same place in several
  scenes may be fixed (buoy, platform, wreck, rock) — or a vessel returning to
  its fishing spot: a moderate penalty only, none where a vessel was confirmed,
- operator feedback: false positives flagged nearby within the last
  ``false_positive_memory_days`` (an old verdict never blinds a spot forever).
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.core.geo import haversine_km


SHALLOW_M = 10.0
# (distance to the shore below which, penalty, flag) — first matching band only.
COASTAL_BANDS = (
    (200, 0.45, "Au ras du rivage (~{shore} m) : roche, pieu, épave ou lisière de mangrove — très probablement fixe"),
    (500, 0.25, "Proche du rivage (~{shore} m) : rochers, pieux et barrières de pêche fréquents — à confirmer"),
    (1000, 0.1, "Zone côtière (~{shore} m du rivage)"),
)


def _recent(value: Any, window: datetime.timedelta) -> bool:
    """Verdict given within ``window`` (undated verdicts count: they predate this rule)."""
    if not value:
        return True
    try:
        when = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return True
    if when.tzinfo is None:
        when = when.replace(tzinfo=datetime.timezone.utc)
    return datetime.datetime.now(datetime.timezone.utc) - when <= window


def quality_level(score: float) -> str:
    if score >= 0.7:
        return "reliable"
    if score >= 0.45:
        return "check"
    return "doubtful"


def assess(
    det: Dict[str, Any],
    *,
    history: Optional[List[Dict[str, Any]]] = None,
    sea_state: Optional[Dict[str, Any]] = None,
    scene_id: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Return ``{score, level, flags}`` for one radar detection."""
    cfg = settings or get_settings()
    score = 0.55
    flags: List[str] = []

    snr = float(det.get("snr") or 0.0)
    score += 0.25 * max(0.0, min(1.0, (snr - cfg.cfar_k) / (2 * cfg.cfar_k)))

    dual = det.get("dual_pol")
    if dual is True:
        score += 0.15
        flags.append("Confirmé en double polarisation")
    elif dual is False:
        score -= 0.2
        flags.append("Absent de la polarisation complémentaire")

    ai = det.get("ai_confidence")
    if ai is not None:
        if det.get("ai_only"):
            score -= 0.15
            flags.append(f"Détecté par l'IA seule (confiance {ai:.0%}), pas par le CFAR : à vérifier")
        elif cfg.ai_detector_mode == 2:
            if ai >= 0.5:
                score += 0.1
                flags.append(f"Confirmé par l'IA (confiance {ai:.0%})")
            elif ai < 0.1:
                score -= 0.1
                flags.append("Non vu par l'IA de détection")
        else:
            flags.append(f"Avis IA (observation, sans effet sur le score) : {ai:.0%}")

    pixels = int(det.get("pixels") or 0)
    elongation = float(det.get("elongation") or 1.0)
    if pixels and pixels < 3:
        score -= 0.15
        flags.append("Cible ponctuelle (1-2 px) : chatoiement possible")
    elif pixels >= 6 and elongation >= 2.0:
        score += 0.05
        flags.append(f"Forme allongée (×{elongation:.1f}) compatible coque")
    elif pixels >= 6 and elongation < 1.2:
        score -= 0.1
        flags.append("Forme compacte peu typique d'un navire")

    if det.get("swath_edge"):
        score -= 0.15
        flags.append("Bord de fauchée SAR (bruit élevé)")

    if det.get("gfw_confirmed"):
        score += 0.15
        flags.append("Confirmé par une détection SAR Global Fishing Watch le même jour")

    ocean = det.get("ocean") or {}
    coast = det.get("coast") or {}
    shore = coast.get("shore_m")
    coast_known = bool(coast.get("radar_checked") or coast.get("osm_known"))
    # Distance to the shore from the radar image itself and the OSM coastline (echoes ON the
    # coast were already discarded by the detector). Rocks, stakes, fish weirs, wrecks and
    # the mangrove fringe crowd the first few hundred metres.
    for limit, penalty, text in COASTAL_BANDS:
        if shore is not None and shore < limit:
            score -= penalty
            flags.append(text.format(shore=shore))
            break
    structure = coast.get("structure")
    if structure:  # mapped fixed structure 80–300 m away (closer ones were discarded by the detector)
        score -= 0.15
        flags.append(f"{structure['label'].capitalize()} cartographié(e) à ~{structure['distance_m']} m : écho fixe possible")
    if coast_known:
        # The ~1.8 km bathymetry grid calls land many points of the Guiana mud coast that the
        # coastline puts at sea: there it only means "very shallow" (intertidal mud banks).
        depth = ocean.get("depth_m")
        if ocean.get("on_land") or (depth is not None and depth < 3):
            score -= 0.15
            flags.append("Haut-fond (< 3 m, bancs de vase côtiers) : roche ou vase découvrante possible")
        elif depth is not None and depth < SHALLOW_M:
            score -= 0.1
            flags.append(f"Eaux très peu profondes (~{depth:.0f} m) : bancs de vase côtiers, cible à confirmer")
    elif ocean.get("on_land"):
        score -= 0.4
        flags.append("Point sur terre émergée d'après la bathymétrie (île, rocher, côte)")
    elif ocean.get("depth_m") is not None and ocean["depth_m"] < 3:
        score -= 0.2
        flags.append(f"Haut-fond (~{ocean['depth_m']:.0f} m) : roche ou banc de vase possible")
    elif ocean.get("depth_m") is not None and ocean["depth_m"] < SHALLOW_M:
        # Guiana coast: migrating Amazon mud banks in a few metres of water create bright returns.
        score -= 0.1
        flags.append(f"Eaux très peu profondes (~{ocean['depth_m']:.0f} m) : bancs de vase côtiers, cible à confirmer")

    reliability = ((sea_state or {}).get("sar_reliability") or {}).get("level")
    if reliability == "low":
        score -= 0.15
        flags.append("Mer forte : fouillis radar")
    elif reliability == "medium":
        score -= 0.05

    near = [
        h
        for h in history or []
        if haversine_km(det["lat"], det["lon"], h["lat"], h["lon"]) <= cfg.persistence_radius_km
    ]
    confirmed = any(h.get("review") == "confirmed" for h in near)
    memory = datetime.timedelta(days=cfg.false_positive_memory_days)
    if any(h.get("review") == "false_positive" and _recent(h.get("reviewed_at"), memory) for h in near):
        score -= 0.45
        flags.append(f"Fausse alarme signalée par un opérateur à cet endroit (moins de {cfg.false_positive_memory_days} j)")
    other_scenes = {h.get("scene_id") for h in near if h.get("scene_id") and h.get("scene_id") != scene_id}
    if len(other_scenes) >= cfg.persistence_min_scenes and not confirmed:
        score -= 0.15
        flags.append(
            f"Cible récurrente : détectée au même endroit sur {len(other_scenes)} autres scènes — objet fixe "
            "(bouée, plateforme, épave, roche) ou navire qui revient sur son lieu de pêche : à vérifier"
        )
    if confirmed:
        flags.append("Navire déjà confirmé par un opérateur dans ce secteur")

    final = round(max(0.0, min(1.0, score)), 2)
    return {"score": final, "level": quality_level(final), "flags": flags}
