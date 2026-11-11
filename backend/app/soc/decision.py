"""Decision support for an incident: where the target is now, who can reach it, what to do.

Everything here is computed from recorded facts and stated assumptions, and
every figure says how it was obtained:

1. **Target state** — last known position and its kinematics: the vessel's own
   AIS (incidents raised on an MMSI), or an estimate from two successive radar
   observations, or unknown (dark target seen once). The position is projected
   to *now* by dead reckoning, with an uncertainty radius that grows with the
   age of the fix (a dark target of unknown course is a *datum*: a circle of
   radius age × assumed speed).
2. **Geography** — legal zone now, time before the target leaves the EEZ on its
   current course (or reaches the coast).
3. **Means** — for each intervention asset: earliest interception (straight
   route from its departure point at sea, after its readiness delay, solved
   against the target's motion), distance, endurance check, legal zone at the
   interception point, search radius on arrival.
4. **Courses of action** — interception, airborne identification, reinforced
   surveillance, cooperation / flag-State report — with the one recommended and
   why. The operator decides; this is advice, recorded with the assumptions.

Limits stated in every result: straight routes (no land avoidance beyond the
harbour exit), constant speed and course, generic asset values until the unit
edits them.
"""

from __future__ import annotations

import datetime
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.core.geo import NM_TO_KM, haversine_km, point_in_geometry
from app.db import read_db, row
from app.soc import assets as assets_svc

logger = logging.getLogger(__name__)

KN_TO_KMH = NM_TO_KM
MAX_PROJECTION_H = 72.0
PROJECTION_STEP_MIN = 10
MAX_RELEVANT_INTERCEPT_H = 48.0
MAX_PLAUSIBLE_SPEED_KN = 35.0
DOUBTFUL_QUALITY = 0.45

ZONE_LABELS = {
    "territorial": "mer territoriale (0-12 NM)",
    "contiguous": "zone contiguë (12-24 NM)",
    "eez": "ZEE (24-200 NM)",
    None: "hors des eaux sous juridiction française",
}
LEGAL_FRAME = {
    "territorial": (
        "Mer territoriale : souveraineté de l'État côtier. La pêche prive le passage de son caractère inoffensif "
        "(CNUDM art. 19 §2 i) : contrôle, déroutement et poursuites de droit commun."
    ),
    "contiguous": (
        "Zone contiguë : droits souverains de la ZEE sur la pêche (CNUDM art. 73) et, en plus, contrôles douaniers, "
        "fiscaux, sanitaires et d'immigration (art. 33)."
    ),
    "eez": (
        "ZEE : droits souverains sur les ressources. Arraisonnement, inspection, saisie et poursuites judiciaires "
        "d'un navire de pêche (CNUDM art. 73 §1) ; mainlevée contre caution raisonnable (§2) ; pas d'emprisonnement "
        "pour la seule infraction de pêche (§3) ; notification à l'État du pavillon en cas de saisie (§4)."
    ),
    None: (
        "Hors des eaux françaises : pas de police des pêches, sauf droit de poursuite commencé dans les eaux "
        "françaises et ininterrompu (CNUDM art. 111) ou accord de l'État du pavillon."
    ),
}
HOT_PURSUIT = (
    "Droit de poursuite (CNUDM art. 111) : il doit commencer pendant que le navire est dans les eaux françaises, "
    "après un signal visuel ou sonore perceptible, se poursuivre sans interruption, et cesse dès l'entrée du navire "
    "dans la mer territoriale d'un autre État."
)
CAVEAT = (
    "Repères juridiques indicatifs : ils ne remplacent pas la décision du délégué du gouvernement pour l'action de "
    "l'État en mer ni l'avis du parquet."
)


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _parse(value: Any) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


# --------------------------------------------------------------------------- local geometry


def _to_xy(lat: float, lon: float, lat0: float, lon0: float) -> Tuple[float, float]:
    """Local tangent plane (km, x east / y north) around (lat0, lon0): accurate at EEZ scale."""
    return (lon - lon0) * 111.32 * math.cos(math.radians(lat0)), (lat - lat0) * 110.57


def _from_xy(x: float, y: float, lat0: float, lon0: float) -> Tuple[float, float]:
    return lat0 + y / 110.57, lon0 + x / (111.32 * max(math.cos(math.radians(lat0)), 1e-6))


def _velocity_kmh(sog_kn: Optional[float], cog_deg: Optional[float]) -> Tuple[float, float]:
    if not sog_kn or cog_deg is None:
        return 0.0, 0.0
    speed = float(sog_kn) * KN_TO_KMH
    rad = math.radians(float(cog_deg))
    return speed * math.sin(rad), speed * math.cos(rad)


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    x, y = _to_xy(lat2, lon2, lat1, lon1)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def intercept_time_h(
    target_xy: Tuple[float, float], velocity: Tuple[float, float], base_xy: Tuple[float, float],
    speed_kmh: float, readiness_h: float,
) -> Optional[float]:
    """Earliest t ≥ readiness (hours from now) at which an asset leaving ``base`` meets the target.

    Target at P0 + v·t; the asset leaves at t = r and runs straight at speed s:
    |P0 + v·t − B| = s·(t − r). Squared, a quadratic in t. None when the target
    outruns the asset on its current course.
    """
    if speed_kmh <= 0:
        return None
    dx, dy = target_xy[0] - base_xy[0], target_xy[1] - base_xy[1]
    vx, vy = velocity
    a = vx * vx + vy * vy - speed_kmh * speed_kmh
    b = 2 * (dx * vx + dy * vy) + 2 * speed_kmh * speed_kmh * readiness_h
    c = dx * dx + dy * dy - speed_kmh * speed_kmh * readiness_h * readiness_h
    roots: List[float] = []
    if abs(a) < 1e-9:
        if abs(b) > 1e-12:
            roots = [-c / b]
    else:
        disc = b * b - 4 * a * c
        if disc < 0:
            return None
        sq = math.sqrt(disc)
        roots = [(-b - sq) / (2 * a), (-b + sq) / (2 * a)]
    valid = sorted(t for t in roots if t >= readiness_h - 1e-9)
    # A root of the squared equation must also satisfy the unsquared one (distance = s·(t − r) ≥ 0).
    for t in valid:
        px, py = dx + vx * t, dy + vy * t
        if abs(math.hypot(px, py) - speed_kmh * (t - readiness_h)) < 1e-3 * max(1.0, speed_kmh * t):
            return t
    return None


# --------------------------------------------------------------------------- target state


def _latest_ais_fix(conn: Any, mmsi: str, at: datetime.datetime) -> Optional[Dict[str, Any]]:
    fix = row(conn.execute(
        "SELECT p.ts, p.lat, p.lon, p.sog, p.cog, p.source, v.name, v.flag, v.ship_type, v.length_m "
        "FROM ais_positions p LEFT JOIN ais_vessels v ON v.mmsi = p.mmsi "
        "WHERE p.mmsi = ? AND p.ts <= ? AND p.ts >= ? ORDER BY p.ts DESC LIMIT 1",
        (mmsi, at.isoformat(), (at - datetime.timedelta(hours=48)).isoformat()),
    ).fetchone())
    return fix


def target_state(incident: Dict[str, Any], at: datetime.datetime, cfg: Settings) -> Dict[str, Any]:
    """Last known position, kinematics (with their origin), projected position now and its uncertainty."""
    observations = sorted(incident.get("observations") or [], key=lambda o: o["seen_at"], reverse=True)
    latest = observations[0] if observations else {"lat": incident["lat"], "lon": incident["lon"],
                                                   "seen_at": incident["last_seen"], "vessel_id": ""}
    state: Dict[str, Any] = {
        "lat": latest["lat"], "lon": latest["lon"], "time": latest["seen_at"], "sog_kn": None, "cog_deg": None,
        "kinematics": "unknown", "kinematics_label": "Inconnue (cible vue une seule fois, sans AIS)",
        "mmsi": None, "name": None, "flag": None,
    }
    vessel_id = str(latest.get("vessel_id") or "")
    if vessel_id.startswith("AIS-"):
        mmsi = vessel_id[4:]
        state["mmsi"] = mmsi
        with read_db(cfg) as conn:
            fix = _latest_ais_fix(conn, mmsi, at)
        if fix:
            from app.sources.mid import flag_from_mmsi

            state.update(lat=fix["lat"], lon=fix["lon"], time=fix["ts"], name=fix.get("name"),
                         flag=fix.get("flag") or flag_from_mmsi(mmsi))
            if fix.get("sog") is not None and fix.get("cog") is not None and float(fix["cog"]) < 360:
                state.update(sog_kn=float(fix["sog"]), cog_deg=float(fix["cog"]), kinematics="ais",
                             kinematics_label=f"AIS du navire ({fix.get('source') or 'aisstream'})")
            else:
                state.update(kinematics="ais_position", kinematics_label="Position AIS sans cap ni vitesse exploitables")
    if state["kinematics"] == "unknown":
        distinct = []
        for o in observations:
            if not distinct or o["seen_at"] != distinct[-1]["seen_at"]:
                distinct.append(o)
            if len(distinct) == 2:
                break
        if len(distinct) == 2:
            t1, t0 = _parse(distinct[0]["seen_at"]), _parse(distinct[1]["seen_at"])
            if t1 and t0 and (t1 - t0).total_seconds() >= 600:
                hours = (t1 - t0).total_seconds() / 3600
                speed = haversine_km(distinct[1]["lat"], distinct[1]["lon"], distinct[0]["lat"], distinct[0]["lon"]) / NM_TO_KM / hours
                if speed <= MAX_PLAUSIBLE_SPEED_KN and hours <= 48:
                    state.update(
                        sog_kn=round(speed, 1),
                        cog_deg=round(_bearing(distinct[1]["lat"], distinct[1]["lon"], distinct[0]["lat"], distinct[0]["lon"])),
                        kinematics="observations",
                        kinematics_label=f"Estimée entre deux observations à {hours:.1f} h d'intervalle (même navire supposé)",
                    )
    fix_time = _parse(state["time"]) or at
    age_h = max(0.0, (at - fix_time).total_seconds() / 3600)
    vx, vy = _velocity_kmh(state["sog_kn"], state["cog_deg"])
    lat_now, lon_now = _from_xy(vx * age_h, vy * age_h, state["lat"], state["lon"])
    assumed = float(cfg.decision_assumed_speed_kn)
    travelled_nm = (state["sog_kn"] or 0) * age_h
    if state["kinematics"] == "ais":
        radius = 0.5 + 0.1 * travelled_nm
    elif state["kinematics"] == "observations":
        radius = 1.0 + 0.3 * travelled_nm
    else:
        radius = 1.0 + assumed * age_h  # datum: anywhere the target could have gone at the assumed speed
    state.update(
        age_minutes=round(age_h * 60),
        lat_now=round(lat_now, 5), lon_now=round(lon_now, 5),
        uncertainty_nm=round(min(radius, 250.0), 1),
        assumed_speed_kn=assumed,
    )
    return state


# --------------------------------------------------------------------------- geography


def _zone(lon: float, lat: float, zones: Dict[str, Any]) -> Optional[str]:
    from app.sources.marine_regions import classify_zone

    return classify_zone(lon, lat, zones)


def projection(state: Dict[str, Any], zones: Dict[str, Any], cfg: Settings) -> Dict[str, Any]:
    """Where the current course leads: EEZ exit or coast, within ``MAX_PROJECTION_H``."""
    result: Dict[str, Any] = {"exit_eez_h": None, "exit_point": None, "reaches_coast_h": None, "horizon_h": MAX_PROJECTION_H}
    if not state["sog_kn"] or state["sog_kn"] < 0.5 or state["cog_deg"] is None:
        result["note"] = "Cible stationnaire ou cap inconnu : pas de projection de route."
        return result
    from app.sources import coastline

    eez = zones.get("eez")
    lat0, lon0 = state["lat_now"], state["lon_now"]
    vx, vy = _velocity_kmh(state["sog_kn"], state["cog_deg"])
    inside_at_start = point_in_geometry(lon0, lat0, eez)
    steps = int(MAX_PROJECTION_H * 60 / PROJECTION_STEP_MIN)
    for step in range(1, steps + 1):
        hours = step * PROJECTION_STEP_MIN / 60
        lat, lon = _from_xy(vx * hours, vy * hours, lat0, lon0)
        try:
            located = coastline.locate(lon, lat, cfg)
        except Exception:
            located = None
        if located and located.get("on_land"):
            result["reaches_coast_h"] = round(hours, 2)
            result["note"] = "La route actuelle mène à la côte (entrée en fleuve ou approche d'un port)."
            break
        if inside_at_start and not point_in_geometry(lon, lat, eez):
            # Refine the crossing between the last inside step and this one (≈ 1 min precision).
            low, high = hours - PROJECTION_STEP_MIN / 60, hours
            while high - low > 1 / 60:
                mid = (low + high) / 2
                mid_lat, mid_lon = _from_xy(vx * mid, vy * mid, lat0, lon0)
                low, high = (mid, high) if point_in_geometry(mid_lon, mid_lat, eez) else (low, mid)
            hours = high
            lat, lon = _from_xy(vx * hours, vy * hours, lat0, lon0)
            result["exit_eez_h"] = round(hours, 2)
            result["exit_point"] = {"lat": round(lat, 4), "lon": round(lon, 4)}
            result["note"] = f"Sortie de la ZEE dans {_duration(hours)} si le navire garde cap et vitesse."
            break
    else:
        result["note"] = f"Reste dans la ZEE au moins {int(MAX_PROJECTION_H)} h à cap et vitesse constants."
    if not inside_at_start:
        result["note"] = "Cible déjà hors de la ZEE."
    return result


def _duration(hours: Optional[float]) -> str:
    if hours is None:
        return "—"
    minutes = int(round(hours * 60))
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d}"


# --------------------------------------------------------------------------- means


def evaluate_asset(asset: Dict[str, Any], state: Dict[str, Any], exit_h: Optional[float], zones: Dict[str, Any]) -> Dict[str, Any]:
    lat0, lon0 = state["lat_now"], state["lon_now"]
    base_xy = _to_xy(asset["base_lat"], asset["base_lon"], lat0, lon0)
    known = state["kinematics"] in ("ais", "observations")
    velocity = _velocity_kmh(state["sog_kn"], state["cog_deg"]) if known else (0.0, 0.0)
    speed_kmh = float(asset["speed_kn"]) * KN_TO_KMH
    readiness_h = int(asset["readiness_min"]) / 60
    t = intercept_time_h((0.0, 0.0), velocity, base_xy, speed_kmh, readiness_h)
    result: Dict[str, Any] = {
        "asset_id": asset["id"], "name": asset["name"], "kind": asset["kind"], "kind_label": asset["kind_label"],
        "base_name": asset["base_name"], "status": asset["status"], "status_label": asset["status_label"],
        "can_board": asset["can_board"], "speed_kn": asset["speed_kn"], "readiness_min": asset["readiness_min"],
        "feasible": False, "issues": [],
    }
    if asset["status"] != "available":
        result["issues"].append(f"Moyen {asset['status_label'].lower()}")
    if t is None:
        result["issues"].append("Cible plus rapide que le moyen sur sa route actuelle : interception impossible")
        return result
    point_lat, point_lon = _from_xy(velocity[0] * t, velocity[1] * t, lat0, lon0)
    distance_nm = speed_kmh * (t - readiness_h) / NM_TO_KM
    # Search radius on arrival: uncertainty now, grown by the drift possible until the asset is there.
    growth = 0.1 * (state["sog_kn"] or 0) if state["kinematics"] == "ais" else (
        0.3 * (state["sog_kn"] or 0) if state["kinematics"] == "observations" else state["assumed_speed_kn"])
    search_nm = min(state["uncertainty_nm"] + growth * t, 250.0)
    zone = _zone(point_lon, point_lat, zones)
    result.update(
        eta_minutes=round(t * 60), eta_label=_duration(t), transit_minutes=round((t - readiness_h) * 60),
        distance_nm=round(distance_nm, 1), round_trip_nm=round(2 * distance_nm, 1),
        intercept={"lat": round(point_lat, 4), "lon": round(point_lon, 4)},
        intercept_zone=zone, intercept_zone_label=ZONE_LABELS.get(zone, ZONE_LABELS[None]),
        search_radius_nm=round(search_nm, 1),
        in_range=2 * distance_nm <= float(asset["endurance_nm"]),
        before_exit=exit_h is None or t <= exit_h,
    )
    if not result["in_range"]:
        result["issues"].append(f"Aller-retour {round(2 * distance_nm)} NM > distance franchissable {round(asset['endurance_nm'])} NM")
    if not result["before_exit"]:
        result["issues"].append(f"Arrivée après la sortie de ZEE prévue ({_duration(exit_h)})")
    if t > MAX_RELEVANT_INTERCEPT_H:
        result["issues"].append(f"Délai supérieur à {int(MAX_RELEVANT_INTERCEPT_H)} h : projection peu fiable")
    if zone is None and result["before_exit"]:
        result["issues"].append("Point d'interception hors des eaux françaises")
    result["feasible"] = asset["status"] == "available" and result["in_range"] and result["before_exit"] \
        and t <= MAX_RELEVANT_INTERCEPT_H and zone is not None
    return result


# --------------------------------------------------------------------------- courses of action


def _options(incident: Dict[str, Any], state: Dict[str, Any], proj: Dict[str, Any], means: List[Dict[str, Any]],
             zone_now: Optional[str], fused: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Optional[str], str]:
    quality = incident.get("quality_score")
    severity = incident.get("severity") or "P4"
    stale = state["kinematics"] == "unknown" and state["age_minutes"] > 6 * 60
    doubtful = (quality is not None and quality < DOUBTFUL_QUALITY) or stale
    contact = (fused or {}).get("contact") or {}
    if doubtful and not stale and contact.get("status") == "dark_corroborated" and contact.get("confidence", 0) >= 0.8:
        doubtful = False  # a weak radar echo confirmed by another discipline is no longer doubtful
    boarding = [m for m in means if m["feasible"] and m["can_board"]]
    observers = [m for m in means if m["feasible"] and not m["can_board"]]
    best_board = min(boarding, key=lambda m: m["eta_minutes"]) if boarding else None
    best_obs = min(observers, key=lambda m: m["eta_minutes"]) if observers else None
    leaving = proj.get("exit_eez_h") is not None and not boarding

    options: List[Dict[str, Any]] = []
    if best_board:
        options.append({
            "id": "intercept", "title": f"Interception et contrôle — {best_board['name']}",
            "summary": f"Sur zone dans {best_board['eta_label']} ({best_board['distance_nm']} NM), en "
                       f"{best_board['intercept_zone_label']}.",
            "pros": ["Seule option permettant arraisonnement, constat et saisie (CNUDM art. 73)"],
            "cons": [f"Zone de recherche à l'arrivée : rayon {best_board['search_radius_nm']} NM"]
                    + (["Fiabilité de détection faible : risque d'engager un moyen pour une fausse alarme"] if doubtful else []),
        })
    if best_obs:
        options.append({
            "id": "identify", "title": f"Levée de doute aérienne — {best_obs['name']}",
            "summary": f"Sur zone dans {best_obs['eta_label']} : identification, photographies, guidage d'un moyen nautique.",
            "pros": ["Lève le doute rapidement sur une détection incertaine",
                     "Documente l'infraction (preuves) même si l'interception échoue"],
            "cons": ["Ne permet pas d'arraisonner"],
        })
    options.append({
        "id": "monitor", "title": "Surveillance renforcée sans engagement",
        "summary": "Suivre la cible à l'AIS et au prochain passage radar ; réévaluer à chaque nouvelle observation.",
        "pros": ["Aucun moyen immobilisé"], "cons": ["Le navire peut quitter la zone ou cesser son activité sans constat"],
    })
    options.append({
        "id": "cooperate", "title": "Signalement et coopération",
        "summary": "Transmettre le dossier de preuves scellé à l'État du pavillon, à l'ORGP compétente ou au pays voisin.",
        "pros": ["Reste possible quand l'interception ne l'est pas", "Alimente les listes INN"],
        "cons": ["Pas d'effet immédiat sur l'activité en cours"],
    })

    if best_board and severity in ("P1", "P2") and not doubtful:
        recommended = "intercept"
        why = (f"Sévérité {severity}, détection fiable, et {best_board['name']} peut intercepter dans les eaux françaises "
               f"en {best_board['eta_label']}.")
    elif doubtful and best_obs:
        recommended = "identify"
        why = "Détection incertaine ou position ancienne : lever le doute par air avant d'engager un moyen nautique."
    elif best_board and severity in ("P1", "P2"):
        recommended = "intercept"
        why = (f"Sévérité {severity} et interception possible ; la fiabilité est faible : confirmer la cible pendant le "
               f"transit.")
    elif leaving or (zone_now is None) or (severity in ("P1", "P2") and not best_board and not best_obs):
        recommended = "cooperate"
        why = ("Aucun moyen ne peut atteindre la cible dans les eaux françaises avant sa sortie : documenter et signaler."
               if zone_now is not None else "Cible hors des eaux françaises : documenter et signaler.")
        if best_obs and zone_now is not None:
            why += (f" {best_obs['name']} peut être sur zone dans {best_obs['eta_label']} pour identifier et photographier "
                    f"(preuves pour l'État du pavillon) et, le cas échéant, signaler le début d'une poursuite.")
    else:
        recommended = "monitor"
        why = f"Sévérité {severity} : surveiller et réévaluer à la prochaine observation."
    for option in options:
        option["recommended"] = option["id"] == recommended
    return options, recommended, why


# --------------------------------------------------------------------------- entry point


def assess(incident: Dict[str, Any], at: Optional[datetime.datetime] = None, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Full decision-support picture for one incident (pure read: nothing is stored)."""
    cfg = settings or get_settings()
    now = at or _now()
    from app.sources.marine_regions import zone_geometries

    zones = zone_geometries(cfg)
    state = target_state(incident, now, cfg)
    zone_now = _zone(state["lon_now"], state["lat_now"], zones)
    proj = projection(state, zones, cfg)
    means = [evaluate_asset(a, state, proj.get("exit_eez_h"), zones) for a in assets_svc.list_assets(cfg)]
    means.sort(key=lambda m: (not m["feasible"], not m["can_board"], m.get("eta_minutes", 10 ** 9)))
    fused: Optional[Dict[str, Any]] = None
    try:
        from app.soc import fusion

        fused = fusion.corroboration(incident, now, cfg)
    except Exception:
        logger.exception("Fusion unavailable for %s", incident.get("ref"))
    options, recommended, why = _options(incident, state, proj, means, zone_now, fused)
    if fused and fused.get("contact") and fused["contact"]["status"] == "dark_corroborated":
        why += f" Corroboration multi-capteurs : {fused['summary']}"

    caveats = [
        "Routes en ligne droite depuis le point de départ en mer de chaque moyen ; vitesse et cap de la cible supposés constants.",
    ]
    if state["kinematics"] == "unknown":
        caveats.append(
            f"Cinématique inconnue : position de référence (datum) de la dernière observation, cercle d'incertitude "
            f"croissant à {state['assumed_speed_kn']:g} nœuds (paramètre « vitesse supposée d'une cible sans AIS »)."
        )
    elif state["kinematics"] == "observations":
        caveats.append("Vitesse et cap estimés entre deux observations radar : elles peuvent concerner deux navires distincts.")
    if state["age_minutes"] > 180:
        caveats.append(f"Dernière position vieille de {_duration(state['age_minutes'] / 60)} : la projection perd en précision.")
    if any(a.get("updated_by") == "system" for a in assets_svc.list_assets(cfg)):
        caveats.append("Certains moyens ont encore leurs valeurs génériques : les faire renseigner (Commandement › Moyens).")

    legal = [LEGAL_FRAME.get(zone_now, LEGAL_FRAME[None])]
    if proj.get("exit_eez_h") is not None or any(m.get("intercept_zone") is None for m in means if "intercept" in m):
        legal.append(HOT_PURSUIT)
    legal.append(CAVEAT)
    return {
        "computed_at": now.isoformat(),
        "incident": {"id": incident["id"], "ref": incident["ref"], "severity": incident["severity"],
                     "quality_score": incident.get("quality_score"), "risk_score": incident.get("risk_score")},
        "target": state,
        "zone_now": zone_now,
        "zone_now_label": ZONE_LABELS.get(zone_now, ZONE_LABELS[None]),
        "projection": proj,
        "means": means,
        "options": options,
        "recommended": recommended,
        "recommendation": why,
        "legal": legal,
        "caveats": caveats,
        "corroboration": fused,
    }
