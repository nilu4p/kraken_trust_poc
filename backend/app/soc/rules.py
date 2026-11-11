"""24/7 detection rules on the continuous AIS picture (between satellite passes).

Rules
-----
``foreign_fishing``  fishing vessel (AIS type 30 or GFW gear) under a foreign flag inside the EEZ
``watchlist_ais``    vessel on the watchlist (Combined IUU list, manual) transmitting inside the EEZ
``ais_gap``          vessel transmitting regularly near the coast that suddenly goes silent
                     (only while the receiver still hears other vessels → not our outage)
``encounter``        two slow vessels within ``encounter_distance_km`` for ``encounter_min_minutes``
                     inside the EEZ (possible transshipment)
``sanctions``        vessel matching a sanctions list (OpenSanctions: OFAC, EU, UN…) inside the EEZ
``watchlist_ais``    also raised when GFW Insights reports an RFMO IUU listing
``mpa_fishing``      fishing vessel moving slowly inside a marine protected area (WDPA)
                     (applies to licensed vessels too: a licence does not cover a reserve)

Fusion: each vessel heard in the EEZ is enriched (bounded per tick, refreshed
weekly) with GFW identity + Insights, the VesselAPI registry record (foreign
vessels) and OpenSanctions screening, stored in
``ais_vessels.intel``. Vessels reported by VMS are licensed: they are not
raised by the foreign-fishing rule, nor are vessels on the authorised list.

Each hit opens or updates an incident correlated *per vessel* and notifies the
SOC. Rules never raise: a failing rule is logged and the others still run.
"""

from __future__ import annotations

import datetime
import json
import logging
import math
import time
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.config import Settings, get_settings
from app.core.geo import haversine_km
from app.db import db
from app.soc import incidents, notify
from app.soc.watchlist import Matcher, summary
from app.sources import ais_store
from app.sources.mid import flag_name, is_french, is_french_flag

logger = logging.getLogger(__name__)

IN_EEZ = ("territorial", "contiguous", "eez")
COASTAL = ("territorial", "contiguous")  # terrestrial AIS coverage: gaps are meaningful here
ZONE_RISK = {"territorial": 85, "contiguous": 70, "eez": 60}
FISHING_SHIP_TYPE = 30
# Ports and anchorages where slow vessels lie side by side for hours for ordinary reasons (waiting
# for a berth, pilotage, bunkering). As Global Fishing Watch does, no encounter is counted within
# 10 km of them.
ANCHORAGES = (
    ("Cayenne – Dégrad des Cannes (rade, chenal du Mahury, Larivot)", 4.90, -52.27),
    ("Kourou (port de Pariacabo, embouchure)", 5.17, -52.63),
    ("Îles du Salut (mouillage)", 5.29, -52.59),
)
ANCHORAGE_EXCLUSION_KM = 10.0
MAX_IDENTITY_LOOKUPS = 10
INTEL_TIME_BUDGET_S = 20.0  # external lookups per tick: the 1-minute loop must stay on time


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(value: datetime.datetime) -> str:
    return value.isoformat()


def _parse(value: Optional[str]) -> Optional[datetime.datetime]:
    return ais_store.parse_ts(value) if value else None


def _foreign(vessel: Dict[str, Any]) -> bool:
    if vessel.get("flag"):
        return not is_french(vessel["flag"])
    gfw_flag = vessel.get("gfw_flag") or ""
    return bool(gfw_flag) and not is_french_flag(gfw_flag)


def _fishing(vessel: Dict[str, Any]) -> bool:
    return vessel.get("ship_type") == FISHING_SHIP_TYPE or bool(vessel.get("gfw_geartype"))


def _describe(vessel: Dict[str, Any]) -> List[Dict[str, Any]]:
    fields = [
        {"title": "Navire", "value": f"{vessel.get('name') or '—'} · MMSI {vessel['mmsi']}", "short": False},
        {"title": "Pavillon", "value": vessel.get("flag") or flag_name(vessel.get("gfw_flag")) or "inconnu", "short": True},
        {"title": "Type AIS", "value": "pêche (30)" if vessel.get("ship_type") == FISHING_SHIP_TYPE else (vessel.get("ship_type") or "—"), "short": True},
        {"title": "Engin (GFW)", "value": vessel.get("gfw_geartype") or "—", "short": True},
        {"title": "Vitesse", "value": f"{vessel['last_sog']} nd" if vessel.get("last_sog") is not None else "—", "short": True},
    ]
    return fields


ZONE_LABELS = {"territorial": "mer territoriale", "contiguous": "zone contiguë", "eez": "ZEE"}


def _check(label: str, observed: Any) -> Dict[str, Any]:
    """One satisfied condition of a rule, with the value actually observed (for "why this alert?")."""
    return {"condition": label, "observed": observed, "ok": True}


def _common_checks(vessel: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        _check("Navire entendu récemment", f"dernière position {str(vessel.get('last_seen') or '')[:16].replace('T', ' ')} Z"
                                           f" via {vessel.get('last_source') or 'aisstream'}"),
        _check("Dans les eaux françaises", ZONE_LABELS.get(vessel.get("last_zone") or "", vessel.get("last_zone") or "—")),
    ]


def _open(
    vessel: Dict[str, Any], *, kind: str, risk: int, title: str, headline: str, channel: str,
    seen_at: str, key: str, extra: Optional[List[Dict[str, Any]]] = None, cfg: Settings,
    explanation: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    record = {
        "id": f"AIS-{vessel['mmsi']}"[:20],
        "lat": vessel["last_lat"],
        "lon": vessel["last_lon"],
        "zone": vessel.get("last_zone"),
        "risk": {"score": risk},
        "quality": {"score": 1.0},
    }
    incident, created = incidents.ingest_detection(
        record, alert_id=key[:120], dossier="", scene_id=None, seen_at=seen_at,
        kind=kind, title=title, same_vessel_only=True, explanation=explanation, settings=cfg,
    )
    if created or incident.get("upgraded_from"):
        notify.notify_incident(incident, headline, cfg, channel=channel, extra=_describe(vessel) + (extra or []))
    return incident if created else None


def _enrich_identity(vessel: Dict[str, Any], cfg: Settings, budget: List[int]) -> None:
    """Fetch flag / gear from GFW once a week per vessel (bounded per tick)."""
    if not cfg.gfw_token or budget[0] <= 0:
        return
    checked = _parse(vessel.get("gfw_checked_at"))
    if checked and _now() - checked < datetime.timedelta(days=7):
        return
    from app.sources.gfw_4wings import vessel_identity

    budget[0] -= 1
    identity = vessel_identity(vessel["mmsi"], cfg) or {}
    gear = ",".join(identity.get("geartypes") or []) or ("FISHING" if identity.get("fishing") else None)
    vessel["gfw_geartype"], vessel["gfw_flag"] = gear, identity.get("flag")
    with db(cfg) as conn:
        conn.execute(
            "UPDATE ais_vessels SET gfw_geartype = ?, gfw_flag = ?, gfw_checked_at = ? WHERE mmsi = ?",
            (gear, identity.get("flag"), _iso(_now()), vessel["mmsi"]),
        )


def rule_presence(vessels: List[Dict[str, Any]], cfg: Settings, off: Tuple[str, ...] = ()) -> Dict[str, int]:
    """foreign_fishing + watchlist_ais on vessels heard in the last hour inside the EEZ."""
    from app.soc.rule_catalog import explanation

    matcher = Matcher(cfg)
    budget = [MAX_IDENTITY_LOOKUPS]
    hour_ago = _now() - datetime.timedelta(hours=1)
    today = _now().strftime("%Y%m%d")
    counts = {"foreign_fishing": 0, "watchlist_ais": 0}
    from app.soc.authorized import AuthorizedMatcher
    from app.sources.vms import licensed_mmsis

    licensed = licensed_mmsis()
    authorized = AuthorizedMatcher(cfg)
    for vessel in vessels:
        seen = _parse(vessel["last_seen"])
        if vessel.get("last_zone") not in IN_EEZ or seen is None or seen < hour_ago:
            continue
        hit = summary(matcher.match(mmsi=vessel["mmsi"], imo=vessel.get("imo"), name=vessel.get("name"), call_sign=vessel.get("call_sign")))
        if hit and "watchlist_ais" not in off:
            if _open(
                vessel, kind="watchlist_ais", risk=90, channel="surveillance", seen_at=vessel["last_seen"],
                key=f"wl-{vessel['mmsi']}-{today}",
                title=f"Navire listé ({hit['source']}) visible à l'AIS : {vessel.get('name') or vessel['mmsi']}",
                headline="Navire de la liste de surveillance dans la ZEE",
                extra=[{"title": "Motif d'inscription", "value": hit.get("reason") or "—", "short": False},
                       {"title": "Correspondance", "value": hit.get("matched_on"), "short": True}],
                cfg=cfg,
                explanation=explanation("watchlist_ais", _common_checks(vessel) + [
                    _check("Sur la liste de surveillance", f"{hit.get('source')} — {hit.get('reason') or 'sans motif'}"),
                    _check("Correspondance sur", hit.get("matched_on") or "—"),
                ]),
            ):
                counts["watchlist_ais"] += 1
        _enrich_identity(vessel, cfg, budget)
        if (
            "foreign_fishing" not in off
            and _fishing(vessel) and _foreign(vessel) and vessel["mmsi"] not in licensed
            and not authorized.match(mmsi=vessel["mmsi"], imo=vessel.get("imo"), call_sign=vessel.get("call_sign"))
        ):
            country = vessel.get("flag") or flag_name(vessel.get("gfw_flag"))
            why = explanation("foreign_fishing", _common_checks(vessel) + [
                _check("Navire de pêche", "type AIS 30" if vessel.get("ship_type") == FISHING_SHIP_TYPE
                       else f"engin GFW : {vessel.get('gfw_geartype')}"),
                _check("Pavillon étranger", country or "inconnu"),
                _check("Sans licence", "absent du VMS et de la liste des navires autorisés"),
            ], {"risque selon la zone": ZONE_RISK})
            if _open(
                vessel, kind="foreign_fishing", risk=ZONE_RISK[vessel["last_zone"]], channel="alertes",
                seen_at=vessel["last_seen"], key=f"ff-{vessel['mmsi']}-{today}",
                title=f"Navire de pêche étranger ({country}) dans la ZEE : {vessel.get('name') or vessel['mmsi']}",
                headline="Navire de pêche sous pavillon étranger dans la ZEE",
                cfg=cfg, explanation=why,
            ):
                counts["foreign_fishing"] += 1
    return counts


def _intel(vessel: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(vessel.get("intel") or "{}")
    except ValueError:
        return {}


def registry_wanted(vessel: Dict[str, Any]) -> bool:
    """24/7 registry lookups (scarce quota) only for foreign vessels fishing or not declaring their type.

    Merchant ships in transit are looked up on demand, when their profile is opened.
    """
    fishing_or_unknown = vessel.get("ship_type") in (None, 30) or bool(vessel.get("gfw_geartype"))
    return fishing_or_unknown and not is_french_flag(vessel.get("flag"))


def apply_registry(vessel: Dict[str, Any], intel: Dict[str, Any], cfg: Settings) -> None:
    """VesselAPI registry record into ``intel["registry"]``; fills a missing IMO / call sign (better sanctions match)."""
    from app.sources.vesselapi import vessel_record

    record = vessel_record(vessel["mmsi"], cfg)
    if record is None:
        return  # unavailable or budget exhausted: retried at the next refresh
    intel["registry"] = record
    if record and (record.get("imo") and not vessel.get("imo") or record.get("call_sign") and not vessel.get("call_sign")
                   or record.get("length_m") and not vessel.get("length_m")):
        ais_store.store_static(vessel["mmsi"], imo=record.get("imo"), call_sign=record.get("call_sign") or "",
                               length_m=record.get("length_m"), settings=cfg)
        vessel["imo"] = vessel.get("imo") or record.get("imo")
        vessel["call_sign"] = vessel.get("call_sign") or record.get("call_sign")


def enrich_intel(vessel: Dict[str, Any], cfg: Settings, budget: List[int]) -> Dict[str, Any]:
    """GFW Insights + VesselAPI registry (foreign vessels) + OpenSanctions, refreshed every ``intel_refresh_days``."""
    intel = _intel(vessel)
    if budget[0] <= 0 or not (cfg.gfw_token or cfg.opensanctions_api_key or cfg.vesselapi_api_key) \
            or not str(vessel["mmsi"]).isdigit():
        return intel
    checked = _parse(vessel.get("intel_checked_at"))
    if checked and _now() - checked < datetime.timedelta(days=cfg.intel_refresh_days):
        return intel
    budget[0] -= 1
    if cfg.gfw_token:
        from app.sources.gfw_4wings import vessel_identity, vessel_insights

        identity = vessel_identity(vessel["mmsi"], cfg) or {}
        if identity.get("gfw_id"):
            intel["gfw_id"] = identity["gfw_id"]
            insights = vessel_insights(identity["gfw_id"], cfg)
            if insights is not None:
                intel["gfw"] = insights
    if cfg.vesselapi_api_key and "registry" not in intel and registry_wanted(vessel):
        apply_registry(vessel, intel, cfg)
    if cfg.opensanctions_api_key:
        from app.sources.opensanctions import screen_vessel

        matches = screen_vessel(name=vessel.get("name") or "", imo=vessel.get("imo"), mmsi=vessel["mmsi"], settings=cfg)
        if matches is not None:
            intel["sanctions"] = matches
    vessel["intel"] = json.dumps(intel)
    with db(cfg) as conn:
        conn.execute("UPDATE ais_vessels SET intel = ?, intel_checked_at = ? WHERE mmsi = ?",
                     (vessel["intel"], _iso(_now()), vessel["mmsi"]))
    return intel


def intel_fields(intel: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Notification fields summarising the fused intelligence of a vessel."""
    fields = []
    gfw = intel.get("gfw") or {}
    if gfw:
        bits = []
        if gfw.get("iuu_listed"):
            bits.append(f"inscrit INN ({gfw.get('iuu_times_listed') or 1}×)")
        if gfw.get("fishing_in_no_take_mpa"):
            bits.append(f"{gfw['fishing_in_no_take_mpa']} pêche(s) en AMP intégrale")
        if gfw.get("fishing_without_rfmo_authorization"):
            bits.append(f"{gfw['fishing_without_rfmo_authorization']} pêche(s) sans autorisation ORGP")
        if gfw.get("ais_off_events"):
            bits.append(f"{gfw['ais_off_events']} coupure(s) AIS")
        if gfw.get("flag_changes"):
            bits.append(f"{gfw['flag_changes']} changement(s) de pavillon")
        fields.append({"title": "GFW Insights (12 mois)", "value": ", ".join(bits) or "aucun indicateur", "short": False})
    for match in (intel.get("sanctions") or [])[:2]:
        fields.append({"title": "Sanctions (OpenSanctions)",
                       "value": f"{match['caption']} — score {match['score']:.2f} — {', '.join(match['datasets'][:3])}",
                       "short": False})
    return fields


def rule_intel(vessels: List[Dict[str, Any]], cfg: Settings, off: Tuple[str, ...] = ()) -> Dict[str, int]:
    """sanctions + GFW IUU listing on vessels heard in the last hour inside the EEZ."""
    from app.soc.rule_catalog import explanation

    budget = [cfg.intel_lookups_per_tick]
    hour_ago = _now() - datetime.timedelta(hours=1)
    today = _now().strftime("%Y%m%d")
    counts = {"sanctions": 0, "iuu_gfw": 0}
    deadline = time.monotonic() + INTEL_TIME_BUDGET_S
    for vessel in vessels:
        if time.monotonic() > deadline:
            budget[0] = 0  # slow providers: finish the tick with cached intelligence only
        seen = _parse(vessel["last_seen"])
        if vessel.get("last_zone") not in IN_EEZ or seen is None or seen < hour_ago:
            continue
        intel = enrich_intel(vessel, cfg, budget)
        name = vessel.get("name") or vessel["mmsi"]
        sanctions = intel.get("sanctions") or []
        if sanctions and "sanctions" not in off and _open(
            vessel, kind="sanctions", risk=85, channel="surveillance", seen_at=vessel["last_seen"],
            key=f"sanc-{vessel['mmsi']}-{today}",
            title=f"Navire sous sanctions internationales dans la ZEE : {name}",
            headline="Correspondance avec une liste de sanctions (OpenSanctions)",
            extra=intel_fields(intel), cfg=cfg,
            explanation=explanation("sanctions", _common_checks(vessel) + [
                _check("Correspondance OpenSanctions", f"{sanctions[0]['caption']} — score {sanctions[0]['score']:.2f} "
                                                      f"({', '.join(sanctions[0]['datasets'][:3])})"),
            ], {"sanctions_min_score": cfg.sanctions_min_score}),
        ):
            counts["sanctions"] += 1
        if (intel.get("gfw") or {}).get("iuu_listed") and "watchlist_ais" not in off and _open(
            vessel, kind="watchlist_ais", risk=90, channel="surveillance", seen_at=vessel["last_seen"],
            key=f"iuu-gfw-{vessel['mmsi']}-{today}",
            title=f"Navire inscrit INN (ORGP, via GFW) visible à l'AIS : {name}",
            headline="Navire de la liste INN des ORGP dans la ZEE (Global Fishing Watch Insights)",
            extra=intel_fields(intel), cfg=cfg,
            explanation=explanation("watchlist_ais", _common_checks(vessel) + [
                _check("Inscrit INN par une ORGP (GFW Insights)",
                       f"{(intel.get('gfw') or {}).get('iuu_times_listed') or 1} inscription(s) sur 12 mois"),
            ]),
        ):
            counts["iuu_gfw"] += 1
    return counts


def rule_mpa(vessels: List[Dict[str, Any]], cfg: Settings) -> int:
    """Fishing vessel moving slowly (fishing speed) inside a marine protected area."""
    from app.sources.wdpa import MpaIndex

    hour_ago = _now() - datetime.timedelta(hours=1)
    candidates = [
        v for v in vessels
        if _fishing(v) and v.get("last_lat") is not None and (_parse(v["last_seen"]) or hour_ago) > hour_ago
        and v.get("last_sog") is not None and v["last_sog"] <= cfg.mpa_max_speed_knots
    ]
    if not candidates:
        return 0
    from app.soc.rule_catalog import explanation

    index = MpaIndex(settings=cfg)
    today = _now().strftime("%Y%m%d")
    created = 0
    for vessel in candidates:
        area = index.find(vessel["last_lat"], vessel["last_lon"])
        if not area:
            continue
        if _open(
            vessel, kind="mpa_fishing", risk=75 if _foreign(vessel) else 65, channel="alertes",
            seen_at=vessel["last_seen"], key=f"mpa-{vessel['mmsi']}-{area.get('site_id')}-{today}",
            title=f"Navire de pêche en aire marine protégée ({area['name']}) : {vessel.get('name') or vessel['mmsi']}",
            headline=f"Activité de pêche probable dans l'aire protégée « {area['name']} »",
            extra=[{"title": "Aire protégée", "value": f"{area['name']} — {area.get('designation') or ''} (UICN {area.get('iucn') or '?'})",
                    "short": False}],
            cfg=cfg,
            explanation=explanation("mpa_fishing", _common_checks(vessel)[:1] + [
                _check("Navire de pêche", "type AIS 30" if vessel.get("ship_type") == FISHING_SHIP_TYPE
                       else f"engin GFW : {vessel.get('gfw_geartype')}"),
                _check("Dans une aire protégée", f"{area['name']} ({area.get('designation') or 'WDPA'})"),
                _check("À vitesse de pêche", f"{vessel['last_sog']} nd ≤ {cfg.mpa_max_speed_knots} nd"),
            ], {"mpa_max_speed_knots": cfg.mpa_max_speed_knots}),
        ):
            created += 1
    return created


def _receiver_alive(cfg: Settings, minutes: int = 30) -> bool:
    since = _iso(_now() - datetime.timedelta(minutes=minutes))
    with db(cfg) as conn:
        return conn.execute("SELECT COUNT(DISTINCT mmsi) FROM ais_positions WHERE ts >= ?", (since,)).fetchone()[0] >= 2


def _fixes_before_silence(candidates: List[Tuple[Dict[str, Any], datetime.datetime, float]], cfg: Settings) -> Dict[str, int]:
    """Per candidate, the fixes of the 6 hours before its last one — in batches of 500 MMSIs."""
    counts: Dict[str, int] = {}
    windows = {v["mmsi"]: (last - datetime.timedelta(hours=6), last) for v, last, _h in candidates}
    mmsis = list(windows)
    for start in range(0, len(mmsis), 500):
        batch = mmsis[start:start + 500]
        low = min(windows[m][0] for m in batch)
        high = max(windows[m][1] for m in batch)
        with db(cfg) as conn:
            fixes = conn.execute(
                f"SELECT mmsi, ts FROM ais_positions WHERE mmsi IN ({','.join('?' * len(batch))}) AND ts BETWEEN ? AND ?",
                (*batch, _iso(low), _iso(high)),
            ).fetchall()
        for mmsi, ts in fixes:
            moment = _parse(ts if isinstance(ts, str) else _iso(ts))
            begin, end = windows[mmsi]
            if moment is not None and begin <= moment <= end:
                counts[mmsi] = counts.get(mmsi, 0) + 1
    return counts


def rule_gaps(vessels: List[Dict[str, Any]], cfg: Settings) -> int:
    """A coastal vessel that transmitted regularly then went silent while others are still heard."""
    if not _receiver_alive(cfg):
        return 0  # our own feed is down: silence means nothing
    now = _now()
    created = 0
    candidates = []
    for vessel in vessels:
        last = _parse(vessel["last_seen"])
        if vessel.get("last_zone") not in COASTAL or last is None or vessel.get("last_source") == "vms":
            continue  # VMS reports hourly: its silence is not an AIS gap
        silence_h = (now - last).total_seconds() / 3600
        if cfg.ais_gap_hours <= silence_h <= 48 and (vessel.get("last_sog") or 0) >= 0.5:
            candidates.append((vessel, last, silence_h))
    regular_counts = _fixes_before_silence(candidates, cfg)  # one query for all candidates, not one each
    for vessel, last, silence_h in candidates:
        regular = regular_counts.get(vessel["mmsi"], 0)
        if regular < 6:
            continue  # sporadic transmitter
        risk = 75 if (_fishing(vessel) or _foreign(vessel)) else 60
        from app.soc.rule_catalog import explanation

        if _open(
            vessel, kind="ais_gap", risk=risk, channel="alertes", seen_at=vessel["last_seen"],
            key=f"gap-{vessel['mmsi']}-{vessel['last_seen']}",
            title=f"Coupure AIS en zone côtière : {vessel.get('name') or vessel['mmsi']}",
            headline=f"Coupure AIS suspecte ({silence_h:.0f} h de silence)",
            extra=[{"title": "Dernier signal", "value": vessel["last_seen"][:16].replace("T", " ") + "Z", "short": True},
                   {"title": "Émissions régulières avant", "value": f"{regular} positions / 6 h", "short": True}],
            cfg=cfg,
            explanation=explanation("ais_gap", [
                _check("En zone côtière (bonne couverture AIS)", ZONE_LABELS.get(vessel.get("last_zone") or "", "—")),
                _check("Émettait régulièrement", f"{regular} positions dans les 6 h avant le silence, {vessel.get('last_sog')} nd"),
                _check("Silence prolongé", f"{silence_h:.1f} h depuis {vessel['last_seen'][:16].replace('T', ' ')} Z ≥ {cfg.ais_gap_hours} h"),
                _check("La réception fonctionne", "d'autres navires entendus dans les 30 dernières minutes"),
            ], {"ais_gap_hours": cfg.ais_gap_hours}),
        ):
            created += 1
    return created


def _close_pairs(points: List[Tuple[str, float, float]], max_km: float) -> List[Tuple[str, str, float, float]]:
    """Pairs of distinct vessels closer than ``max_km`` — spatial grid, O(n) instead of O(n²).

    Cells are at least ``max_km`` wide in both directions (longitude degrees shrink with
    latitude): two close points are always in the same or an adjacent cell.
    """
    if not points:
        return []
    max_lat = min(max(abs(p[1]) for p in points), 85.0)
    cell_deg = max(max_km / 110.57, max_km / (111.32 * math.cos(math.radians(max_lat))), 1e-4) * 1.01
    grid: Dict[Tuple[int, int], List[Tuple[str, float, float]]] = defaultdict(list)
    for item in points:
        grid[(int(item[1] // cell_deg), int(item[2] // cell_deg))].append(item)
    pairs = []
    for (cy, cx), members in grid.items():
        neighbours = [p for dy in (-1, 0, 1) for dx in (-1, 0, 1) for p in grid.get((cy + dy, cx + dx), ())]
        for a, lat_a, lon_a in members:
            for b, lat_b, lon_b in neighbours:
                if a < b and haversine_km(lat_a, lon_a, lat_b, lon_b) <= max_km:
                    pairs.append((a, b, lat_a, lon_a))
    return pairs


def _anchorage(lat: float, lon: float) -> Optional[str]:
    for name, a_lat, a_lon in ANCHORAGES:
        if haversine_km(lat, lon, a_lat, a_lon) <= ANCHORAGE_EXCLUSION_KM:
            return name
    return None


def rule_encounters(cfg: Settings) -> int:
    """Two slow vessels close together for long enough inside the EEZ (possible transshipment)."""
    history = ais_store.tracks(since_hours=3, settings=cfg)
    info = {v["mmsi"]: v for v in ais_store.vessels(since_hours=3, settings=cfg)}
    buckets: Dict[str, List[Tuple[str, float, float]]] = defaultdict(list)
    for mmsi, points in history.items():
        if info.get(mmsi, {}).get("last_zone") not in IN_EEZ:
            continue
        for p in points:
            if (p.get("sog") or 0) <= 2.0:
                bucket = p["ts"][:15]  # 10-minute buckets "YYYY-MM-DDTHH:M"
                buckets[bucket].append((mmsi, p["lat"], p["lon"]))
    together: Dict[Tuple[str, str], int] = defaultdict(int)
    where: Dict[Tuple[str, str], Tuple[float, float]] = {}
    for points in buckets.values():
        seen_pairs = set()
        for a, b, lat_a, lon_a in _close_pairs(points, cfg.encounter_distance_km):
            pair = (a, b) if a < b else (b, a)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            together[pair] += 1
            where[pair] = (lat_a, lon_a)
    created = 0
    needed = max(1, cfg.encounter_min_minutes // 10)
    today = _now().strftime("%Y%m%d")
    for (a, b), count in together.items():
        if count < needed or _anchorage(*where[(a, b)]):
            continue
        first, second = info.get(a, {"mmsi": a}), info.get(b, {"mmsi": b})
        suspicious = _fishing(first) or _fishing(second) or _foreign(first) or _foreign(second)
        vessel = {**first, "last_lat": where[(a, b)][0], "last_lon": where[(a, b)][1]}
        from app.soc.rule_catalog import explanation

        if _open(
            vessel, kind="encounter", risk=70 if suspicious else 50, channel="alertes",
            seen_at=_iso(_now()), key=f"enc-{a}-{b}-{today}",
            title=f"Rendez-vous en mer : {first.get('name') or a} / {second.get('name') or b}",
            headline=f"Rendez-vous en mer ({count * 10} min à moins de {cfg.encounter_distance_km * 1000:.0f} m)",
            extra=[{"title": "Second navire", "value": f"{second.get('name') or '—'} · MMSI {b} · {second.get('flag') or '?'}", "short": False}],
            cfg=cfg,
            explanation={"entities": {"vessels": [a, b]}, **explanation("encounter", [
                _check("Deux navires lents dans la ZEE", f"{first.get('name') or a} et {second.get('name') or b}, ≤ 2 nd"),
                _check("Très proches", f"moins de {cfg.encounter_distance_km * 1000:.0f} m"),
                _check("Hors port et mouillage", f"à plus de {ANCHORAGE_EXCLUSION_KM:.0f} km de Cayenne, Kourou et des îles du Salut"),
                _check("Assez longtemps", f"{count * 10} min ≥ {cfg.encounter_min_minutes} min (3 dernières heures)"),
                _check("Profil", "pêcheur ou pavillon étranger" if suspicious else "aucun pêcheur ni étranger (risque minoré)"),
            ], {"encounter_distance_km": cfg.encounter_distance_km, "encounter_min_minutes": cfg.encounter_min_minutes})},
        ):
            created += 1
    return created


def run_rules(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Run every rule once; returns the counts of new incidents per rule."""
    cfg = settings or get_settings()
    if not cfg.rules_enabled:
        return {}
    from app.soc import custom_rules, rule_catalog

    tick_started = time.monotonic()
    result: Dict[str, Any] = {}
    vessels = ais_store.vessels(since_hours=48, settings=cfg)
    off = tuple(rule_catalog.disabled(cfg))  # rules switched off from the Rules page
    steps: List[Tuple[str, Callable[[], Any]]] = [
        ("presence", lambda: rule_presence(vessels, cfg, off)),
        ("intel", lambda: rule_intel(vessels, cfg, off)),
        ("mpa_fishing", lambda: 0 if "mpa_fishing" in off else rule_mpa(vessels, cfg)),
        ("ais_gap", lambda: 0 if "ais_gap" in off else rule_gaps(vessels, cfg)),
        ("encounter", lambda: 0 if "encounter" in off else rule_encounters(cfg)),
        ("custom", lambda: custom_rules.run(vessels, cfg)),
    ]
    for name, step in steps:
        try:
            outcome = step()
            if isinstance(outcome, dict):
                result.update(outcome)
            else:
                result[name] = outcome
        except Exception as exc:  # one failing rule must not stop the others
            logger.exception("Rule %s failed: %s", name, exc)
            result[name] = "error"
    result["duration_ms"] = round((time.monotonic() - tick_started) * 1000)  # shown on the supervision board
    with db(cfg) as conn:
        conn.execute(
            "INSERT INTO rule_state (key, value, updated_at) VALUES ('last_run', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (json.dumps(result), _iso(_now())),
        )
    return result
