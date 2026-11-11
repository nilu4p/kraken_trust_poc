"""Global Fishing Watch 4Wings reports and vessel identity (free GFW token).

- ``public-global-sar-presence``: vessel detections GFW derived from
  Sentinel-1, each matched (or not) to AIS. Used to cross-validate our own
  detections and to map recurrent "dark" activity (≈5-day latency).
- ``public-global-fishing-effort``: AIS apparent fishing hours by flag
  (≈96-hour latency), shown as a heat layer and used as risk context.
- Vessel identity: registry / self-reported flag and gear type of an MMSI,
  used by the 24/7 foreign-fishing rule.
- Area report: the same effort summed over the whole EEZ for a chosen period, by
  day, flag, gear type and vessel (the "activity" report of the GFW map).
- Vessel insights: fused IUU risk indicators of a vessel (RFMO IUU listing,
  apparent fishing in no-take MPAs or without known RFMO authorisation,
  AIS-off events, flag changes) over the last 12 months.

GFW allows one concurrent report per user (429 otherwise): calls are
serialised and cached on disk.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Dict, Iterable, List, Optional

import httpx

from app.config import Settings, get_settings
from app.sources import health
from app.sources.mid import flag_name, is_french_flag
from app.sources.http import USER_AGENT, cache_read, cache_read_stale, cache_write

logger = logging.getLogger(__name__)

API = "https://gateway.api.globalfishingwatch.org/v3"
SAR_DATASET = "public-global-sar-presence:latest"
EFFORT_DATASET = "public-global-fishing-effort:latest"
IDENTITY_DATASET = "public-global-vessel-identity:latest"
_report_lock = threading.Lock()


def _headers(cfg: Settings) -> Dict[str, str]:
    return {"Authorization": f"Bearer {cfg.gfw_token}", "User-Agent": USER_AGENT}


def _rows(payload: Dict[str, Any]) -> Iterable[Dict[str, Any]]:
    """4Wings JSON: ``entries`` = [ {"<dataset:version>": [row, ...]}, ... ]."""
    for entry in payload.get("entries") or []:
        if isinstance(entry, dict):
            for value in entry.values():
                if isinstance(value, list):
                    yield from (r for r in value if isinstance(r, dict))


def _report(dataset: str, group_by: str, resolution: str, temporal: str, days: int, cfg: Settings,
            end: Optional[datetime.date] = None) -> Optional[List[Dict[str, Any]]]:
    """Report over ``[end - days, end]`` (end = today when live): nothing after the reference day."""
    end = min(end or datetime.date.today(), datetime.date.today())
    return _report_range(dataset, group_by, resolution, temporal, end - datetime.timedelta(days=days), end, cfg)


def _report_range(dataset: str, group_by: str, resolution: str, temporal: str, start: datetime.date,
                  end: datetime.date, cfg: Settings, aggregate: bool = False) -> Optional[List[Dict[str, Any]]]:
    """4Wings report over the EEZ between two days; ``aggregate`` sums the whole zone (no grid cells)."""
    if not cfg.gfw_token:
        return None
    key = f"{dataset}|{group_by}|{resolution}|{temporal}|{start}|{end}|{cfg.eez_mrgid}" + ("|zone" if aggregate else "")
    cached = cache_read("gfw_4wings", key, 12 * 3600, cfg)
    if cached is not None:
        return cached
    params = {
        "spatial-resolution": resolution,
        "temporal-resolution": temporal,
        "group-by": group_by,
        "datasets[0]": dataset,
        "date-range": f"{start},{end}",
        "format": "JSON",
        "region-id": cfg.eez_mrgid,
        "region-dataset": "public-eez-areas",
    }
    if aggregate:
        params["spatial-aggregation"] = "true"
    with _report_lock:  # GFW: one running report per user
        try:
            response = httpx.get(f"{API}/4wings/report", params=params, headers=_headers(cfg), timeout=120)
        except Exception as exc:
            health.record("gfw_4wings", False, type(exc).__name__)
            return cache_read_stale("gfw_4wings", key, cfg)
    if response.status_code >= 400:
        health.record("gfw_4wings", False, f"HTTP {response.status_code}")
        logger.warning("GFW 4Wings %s -> HTTP %s: %s", dataset, response.status_code, response.text[:200])
        return cache_read_stale("gfw_4wings", key, cfg)
    health.record("gfw_4wings", True)
    rows = list(_rows(response.json()))
    cache_write("gfw_4wings", key, rows, cfg)
    return rows


def sar_detections(days: int = 30, settings: Optional[Settings] = None, end: Optional[datetime.date] = None) -> Dict[str, Any]:
    """GFW Sentinel-1 vessel detections in the EEZ over the ``days`` up to ``end`` (matched / unmatched to AIS)."""
    cfg = settings or get_settings()
    rows = _report(SAR_DATASET, "VESSEL_ID", "HIGH", "DAILY", days, cfg, end)
    if rows is None:
        return {"type": "FeatureCollection", "features": [], "available": False,
                "reason": "GFW_TOKEN non configuré" if not cfg.gfw_token else "API GFW indisponible"}
    features = []
    for r in rows:
        if r.get("lat") is None or r.get("lon") is None:
            continue
        matched = bool(r.get("vesselId") or r.get("mmsi") or r.get("vesselIDs"))
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
                "properties": {
                    "type": "gfw_sar",
                    "date": str(r.get("date") or "")[:10],
                    "detections": r.get("detections") or 1,
                    "matched_ais": matched,
                    "mmsi": r.get("mmsi"),
                    "vessel_name": r.get("shipName"),
                    "flag": flag_name(r.get("flag")),
                },
            }
        )
    return {
        "type": "FeatureCollection",
        "features": features,
        "available": True,
        "unmatched": sum(1 for f in features if not f["properties"]["matched_ais"]),
        "source": "Global Fishing Watch (Sentinel-1)",
    }


def fishing_effort(days: int = 30, settings: Optional[Settings] = None, end: Optional[datetime.date] = None) -> Dict[str, Any]:
    """AIS apparent fishing hours by flag on a coarse grid (heat layer), over the ``days`` up to ``end``."""
    cfg = settings or get_settings()
    rows = _report(EFFORT_DATASET, "FLAG", "LOW", "ENTIRE", days, cfg, end)
    if rows is None:
        return {"type": "FeatureCollection", "features": [], "available": False,
                "reason": "GFW_TOKEN non configuré" if not cfg.gfw_token else "API GFW indisponible"}
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(r["lon"]), float(r["lat"])]},
            "properties": {"type": "gfw_effort", "hours": round(float(r.get("hours") or 0), 1), "flag": r.get("flag"),
                           "foreign": bool(r.get("flag")) and not is_french_flag(r.get("flag"))},
        }
        for r in rows
        if r.get("lat") is not None and r.get("lon") is not None and float(r.get("hours") or 0) > 0
    ]
    by_flag: Dict[str, float] = {}
    for f in features:
        flag = f["properties"]["flag"] or "?"
        by_flag[flag] = round(by_flag.get(flag, 0) + f["properties"]["hours"], 1)
    return {"type": "FeatureCollection", "features": features, "available": True, "hours_by_flag": by_flag,
            "source": "Global Fishing Watch (effort de pêche AIS)"}


GEAR_LABELS = {
    "trawlers": "Chalutiers",
    "drifting_longlines": "Palangriers dérivants",
    "set_longlines": "Palangriers calés",
    "set_gillnets": "Fileyeurs",
    "purse_seines": "Senneurs",
    "tuna_purse_seines": "Senneurs thoniers",
    "other_purse_seines": "Senneurs (autres)",
    "other_seines": "Sennes (autres)",
    "seiners": "Senneurs",
    "pole_and_line": "Canneurs",
    "trollers": "Ligneurs à la traîne",
    "squid_jigger": "Turluttiers (calmar)",
    "pots_and_traps": "Casiers et nasses",
    "fixed_gear": "Engins fixes",
    "dredge_fishing": "Dragueurs",
    "fishing": "Pêche (engin non précisé)",
    "inconclusive": "Engin indéterminé",
}
REPORT_MAX_DAYS = 366  # GFW 4Wings: one year per report at most
TOP_VESSELS = 50


def _hours(row: Dict[str, Any]) -> float:
    try:
        return float(row.get("hours") or 0)
    except (TypeError, ValueError):
        return 0.0


def activity_report(start: datetime.date, end: datetime.date, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Apparent fishing activity (AIS) of the whole EEZ between two days, like the GFW area report.

    Three zone-wide 4Wings reports: hours per day and flag (time series), per gear type
    and per vessel. Foreign = any flag outside France and its overseas territories.
    """
    cfg = settings or get_settings()
    end = min(end, datetime.date.today())
    period = {"start": str(start), "end": str(end), "days": (end - start).days}
    if not cfg.gfw_token:
        return {"available": False, "reason": "GFW_TOKEN non configuré", "period": period}
    temporal = "DAILY" if period["days"] <= 120 else "MONTHLY"
    daily = _report_range(EFFORT_DATASET, "FLAG", "LOW", temporal, start, end, cfg, aggregate=True)
    if daily is None:
        return {"available": False, "reason": "API GFW indisponible", "period": period}
    gears = _report_range(EFFORT_DATASET, "GEARTYPE", "LOW", "ENTIRE", start, end, cfg, aggregate=True) or []
    vessels = _report_range(EFFORT_DATASET, "VESSEL_ID", "LOW", "ENTIRE", start, end, cfg, aggregate=True) or []

    by_flag: Dict[str, float] = {}
    series: Dict[str, Dict[str, float]] = {}
    for row in daily:
        hours = _hours(row)
        if hours <= 0:
            continue
        code = str(row.get("flag") or "").upper()
        by_flag[code] = by_flag.get(code, 0.0) + hours
        bucket = series.setdefault(str(row.get("date") or "")[:10], {"french": 0.0, "foreign": 0.0, "unknown": 0.0})
        bucket["unknown" if not code else "french" if is_french_flag(code) else "foreign"] += hours
    total = sum(by_flag.values())
    foreign = sum(h for code, h in by_flag.items() if code and not is_french_flag(code))

    by_gear: Dict[str, float] = {}
    for row in gears:
        gear = str(row.get("geartype") or row.get("gearType") or "inconclusive").lower()
        by_gear[gear] = by_gear.get(gear, 0.0) + _hours(row)

    per_vessel: Dict[str, Dict[str, Any]] = {}
    for row in vessels:
        hours = _hours(row)
        key = str(row.get("vesselId") or row.get("mmsi") or row.get("shipName") or "")
        if hours <= 0 or not key:
            continue
        entry = per_vessel.setdefault(key, {
            "gfw_id": row.get("vesselId"),
            "mmsi": str(row["mmsi"]) if row.get("mmsi") else None,
            "name": row.get("shipName") or row.get("shipname"),
            "flag_code": (row.get("flag") or "").upper() or None,
            "flag": flag_name(row.get("flag")),
            "gear": GEAR_LABELS.get(str(row.get("geartype") or "").lower(), row.get("geartype")),
            "foreign": bool(row.get("flag")) and not is_french_flag(row.get("flag")),
            "hours": 0.0,
        })
        entry["hours"] += hours
    top = sorted(per_vessel.values(), key=lambda v: -v["hours"])[:TOP_VESSELS]
    for vessel in top:
        vessel["hours"] = round(vessel["hours"], 1)

    def share(hours: float) -> float:
        return round(100 * hours / total, 1) if total else 0.0

    return {
        "available": True,
        "period": period,
        "resolution": temporal.lower(),
        "total_hours": round(total, 1),
        "foreign_hours": round(foreign, 1),
        "foreign_share": share(foreign),
        "vessel_count": len(per_vessel),
        "foreign_vessel_count": sum(1 for v in per_vessel.values() if v["foreign"]),
        "by_flag": [
            {"code": code or None, "flag": flag_name(code) if code else "Inconnu", "hours": round(h, 1), "share": share(h),
             "foreign": bool(code) and not is_french_flag(code)}
            for code, h in sorted(by_flag.items(), key=lambda kv: -kv[1])
        ],
        "by_gear": [
            {"gear": gear, "label": GEAR_LABELS.get(gear, gear.replace("_", " ")), "hours": round(h, 1), "share": share(h)}
            for gear, h in sorted(by_gear.items(), key=lambda kv: -kv[1]) if h > 0
        ],
        "series": [{"date": day, **{k: round(v, 1) for k, v in values.items()}} for day, values in sorted(series.items())],
        "vessels": top,
        "region": {"dataset": "public-eez-areas", "id": cfg.eez_mrgid},
        "source": "Global Fishing Watch (effort de pêche AIS, 4Wings)",
    }


def _names(items: Any) -> List[str]:
    out = []
    for item in items or []:
        name = item.get("name") if isinstance(item, dict) else item
        if name:
            out.append(str(name).upper())
    return out


def vessel_identity(mmsi: str, settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    """Flag and gear type known to GFW for an MMSI (cached 7 days); None if unknown / no token."""
    cfg = settings or get_settings()
    if not cfg.gfw_token or not str(mmsi).isdigit():
        return None
    cached = cache_read("gfw_identity", mmsi, 7 * 24 * 3600, cfg)
    if cached is not None:
        return cached or None
    try:
        response = httpx.get(
            f"{API}/vessels/search",
            params={"query": mmsi, "datasets[0]": IDENTITY_DATASET, "limit": 1},
            headers=_headers(cfg),
            timeout=cfg.http_timeout,
        )
    except Exception as exc:
        health.record("gfw_identity", False, type(exc).__name__)
        return None
    if response.status_code >= 400:
        health.record("gfw_identity", False, f"HTTP {response.status_code}")
        return None
    health.record("gfw_identity", True)
    entries = response.json().get("entries") or []
    result: Dict[str, Any] = {}
    if entries:
        entry = entries[0]
        registry = (entry.get("registryInfo") or [{}])[0] or {}
        reported = (entry.get("selfReportedInfo") or [{}])[0] or {}
        combined = (entry.get("combinedSourcesInfo") or [{}])[0] or {}
        gears = _names(combined.get("geartypes")) + _names(registry.get("geartypes"))
        types = _names(combined.get("shiptypes"))
        result = {
            "gfw_id": reported.get("id") or registry.get("id"),
            "flag": registry.get("flag") or reported.get("flag"),
            "name": registry.get("shipname") or reported.get("shipname"),
            "geartypes": sorted(set(gears)),
            "shiptypes": sorted(set(types)),
            "fishing": "FISHING" in types or any(g not in ("", "OTHER", "NON_FISHING") for g in gears),
        }
    cache_write("gfw_identity", mmsi, result, cfg)
    return result or None


INSIGHTS = ["FISHING", "GAP", "COVERAGE", "VESSEL-IDENTITY-IUU-VESSEL-LIST", "VESSEL-IDENTITY-FLAG-CHANGES"]


def _count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        for key in ("totalTimesListed", "total", "events", "count"):
            if isinstance(value.get(key), (int, float)):
                return int(value[key])
        return 0
    return int(value) if isinstance(value, (int, float)) else 0


def parse_insights(payload: Dict[str, Any]) -> Dict[str, Any]:
    fishing = payload.get("apparentFishing") or {}
    gaps = payload.get("gaps") or payload.get("gap") or {}
    identity = payload.get("vesselIdentity") or {}
    coverage = payload.get("coverage") or {}
    iuu = identity.get("iuuVesselList") or {}
    flags = identity.get("flagsChanges") or {}
    gap_counters = gaps.get("periodSelectedCounters") or {}
    return {
        "iuu_listed": _count(iuu.get("totalTimesListed")) > 0 or _count(iuu.get("totalTimesListedInThePeriod")) > 0,
        "iuu_times_listed": _count(iuu.get("totalTimesListed")),
        "fishing_in_no_take_mpa": _count(fishing.get("eventsInNoTakeMpas") or fishing.get("eventsInNoTakeMPAs")),
        "fishing_without_rfmo_authorization": _count(fishing.get("eventsInRfmoWithoutKnownAuthorization")),
        "ais_off_events": _count(gap_counters.get("eventsGapOff") if gap_counters else gaps.get("aisOff")),
        "flag_changes": _count(flags.get("totalTimesListedInThePeriod") or flags.get("totalTimesListed")),
        "ais_coverage_pct": coverage.get("percentage"),
    }


def vessel_insights(gfw_id: str, settings: Optional[Settings] = None, days: int = 365) -> Optional[Dict[str, Any]]:
    """GFW Insights for one vessel (cached 7 days); None if unavailable / no token."""
    cfg = settings or get_settings()
    if not cfg.gfw_token or not gfw_id:
        return None
    cached = cache_read("gfw_insights", gfw_id, 7 * 24 * 3600, cfg)
    if cached is not None:
        return cached or None
    end = datetime.date.today()
    body = {
        "includes": INSIGHTS,
        "startDate": str(end - datetime.timedelta(days=days)),
        "endDate": str(end),
        "vessels": [{"datasetId": IDENTITY_DATASET, "vesselId": gfw_id}],
    }
    try:
        response = httpx.post(f"{API}/insights/vessels", json=body, headers=_headers(cfg), timeout=cfg.http_timeout * 2)
    except Exception as exc:
        health.record("gfw_insights", False, type(exc).__name__)
        return None
    if response.status_code >= 400:
        health.record("gfw_insights", False, f"HTTP {response.status_code}")
        logger.warning("GFW insights %s -> HTTP %s: %s", gfw_id, response.status_code, response.text[:200])
        return None
    health.record("gfw_insights", True)
    result = parse_insights(response.json())
    cache_write("gfw_insights", gfw_id, result, cfg)
    return result
