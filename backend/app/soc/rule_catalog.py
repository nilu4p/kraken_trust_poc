"""Catalogue of the built-in detection rules, in plain language, with their live state.

One entry per rule: what it looks for, why it matters, when it fires (each
condition as a sentence), what it produces (incident kind, risk, channel), the
operational parameters that tune it (``app.soc.runtime``), the data sources it
needs, its known limits — plus, computed live: whether it can run (sources
configured), whether it is enabled, and how it performed over 30 days
(incidents opened, share closed as false alarms).

Built-in rules can be switched off (except the radar pipeline, the core of the
platform); the list of disabled rules is kept in ``rule_state``.
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.db import db, read_db

DISABLED_KEY = "rules_disabled"

BUILTIN: List[Dict[str, Any]] = [
    {
        "id": "dark_vessel",
        "label": "Navire sans AIS détecté au radar",
        "family": "Radar",
        "kinds": ["dark_vessel"],
        "summary": "Un écho radar Sentinel-1 dans la ZEE qu'aucun navire déclaré (AIS, VMS) n'explique au moment du passage.",
        "why": "Un navire qui coupe son AIS ou n'en a pas échappe à la surveillance classique : c'est la signature principale de la pêche illégale.",
        "conditions": [
            "un écho plus brillant que la mer environnante (CFAR, seuil k)",
            "de taille et de forme compatibles avec un navire, d'une fiabilité suffisante",
            "aucune position AIS ou VMS à proximité au moment du passage satellite (positions projetées à l'instant exact)",
            "et la collecte AIS écoutait à ce moment-là (sinon : « écho non identifié », risque minoré)",
        ],
        "outcome": "Incident « navire sans AIS », sévérité selon le score de risque (zone, taille, événements GFW, aire protégée…), preuve scellée C2PA.",
        "params": ["cfar_k", "min_detection_quality", "ais_match_km", "ais_correlation_window_h", "persistence_min_scenes",
                   "false_positive_memory_days", "sar_max_windows", "ai_detector_mode"],
        "sources": [["access_key"], ["secret_key"]],
        "limits": "Ne voit que les passages Sentinel-1 (quelques par semaine) ; mer forte et petites embarcations réduisent la fiabilité.",
        "doc": "moteur/detection-sar/",
        "can_disable": False,
    },
    {
        "id": "foreign_fishing",
        "label": "Pêcheur étranger dans la ZEE",
        "family": "Présence",
        "kinds": ["foreign_fishing"],
        "summary": "Un navire de pêche sous pavillon étranger émet son AIS dans les eaux françaises.",
        "why": "Seuls les navires disposant d'une licence peuvent pêcher dans la ZEE ; un pêcheur étranger sans licence y est en infraction présumée.",
        "conditions": [
            "navire entendu dans la dernière heure, en mer territoriale, zone contiguë ou ZEE",
            "navire de pêche (type AIS 30 ou engin de pêche connu de Global Fishing Watch)",
            "pavillon étranger (d'après le MMSI ou GFW)",
            "sans licence : absent du VMS et de la liste des navires autorisés",
        ],
        "outcome": "Incident « pêche étrangère », risque 85 en mer territoriale, 70 en zone contiguë, 60 en ZEE ; une fois par navire et par jour.",
        "params": [],
        "sources": [["aisstream_api_key", "aishub_username", "vesselapi_api_key"]],
        "limits": "Repose sur l'AIS déclaré : un navire qui ment sur son type ou son pavillon y échappe (voir la règle radar).",
        "doc": "moteur/regles/",
    },
    {
        "id": "watchlist_ais",
        "label": "Navire listé (INN) visible à l'AIS",
        "family": "Renseignement",
        "kinds": ["watchlist_ais"],
        "summary": "Un navire de la liste de surveillance (liste INN combinée, ajouts manuels, ou inscrit INN par une ORGP selon GFW) émet dans la ZEE.",
        "why": "Un navire déjà sanctionné pour pêche illégale ailleurs est une cible prioritaire.",
        "conditions": [
            "navire entendu dans la dernière heure dans la ZEE",
            "correspondance avec la liste de surveillance (MMSI, IMO, indicatif ou nom)",
            "ou inscription INN par une ORGP rapportée par GFW Insights",
        ],
        "outcome": "Incident « liste de surveillance », risque 90 ; une fois par navire et par jour.",
        "params": ["intel_lookups_per_tick"],
        "sources": [["aisstream_api_key", "aishub_username", "vesselapi_api_key"]],
        "limits": "Une correspondance sur le nom seul peut être un homonyme : vérifier l'IMO.",
        "doc": "moteur/regles/",
    },
    {
        "id": "sanctions",
        "label": "Navire sous sanctions internationales",
        "family": "Renseignement",
        "kinds": ["sanctions"],
        "summary": "Un navire présent dans la ZEE correspond à une liste de sanctions (OFAC, UE, ONU, Royaume-Uni…) selon OpenSanctions.",
        "why": "Un navire sanctionné peut servir au transbordement, à la fraude ou à d'autres trafics.",
        "conditions": [
            "navire entendu dans la dernière heure dans la ZEE",
            "score de correspondance OpenSanctions supérieur ou égal au seuil",
        ],
        "outcome": "Incident « sanctions », risque 85 ; une fois par navire et par jour.",
        "params": ["sanctions_min_score", "intel_lookups_per_tick"],
        "sources": [["opensanctions_api_key"]],
        "limits": "Homonymes fréquents sous un score de 0,9 : lire la fiche OpenSanctions.",
        "doc": "moteur/regles/",
    },
    {
        "id": "mpa_fishing",
        "label": "Pêche en aire marine protégée",
        "family": "Comportement",
        "kinds": ["mpa_fishing"],
        "summary": "Un navire de pêche se déplace lentement, à vitesse de pêche, dans une aire marine protégée.",
        "why": "Une licence ne couvre pas une réserve : la pêche y est interdite ou strictement encadrée.",
        "conditions": [
            "navire de pêche entendu dans la dernière heure",
            "position à l'intérieur d'une aire protégée (WDPA)",
            "vitesse inférieure ou égale au seuil (vitesse de pêche)",
        ],
        "outcome": "Incident « pêche en aire protégée », risque 75 si étranger, 65 sinon ; une fois par navire, aire et jour.",
        "params": ["mpa_max_speed_knots"],
        "sources": [["aisstream_api_key", "aishub_username", "vesselapi_api_key"]],
        "limits": "Un navire en transit lent ou au mouillage peut déclencher la règle.",
        "doc": "moteur/regles/",
    },
    {
        "id": "ais_gap",
        "label": "Coupure AIS en zone côtière",
        "family": "Comportement",
        "kinds": ["ais_gap"],
        "summary": "Un navire qui émettait régulièrement près de la côte se tait, alors que les autres navires sont toujours entendus.",
        "why": "Couper son AIS juste avant de pêcher illégalement est un comportement classique.",
        "conditions": [
            "navire en mer territoriale ou zone contiguë (bonne couverture AIS terrestre)",
            "au moins 6 positions dans les 6 heures précédant le silence, en mouvement",
            "silence d'au moins le seuil (et de moins de 48 h)",
            "la réception fonctionne : au moins 2 autres navires entendus dans les 30 dernières minutes",
        ],
        "outcome": "Incident « coupure AIS », risque 75 si pêcheur ou étranger, 60 sinon.",
        "params": ["ais_gap_hours"],
        "sources": [["aisstream_api_key", "aishub_username", "vesselapi_api_key"]],
        "limits": "Un navire qui sort de la portée des récepteurs côtiers peut sembler couper son AIS.",
        "doc": "moteur/regles/",
    },
    {
        "id": "encounter",
        "label": "Rendez-vous en mer",
        "family": "Comportement",
        "kinds": ["encounter"],
        "summary": "Deux navires lents restent très proches l'un de l'autre assez longtemps dans la ZEE.",
        "why": "Signe possible de transbordement de poisson, de carburant ou d'équipage, hors de tout contrôle portuaire.",
        "conditions": [
            "deux navires dans la ZEE, à 2 nœuds ou moins",
            "à moins de la distance seuil l'un de l'autre",
            "pendant au moins la durée seuil (par tranches de 10 min, sur les 3 dernières heures)",
            "à plus de 10 km des ports et mouillages (Cayenne – Dégrad des Cannes, Kourou, îles du Salut), comme Global Fishing Watch",
        ],
        "outcome": "Incident « rendez-vous », risque 70 si l'un est pêcheur ou étranger, 50 sinon ; une fois par paire et par jour.",
        "params": ["encounter_distance_km", "encounter_min_minutes"],
        "sources": [["aisstream_api_key", "aishub_username", "vesselapi_api_key"]],
        "limits": "Remorquage, chalutage en bœuf (deux chalutiers tirant le même filet) ou navires de servitude au large peuvent déclencher la règle.",
        "doc": "moteur/regles/",
    },
]

BY_ID = {rule["id"]: rule for rule in BUILTIN}


def disabled(settings: Optional[Settings] = None) -> List[str]:
    with read_db(settings) as conn:
        row = conn.execute("SELECT value FROM rule_state WHERE key = ?", (DISABLED_KEY,)).fetchone()
    try:
        return [r for r in json.loads(row[0])] if row else []
    except ValueError:
        return []


def set_enabled(rule_id: str, enabled: bool, settings: Optional[Settings] = None) -> None:
    if rule_id not in BY_ID:
        raise ValueError("Règle inconnue")
    if not enabled and not BY_ID[rule_id].get("can_disable", True):
        raise ValueError("Cette règle est le cœur de la détection : elle ne peut pas être désactivée")
    current = set(disabled(settings))
    current.discard(rule_id) if enabled else current.add(rule_id)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with db(settings) as conn:
        conn.execute(
            "INSERT INTO rule_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (DISABLED_KEY, json.dumps(sorted(current)), now),
        )


def performance(kinds: List[str], days: int = 30, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Incidents opened by these kinds over ``days``, how they were closed, last trigger."""
    since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days)).isoformat()
    marks = ",".join("?" for _ in kinds)
    with read_db(settings) as conn:
        rows = conn.execute(
            f"SELECT status, resolution, created_at FROM incidents WHERE kind IN ({marks}) AND created_at >= ?",
            (*kinds, since),
        ).fetchall()
        last = conn.execute(f"SELECT MAX(created_at) FROM incidents WHERE kind IN ({marks})", kinds).fetchone()[0]
    closed = [r for r in rows if r["status"] == "closed"]
    false_alarms = sum(1 for r in closed if r["resolution"] == "false_positive")
    return {
        "days": days,
        "opened": len(rows),
        "open": len(rows) - len(closed),
        "closed": len(closed),
        "false_alarms": false_alarms,
        "false_alarm_rate": round(false_alarms / len(closed), 2) if closed else None,
        "last_triggered": last,
    }


def _source_label(key: str) -> str:
    """Readable name of a credential (as in Administration › Keys)."""
    from app.core.credentials import CREDENTIALS

    return CREDENTIALS.get(key, {}).get("label", key)


def _sources_ready(groups: List[List[str]], cfg: Settings) -> bool:
    """Each group needs at least one configured credential (e.g. any AIS feed)."""
    return all(any(getattr(cfg, key, "") for key in group) for group in groups)


def describe(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Every built-in rule with its parameters (current values and explanations) and live state."""
    from app.soc import runtime

    cfg = settings or get_settings()
    params = runtime.describe(cfg)
    off = set(disabled(cfg))
    result = []
    for rule in BUILTIN:
        ready = _sources_ready(rule["sources"], cfg) and (cfg.rules_enabled or rule["id"] == "dark_vessel")
        result.append({
            **{k: v for k, v in rule.items() if k != "sources"},
            "can_disable": rule.get("can_disable", True),
            "enabled": rule["id"] not in off,
            "ready": ready,
            "missing_sources": [] if ready else [" ou ".join(_source_label(k) for k in group) for group in rule["sources"]
                                                  if not any(getattr(cfg, k, "") for k in group)],
            "parameters": [{"key": key, **params[key]} for key in rule["params"] if key in params],
            "performance": performance(rule["kinds"], settings=cfg),
        })
    return result


def explanation(rule_id: str, checks: List[Dict[str, Any]], parameters: Optional[Dict[str, Any]] = None,
                label: Optional[str] = None) -> Dict[str, Any]:
    """What is stored with an incident to answer "why this alert?" exactly as it was decided."""
    rule = BY_ID.get(rule_id) or {}
    return {
        "rule": rule_id,
        "label": label or rule.get("label") or rule_id,
        "summary": rule.get("summary", ""),
        "checks": checks,
        "parameters": parameters or {},
        "decided_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }
