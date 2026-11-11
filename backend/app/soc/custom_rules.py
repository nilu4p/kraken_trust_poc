"""Custom detection rules built from the dashboard — declarative, no code, readable as a sentence.

A rule is one or more groups of conditions on the vessels of the AIS / VMS
picture — all conditions of a group must hold ("and"), any group is enough
("or") — chosen from a closed catalogue of fields and operators (``FIELDS``),
plus what to do when it matches: open an incident with a given risk (hence a
severity), on a notification channel, at most once per vessel per cooldown.

Nothing typed by a user is ever executed: definitions are validated against
the catalogue (types, ranges, list sizes) before being stored. Every rule can
be tested before saving (:func:`preview`) — now, or at a past instant of the
replay — showing which vessels it would catch and why.
"""

from __future__ import annotations

import datetime
import json
import math
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.core import audit
from app.db import db, read_db, rows
from app.sources.mid import MID_COUNTRIES, flag_name, is_french

MAX_CONDITIONS = 10   # per group
MAX_GROUPS = 5
MAX_LIST = 60
CHANNELS = {"alertes": "#alertes (opérateurs de quart)", "surveillance": "#surveillance (suivi, moins urgent)"}
ZONES = {"territorial": "mer territoriale (12 NM)", "contiguous": "zone contiguë (24 NM)", "eez": "ZEE (200 NM)",
         "outside": "hors des eaux françaises"}
SHIP_TYPES: Dict[str, Tuple[str, Callable[[Optional[int]], bool]]] = {
    "fishing": ("pêche", lambda t: t == 30),
    "towing": ("remorquage / servitude", lambda t: t in (31, 32, 52, 53, 54)),
    "pleasure": ("plaisance / voile", lambda t: t in (36, 37)),
    "passenger": ("passagers", lambda t: t is not None and 60 <= t <= 69),
    "cargo": ("cargo", lambda t: t is not None and 70 <= t <= 79),
    "tanker": ("pétrolier / citerne", lambda t: t is not None and 80 <= t <= 89),
    "authorities": ("autorités / SAR", lambda t: t in (35, 51, 55, 58)),
    "unknown": ("type non déclaré", lambda t: t in (None, 0)),
}
COUNTRIES = sorted(set(MID_COUNTRIES.values()))

# Field catalogue: what the builder offers. ``kind`` drives the operators and the value widget.
FIELDS: Dict[str, Dict[str, Any]] = {
    "zone": {"label": "Zone maritime", "kind": "choice", "options": ZONES,
             "help": "Zone légale où se trouve le navire (Marine Regions)."},
    "foreign": {"label": "Pavillon étranger", "kind": "bool",
                "help": "Pavillon déduit du MMSI (ou de Global Fishing Watch) ; les territoires d'outre-mer comptent comme français."},
    "flag": {"label": "Pavillon", "kind": "choice", "options": {c: c for c in COUNTRIES},
             "help": "Pays du pavillon (d'après le MMSI)."},
    "ship_type": {"label": "Type de navire (AIS)", "kind": "choice", "options": {k: v[0] for k, v in SHIP_TYPES.items()},
                  "help": "Type déclaré dans les messages AIS statiques."},
    "fishing": {"label": "Navire de pêche", "kind": "bool",
                "help": "Type AIS 30 ou engin de pêche connu de Global Fishing Watch."},
    "speed": {"label": "Vitesse", "kind": "number", "unit": "nœuds", "min": 0, "max": 60,
              "help": "Vitesse sur le fond de la dernière position (1 nœud = 1,852 km/h). Chalutage : 2 à 4 nœuds."},
    "silent_hours": {"label": "Silence AIS depuis", "kind": "number", "unit": "heures", "min": 0, "max": 48,
                     "help": "Temps écoulé depuis la dernière position (navires entendus dans les 48 dernières heures)."},
    "in_mpa": {"label": "Dans une aire marine protégée", "kind": "bool", "help": "Aires protégées WDPA (UNEP-WCMC / UICN)."},
    "drawn_zone": {"label": "Zone de surveillance (dessinée)", "kind": "zone", "options": {},
                   "help": "Zones dessinées sur la carte (Situation › Couches › Zones de surveillance)."},
    "area": {"label": "Zone personnalisée (cercle)", "kind": "circle",
             "help": "Cercle défini par un centre (latitude, longitude) et un rayon en km : estuaire, chenal, zone de pêche…"},
    "night": {"label": "De nuit", "kind": "bool",
              "help": "Heure solaire locale à la position du navire entre 18 h 30 et 6 h 00."},
    "licensed": {"label": "Sous licence", "kind": "bool",
                 "help": "Navire vu dans le VMS ou inscrit dans la liste des navires autorisés."},
    "watchlisted": {"label": "Sur la liste de surveillance", "kind": "bool", "help": "Liste INN combinée et ajouts manuels."},
    "sanctioned": {"label": "Sous sanctions (OpenSanctions)", "kind": "bool",
                   "help": "Correspondance trouvée lors du criblage hebdomadaire."},
    "name": {"label": "Nom du navire", "kind": "text", "help": "Le nom déclaré contient ce texte (sans tenir compte de la casse)."},
}
OPERATORS = {
    "bool": {"is": "est"},
    "number": {"lt": "inférieure à", "gt": "supérieure à"},
    "choice": {"in": "parmi", "not_in": "hors de"},
    "text": {"contains": "contient"},
    "circle": {"inside": "à l'intérieur de", "outside": "à l'extérieur de"},
    "zone": {"in": "dans", "not_in": "hors de"},
}


class RuleError(ValueError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# --------------------------------------------------------------------------- validation


def validate(definition: Dict[str, Any]) -> Dict[str, Any]:
    """Normalised definition, or RuleError with a message an operator understands."""
    if not isinstance(definition, dict):
        raise RuleError("Définition invalide")
    # "conditions" (one group, rules saved before "or" existed) or "groups" (or between groups).
    groups = definition.get("groups")
    if groups is None:
        groups = [definition.get("conditions")]
    if not isinstance(groups, list) or not groups or not all(isinstance(g, list) and g for g in groups):
        raise RuleError("Ajoutez au moins une condition")
    if len(groups) > MAX_GROUPS:
        raise RuleError(f"{MAX_GROUPS} groupes « ou » au maximum")
    clean_groups = []
    for number, conditions in enumerate(groups, start=1):
        prefix = f"Groupe {number}, " if len(groups) > 1 else ""
        if len(conditions) > MAX_CONDITIONS:
            raise RuleError(f"{prefix}{MAX_CONDITIONS} conditions au maximum".capitalize())
        clean = []
        for index, condition in enumerate(conditions, start=1):
            where = f"{prefix}condition {index}".capitalize()
            if not isinstance(condition, dict):
                raise RuleError(f"{where} invalide")
            field, op, value = condition.get("field"), condition.get("op"), condition.get("value")
            spec = FIELDS.get(field)
            if spec is None:
                raise RuleError(f"{where} : critère inconnu")
            if op not in OPERATORS[spec["kind"]]:
                raise RuleError(f"{where} ({spec['label']}) : opérateur invalide")
            clean.append({"field": field, "op": op, "value": _validate_value(spec, value, where)})
        clean_groups.append(clean)
    try:
        risk = int(definition.get("risk", 60))
        cooldown = int(definition.get("cooldown_hours", 24))
    except (TypeError, ValueError) as exc:
        raise RuleError("Risque et délai doivent être des nombres") from exc
    if not 1 <= risk <= 100:
        raise RuleError("Le risque doit être entre 1 et 100")
    if not 1 <= cooldown <= 24 * 30:
        raise RuleError("Le délai entre deux alertes doit être entre 1 h et 30 jours")
    channel = definition.get("channel", "alertes")
    if channel not in CHANNELS:
        raise RuleError("Canal de notification inconnu")
    return {"groups": clean_groups, "risk": risk, "cooldown_hours": cooldown, "channel": channel}


def groups_of(definition: Dict[str, Any]) -> List[List[Dict[str, Any]]]:
    """Condition groups of a (normalised or legacy) definition."""
    return definition.get("groups") or [definition.get("conditions") or []]


def _validate_value(spec: Dict[str, Any], value: Any, index: Any) -> Any:
    label = spec["label"]
    kind = spec["kind"]
    if kind == "bool":
        if not isinstance(value, bool):
            raise RuleError(f"{index} ({label}) : oui ou non attendu")
        return value
    if kind == "number":
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise RuleError(f"{index} ({label}) : nombre attendu") from exc
        if not (math.isfinite(number) and spec["min"] <= number <= spec["max"]):
            raise RuleError(f"{index} ({label}) : entre {spec['min']} et {spec['max']} {spec['unit']}")
        return number
    if kind == "choice":
        if not isinstance(value, list) or not value or len(value) > MAX_LIST:
            raise RuleError(f"{index} ({label}) : choisissez au moins une valeur")
        unknown = [v for v in value if v not in spec["options"]]
        if unknown:
            raise RuleError(f"{index} ({label}) : valeur inconnue « {unknown[0]} »")
        return sorted(set(value))
    if kind == "text":
        text = str(value or "").strip()
        if not 2 <= len(text) <= 40:
            raise RuleError(f"{index} ({label}) : 2 à 40 caractères")
        return text
    if kind == "zone":
        from app.soc import zones

        known = {z["id"] for z in zones.list_zones()}
        if not isinstance(value, list) or not value or len(value) > MAX_LIST:
            raise RuleError(f"{index} ({label}) : choisissez au moins une zone")
        try:
            ids = sorted({int(v) for v in value})
        except (TypeError, ValueError) as exc:
            raise RuleError(f"{index} ({label}) : zone invalide") from exc
        if any(i not in known for i in ids):
            raise RuleError(f"{index} ({label}) : zone inexistante (supprimée ?)")
        return ids
    if kind == "circle":
        try:
            lat, lon, radius = float(value["lat"]), float(value["lon"]), float(value["radius_km"])
        except (TypeError, ValueError, KeyError) as exc:
            raise RuleError(f"{index} ({label}) : latitude, longitude et rayon attendus") from exc
        if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0.1 <= radius <= 500):
            raise RuleError(f"{index} ({label}) : coordonnées ou rayon (0,1 à 500 km) invalides")
        return {"lat": round(lat, 5), "lon": round(lon, 5), "radius_km": round(radius, 2)}
    raise RuleError(f"{index} : type inconnu")


# --------------------------------------------------------------------------- plain language


def condition_text(condition: Dict[str, Any]) -> str:
    field, op, value = condition["field"], condition["op"], condition["value"]
    spec = FIELDS[field]
    if spec["kind"] == "bool":
        phrases = {
            "foreign": ("est sous pavillon étranger", "est sous pavillon français"),
            "fishing": ("est un navire de pêche", "n'est pas un navire de pêche"),
            "in_mpa": ("se trouve dans une aire marine protégée", "est hors des aires marines protégées"),
            "night": ("est observé de nuit", "est observé de jour"),
            "licensed": ("dispose d'une licence", "n'a pas de licence (ni VMS ni liste des autorisés)"),
            "watchlisted": ("est sur la liste de surveillance", "n'est pas sur la liste de surveillance"),
            "sanctioned": ("est sous sanctions internationales", "n'est pas sous sanctions"),
        }
        yes, no = phrases[field]
        return yes if value else no
    if spec["kind"] == "number":
        if field == "speed":
            return f"navigue à {'moins' if op == 'lt' else 'plus'} de {value:g} nœuds"
        return f"est silencieux à l'AIS depuis {'moins' if op == 'lt' else 'plus'} de {value:g} h"
    if spec["kind"] == "choice":
        names = ", ".join(spec["options"][v] for v in value)
        if field == "zone":
            return f"se trouve {'en' if op == 'in' else 'hors de'} : {names}"
        if field == "flag":
            return f"bat pavillon {'de' if op == 'in' else 'autre que'} : {names}"
        return f"est de type {'' if op == 'in' else 'autre que '}{names}".replace("type  ", "type ")
    if spec["kind"] == "text":
        return f"a un nom contenant « {value} »"
    if spec["kind"] == "zone":
        from app.soc import zones

        names = {z["id"]: z["name"] for z in zones.list_zones()}
        listed = ", ".join(f"« {names.get(i, f'zone {i}')} »" for i in value)
        return f"se trouve {'dans' if op == 'in' else 'hors de'} la zone {listed}"
    where = "à l'intérieur" if op == "inside" else "à l'extérieur"
    return f"se trouve {where} d'un cercle de {value['radius_km']:g} km autour de {value['lat']:.4f}, {value['lon']:.4f}"


def sentence(definition: Dict[str, Any]) -> str:
    """"Si un navire … et … alors …" — the rule as an operator reads it."""
    from app.soc.incidents import severity_for

    groups = groups_of(definition)
    parts = [" et ".join(condition_text(c) for c in group) for group in groups]
    conditions = parts[0] if len(parts) == 1 else " ou ".join(f"({p})" for p in parts)
    return (f"Si un navire {conditions}, alors ouvrir un incident {severity_for(definition['risk'])} "
            f"(risque {definition['risk']}) signalé sur #{definition['channel']}, "
            f"au plus une fois par navire toutes les {definition['cooldown_hours']} h.")


# --------------------------------------------------------------------------- evaluation


class Context:
    """Lookups shared by every vessel of one evaluation (built once, lazily)."""

    def __init__(self, cfg: Settings, now: datetime.datetime) -> None:
        self.cfg, self.now = cfg, now
        self._mpa: Any = None
        self._licensed: Optional[set] = None
        self._authorized: Any = None
        self._watch: Any = None

    def mpa(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        if self._mpa is None:
            from app.sources.wdpa import MpaIndex

            try:
                self._mpa = MpaIndex(settings=self.cfg)
            except Exception:
                self._mpa = False
        return self._mpa.find(lat, lon) if self._mpa else None

    def licensed(self, vessel: Dict[str, Any]) -> bool:
        if self._licensed is None:
            from app.soc.authorized import AuthorizedMatcher
            from app.sources.vms import licensed_mmsis

            self._licensed, self._authorized = licensed_mmsis(), AuthorizedMatcher(self.cfg)
        return vessel["mmsi"] in self._licensed or bool(
            self._authorized.match(mmsi=vessel["mmsi"], imo=vessel.get("imo"), call_sign=vessel.get("call_sign")))

    def watchlisted(self, vessel: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if self._watch is None:
            from app.soc.watchlist import Matcher

            self._watch = Matcher(self.cfg)
        from app.soc.watchlist import summary

        return summary(self._watch.match(mmsi=vessel["mmsi"], imo=vessel.get("imo"), name=vessel.get("name"),
                                         call_sign=vessel.get("call_sign")))


def _flag(vessel: Dict[str, Any]) -> Optional[str]:
    return vessel.get("flag") or flag_name(vessel.get("gfw_flag"))


def _observe(field: str, vessel: Dict[str, Any], ctx: Context) -> Tuple[Any, str]:
    """(value used by the test, how it reads for an operator)."""
    from app.soc.rules import _fishing, _foreign
    from app.sources import ais_store

    if field == "zone":
        zone = vessel.get("last_zone") or "outside"
        return zone, ZONES.get(zone, zone)
    if field == "foreign":
        value = _foreign(vessel)
        return value, f"pavillon {_flag(vessel) or 'inconnu'}"
    if field == "flag":
        flag = _flag(vessel)
        return flag, flag or "inconnu"
    if field == "ship_type":
        t = vessel.get("ship_type")
        return t, f"type AIS {t}" if t is not None else "type non déclaré"
    if field == "fishing":
        value = _fishing(vessel)
        return value, "pêche" if value else f"type AIS {vessel.get('ship_type') or 'non déclaré'}"
    if field == "speed":
        sog = vessel.get("last_sog")
        return sog, f"{sog} nd" if sog is not None else "vitesse inconnue"
    if field == "silent_hours":
        seen = ais_store.parse_ts(vessel.get("last_seen"))
        hours = (ctx.now - seen).total_seconds() / 3600 if seen else None
        return hours, f"{hours:.1f} h" if hours is not None else "—"
    if field == "in_mpa":
        area = ctx.mpa(vessel["last_lat"], vessel["last_lon"]) if vessel.get("last_lat") is not None else None
        return bool(area), area["name"] if area else "hors aire protégée"
    if field == "area":
        return (vessel.get("last_lat"), vessel.get("last_lon")), "position"  # completed with the distance in evaluate()
    if field == "night":
        seen = ais_store.parse_ts(vessel.get("last_seen")) or ctx.now
        hour = ((seen.hour + seen.minute / 60 + (vessel.get("last_lon") or -53) / 15) % 24 + 24) % 24
        night = hour >= 18.5 or hour < 6
        return night, f"{int(hour):02d} h {int(hour % 1 * 60):02d} heure solaire locale"
    if field == "licensed":
        value = ctx.licensed(vessel)
        return value, "licence / VMS" if value else "aucune licence connue"
    if field == "watchlisted":
        hit = ctx.watchlisted(vessel)
        return bool(hit), f"{hit['source']}" if hit else "non listé"
    if field == "sanctioned":
        try:
            intel = json.loads(vessel.get("intel") or "{}")
        except ValueError:
            intel = {}
        matches = intel.get("sanctions") or []
        return bool(matches), matches[0]["caption"] if matches else "aucune correspondance connue"
    if field == "name":
        return vessel.get("name") or "", vessel.get("name") or "sans nom"
    if field == "drawn_zone":
        from app.soc import zones

        if vessel.get("last_lat") is None:
            return [], "position inconnue"
        inside = zones.containing(vessel["last_lon"], vessel["last_lat"])
        return [z["id"] for z in inside], ", ".join(z["name"] for z in inside) or "hors des zones dessinées"
    raise RuleError(f"Critère inconnu : {field}")


def _holds(condition: Dict[str, Any], value: Any) -> bool:
    from app.core.geo import haversine_km

    field, op, expected = condition["field"], condition["op"], condition["value"]
    kind = FIELDS[field]["kind"]
    if kind == "bool":
        return bool(value) == expected
    if kind == "number":
        if value is None:
            return False
        return value < expected if op == "lt" else value > expected
    if kind == "choice":
        if field == "ship_type":
            hit = any(SHIP_TYPES[k][1](value) for k in expected)
        elif field == "flag":
            hit = value in expected or (value is not None and is_french(value) and any(is_french(v) for v in expected))
        else:
            hit = value in expected
        return hit if op == "in" else not hit
    if kind == "text":
        return expected.lower() in str(value).lower()
    if kind == "zone":
        hit = any(zone_id in expected for zone_id in value)
        return hit if op == "in" else not hit
    lat, lon = value
    if lat is None or lon is None:
        return False
    inside = haversine_km(lat, lon, expected["lat"], expected["lon"]) <= expected["radius_km"]
    return inside if op == "inside" else not inside


def evaluate(definition: Dict[str, Any], vessel: Dict[str, Any], ctx: Context) -> Tuple[bool, List[Dict[str, Any]]]:
    """(one group fully holds, its conditions with the observed values).

    When no group holds, the checks of the group that went furthest are returned
    (it is the one worth showing as a "near miss").
    """
    best: List[Dict[str, Any]] = []
    for group in groups_of(definition):
        ok, checks = _evaluate_group(group, vessel, ctx)
        if ok:
            return True, checks
        if sum(c["ok"] for c in checks) >= sum(c["ok"] for c in best) and len(checks) >= len(best):
            best = checks
    return False, best


def _evaluate_group(group: List[Dict[str, Any]], vessel: Dict[str, Any], ctx: Context) -> Tuple[bool, List[Dict[str, Any]]]:
    """(all conditions of the group hold, each with the observed value). Stops at the first failure."""
    from app.core.geo import haversine_km

    checks = []
    for condition in group:
        value, observed = _observe(condition["field"], vessel, ctx)
        ok = _holds(condition, value)
        if condition["field"] == "area" and None not in value:
            centre = condition["value"]
            observed = f"à {haversine_km(value[0], value[1], centre['lat'], centre['lon']):.1f} km du centre"
        checks.append({"condition": condition_text(condition), "observed": observed, "ok": ok})
        if not ok:
            return False, checks
    return True, checks


def _population(definition: Dict[str, Any], vessels: List[Dict[str, Any]], now: datetime.datetime) -> List[Dict[str, Any]]:
    """Vessels heard in the last hour — or 48 h when the rule looks at AIS silence."""
    from app.sources import ais_store

    window = 48 if any(c["field"] == "silent_hours" for g in groups_of(definition) for c in g) else 1
    cutoff = now - datetime.timedelta(hours=window)
    return [v for v in vessels if v.get("last_lat") is not None
            and (ais_store.parse_ts(v.get("last_seen")) or cutoff) > cutoff]


# --------------------------------------------------------------------------- storage


def _row(record: Dict[str, Any]) -> Dict[str, Any]:
    definition = json.loads(record["definition"])
    return {
        "id": record["id"], "name": record["name"], "description": record["description"],
        "enabled": bool(record["enabled"]), "definition": definition, "sentence": sentence(definition),
        "kind": f"custom:{record['id']}", "created_at": record["created_at"], "created_by": record["created_by"],
        "updated_at": record["updated_at"], "updated_by": record["updated_by"],
    }


def list_rules(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    from app.soc.rule_catalog import performance

    with read_db(settings) as conn:
        records = rows(conn.execute("SELECT * FROM custom_rules ORDER BY id").fetchall())
    return [{**_row(r), "performance": performance([f"custom:{r['id']}"], settings=settings)} for r in records]


def _clean_text(name: Any, description: Any) -> Tuple[str, str]:
    name = str(name or "").strip()
    if not 3 <= len(name) <= 80:
        raise RuleError("Le nom doit faire entre 3 et 80 caractères")
    return name, str(description or "").strip()[:1000]


def create(payload: Dict[str, Any], actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    name, description = _clean_text(payload.get("name"), payload.get("description"))
    definition = validate(payload.get("definition") or {})
    now = _now().isoformat()
    with db(settings) as conn:
        rule_id = conn.execute(
            "INSERT INTO custom_rules (name, description, enabled, definition, created_at, created_by, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (name, description, 1 if payload.get("enabled", True) else 0, json.dumps(definition), now, actor, now, actor),
        ).fetchone()[0]
    audit.record("rules.create", actor=actor, target=f"rule:custom:{rule_id}",
                 details={"name": name, "sentence": sentence(definition)}, settings=settings)
    return get(rule_id, settings)


def get(rule_id: int, settings: Optional[Settings] = None) -> Dict[str, Any]:
    with read_db(settings) as conn:
        record = conn.execute("SELECT * FROM custom_rules WHERE id = ?", (rule_id,)).fetchone()
    if record is None:
        raise KeyError(rule_id)
    return _row(dict(record))


def update(rule_id: int, payload: Dict[str, Any], actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    current = get(rule_id, settings)
    name, description = _clean_text(payload.get("name", current["name"]), payload.get("description", current["description"]))
    definition = validate(payload["definition"]) if "definition" in payload else current["definition"]
    enabled = bool(payload.get("enabled", current["enabled"]))
    with db(settings) as conn:
        conn.execute(
            "UPDATE custom_rules SET name = ?, description = ?, enabled = ?, definition = ?, updated_at = ?, updated_by = ? "
            "WHERE id = ?",
            (name, description, 1 if enabled else 0, json.dumps(definition), _now().isoformat(), actor, rule_id),
        )
    audit.record("rules.update", actor=actor, target=f"rule:custom:{rule_id}",
                 details={"name": name, "enabled": enabled, "sentence": sentence(definition)}, settings=settings)
    return get(rule_id, settings)


def delete(rule_id: int, actor: str, settings: Optional[Settings] = None) -> None:
    current = get(rule_id, settings)
    with db(settings) as conn:
        conn.execute("DELETE FROM custom_rules WHERE id = ?", (rule_id,))
    audit.record("rules.delete", actor=actor, target=f"rule:custom:{rule_id}",
                 details={"name": current["name"], "sentence": current["sentence"]}, settings=settings)


# --------------------------------------------------------------------------- run & preview


def preview(definition: Dict[str, Any], at: Optional[datetime.datetime] = None,
            settings: Optional[Settings] = None, limit: int = 50) -> Dict[str, Any]:
    """Which vessels the rule would catch at ``at`` (now when None), with every condition explained.

    Nothing is created. Also returns the closest misses (all conditions but one),
    which helps to adjust a threshold.
    """
    from app.sources import ais_store

    cfg = settings or get_settings()
    definition = validate(definition)
    now = at or _now()
    zone_of = None
    if at is not None:
        from app.sources.marine_regions import classify_zone, zone_geometries

        zones = zone_geometries(cfg)
        zone_of = lambda lon, lat: classify_zone(lon, lat, zones)  # noqa: E731
    vessels = ais_store.picture_at(at, 48, cfg, zone_of=zone_of)
    population = _population(definition, vessels, now)
    ctx = Context(cfg, now)
    matches, near = [], []
    for vessel in population:
        ok, checks = evaluate(definition, vessel, ctx)
        item = {"mmsi": vessel["mmsi"], "name": vessel.get("name") or "", "flag": _flag(vessel),
                "lat": vessel["last_lat"], "lon": vessel["last_lon"], "last_seen": vessel["last_seen"], "checks": checks}
        if ok:
            matches.append(item)
        elif checks and sum(not c["ok"] for c in checks) == 1 and any(len(checks) == len(g) for g in groups_of(definition)):
            near.append(item)  # a whole group read, failed only on its last condition
    return {
        "sentence": sentence(definition),
        "evaluated": len(population),
        "matches": matches[:limit],
        "match_count": len(matches),
        "near_misses": near[:10],
        "at": now.isoformat(),
        "mode": "replay" if at else "live",
    }


def run(vessels: List[Dict[str, Any]], cfg: Settings) -> Dict[str, int]:
    """Evaluate every enabled custom rule on the live picture; open incidents for the matches."""
    from app.soc import rule_catalog
    from app.soc.rules import _open

    now = _now()
    ctx = Context(cfg, now)
    counts: Dict[str, int] = {}
    for rule in list_rules(cfg):
        if not rule["enabled"]:
            continue
        definition = rule["definition"]
        created = 0
        bucket = int(now.timestamp() // (definition["cooldown_hours"] * 3600))
        for vessel in _population(definition, vessels, now):
            ok, checks = evaluate(definition, vessel, ctx)
            if not ok:
                continue
            why = rule_catalog.explanation(rule["kind"], checks, {"règle": rule["sentence"]}, label=rule["name"])
            why["summary"] = rule["description"] or rule["sentence"]
            if _open(
                vessel, kind=rule["kind"], risk=definition["risk"], channel=definition["channel"],
                seen_at=vessel["last_seen"], key=f"cr{rule['id']}-{vessel['mmsi']}-{bucket}",
                title=f"{rule['name']} : {vessel.get('name') or vessel['mmsi']}",
                headline=f"Règle sur mesure « {rule['name']} »",
                extra=[{"title": "Règle", "value": rule["sentence"], "short": False}],
                cfg=cfg, explanation=why,
            ):
                created += 1
        counts[f"custom_{rule['id']}"] = created
    return counts


def catalogue() -> Dict[str, Any]:
    """Fields and operators for the rule builder (labels, help, options, units)."""
    from app.soc import zones

    zone_options = [(z["id"], z["name"]) for z in zones.list_zones()]

    def options(key: str, spec: Dict[str, Any]) -> Any:
        return zone_options if key == "drawn_zone" else list(spec["options"].items())

    return {
        "fields": [{"id": key, **{k: (options(key, spec) if k == "options" else v) for k, v in spec.items()},
                    "operators": list(OPERATORS[spec["kind"]].items())} for key, spec in FIELDS.items()],
        "channels": list(CHANNELS.items()),
        "limits": {"conditions": MAX_CONDITIONS, "groups": MAX_GROUPS},
    }
