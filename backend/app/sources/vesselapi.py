"""VesselAPI (https://vesselapi.com/) — vessel registry and AIS positions, Bearer key.

Two uses, both bounded by a strict **monthly call budget** (free plan: 150
calls / month, Basic: 1 500 — the provider counts every request):

- **registry** — ``GET /v1/vessel/{mmsi}?filter.idType=mmsi``: IMO, call sign,
  flag, type, build year, size, tonnage, home port and former names. Looked up
  for foreign vessels during the 24/7 intelligence pass (before the sanctions
  screening, so that the IMO improves the match) and when a vessel profile is
  opened; cached 30 days, misses too.
- **positions** (optional third AIS feed) — ``GET /v1/location/vessels/bounding-box``
  over the EEZ every ``vesselapi_poll_minutes`` (0 = off, the default: the
  free quota cannot sustain a feed). Windows are contiguous and capped at the
  4 h the API accepts; fixes join the fused history with ``source="vesselapi"``.
  The feed stops while less than ``REGISTRY_RESERVE`` of the budget is left,
  keeping calls for registry lookups.

The call counter lives in ``rule_state`` (survives restarts, shared by every
process) and is re-synchronised with the ``X-RateLimit-Remaining`` header.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import Settings, get_settings
from app.db import db
from app.sources import health
from app.sources.http import USER_AGENT, cache_read, cache_write

logger = logging.getLogger(__name__)

API = "https://api.vesselapi.com/v1"
SOURCE = "vesselapi"
REGISTRY_TTL = 30 * 24 * 3600
PAGE_SIZE = 50                 # API maximum
MAX_PAGES_PER_POLL = 5         # ≤ 250 fixes per poll: one busy window cannot drain the budget
MAX_WINDOW = datetime.timedelta(hours=4)
REGISTRY_RESERVE = 0.3         # share of the monthly budget the position feed never uses
USAGE_KEY = "vesselapi_usage"


class VesselApiError(RuntimeError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def _rfc3339(value: datetime.datetime) -> str:
    return value.astimezone(datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


# --------------------------------------------------------------------------- budget


def usage(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Calls made this calendar month (UTC), provider-reported remaining quota, calls left in our budget."""
    cfg = settings or get_settings()
    month = _now().strftime("%Y-%m")
    with db(cfg) as conn:
        row = conn.execute("SELECT value FROM rule_state WHERE key = ?", (USAGE_KEY,)).fetchone()
    try:
        state = json.loads(row[0]) if row else {}
    except ValueError:
        state = {}
    if state.get("month") != month:
        state = {"month": month, "calls": 0, "remaining": None}
    left = max(0, cfg.vesselapi_monthly_calls - int(state.get("calls") or 0))
    if isinstance(state.get("remaining"), int):
        left = min(left, state["remaining"])
    return {**state, "budget": cfg.vesselapi_monthly_calls, "left": left}


def _count_call(response: Optional[httpx.Response], cfg: Settings) -> None:
    state = usage(cfg)
    state["calls"] = int(state.get("calls") or 0) + 1
    remaining = response.headers.get("X-RateLimit-Remaining", "") if response is not None else ""
    if remaining.isdigit():
        state["remaining"] = int(remaining)
    elif response is not None and response.status_code == 429:
        state["remaining"] = 0
    value = json.dumps({k: state[k] for k in ("month", "calls", "remaining")})
    with db(cfg) as conn:
        conn.execute(
            "INSERT INTO rule_state (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
            (USAGE_KEY, value, _now().isoformat()),
        )


def _error_message(response: httpx.Response) -> str:
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    code = error.get("code") or ""
    hints = {401: "clé refusée", 402: "crédits insuffisants", 403: "fonction non incluse dans l'offre ou compte suspendu",
             429: "quota mensuel atteint ou trop de requêtes simultanées"}
    return f"HTTP {response.status_code}{f' {code}' if code else ''} — {hints.get(response.status_code, 'erreur VesselAPI')}"


def _get(path: str, params: Dict[str, Any], cfg: Settings, *, reserve: float = 0.0) -> Optional[Dict[str, Any]]:
    """One budgeted GET. None on 404; raises VesselApiError on budget exhaustion / API errors."""
    if usage(cfg)["left"] <= cfg.vesselapi_monthly_calls * reserve:
        raise VesselApiError("budget mensuel VesselAPI épuisé")
    response = None
    try:
        response = httpx.get(
            f"{API}{path}",
            params=params,
            headers={"Authorization": f"Bearer {cfg.vesselapi_api_key}", "User-Agent": USER_AGENT},
            timeout=cfg.http_timeout,
        )
    except Exception as exc:
        health.record(SOURCE, False, type(exc).__name__)
        raise VesselApiError(f"VesselAPI injoignable ({type(exc).__name__})") from exc
    finally:
        _count_call(response, cfg)
    if response.status_code == 404:
        health.record(SOURCE, True)
        return None
    if response.status_code >= 400:
        message = _error_message(response)
        health.record(SOURCE, False, message)
        raise VesselApiError(message)
    health.record(SOURCE, True)
    return response.json()


# --------------------------------------------------------------------------- registry


def _number(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def parse_vessel(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise ``vessel`` from ``/v1/vessel/{id}`` (registry fields shown in the vessel profile)."""
    v = payload.get("vessel") or {}
    imo = str(v.get("imo") or "")
    return {
        "imo": imo if imo.isdigit() and len(imo) == 7 else None,
        "name": (v.get("name") or v.get("name_ais") or "").strip(),
        "call_sign": (v.get("call_sign") or "").strip(),
        "flag": v.get("country") or None,
        "flag_code": v.get("country_code") or None,
        "vessel_type": v.get("vessel_type") or None,
        "vessel_subtype": v.get("vessel_subtype") or None,
        "year_built": v.get("year_built") or None,
        "length_m": _number(v.get("length")),
        "breadth_m": _number(v.get("breadth")),
        "gross_tonnage": _number(v.get("gross_tonnage")),
        "deadweight_t": _number(v.get("deadweight_tonnage")),
        "home_port": v.get("home_port") or None,
        "operating_status": v.get("operating_status") or None,
        "former_names": [
            {"name": f.get("name"), "until": f.get("year_until")} for f in (v.get("former_names") or []) if f.get("name")
        ][:10],
        "source": "vesselapi.com",
    }


def vessel_record(mmsi: str, settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    """Registry record of an MMSI (cached 30 days); {} if unknown, None when unavailable / not configured."""
    cfg = settings or get_settings()
    if not cfg.vesselapi_api_key or not (str(mmsi).isdigit() and len(str(mmsi)) == 9):
        return None
    cached = cache_read("vesselapi_vessel", mmsi, REGISTRY_TTL, cfg)
    if cached is not None:
        return cached
    try:
        payload = _get(f"/vessel/{mmsi}", {"filter.idType": "mmsi"}, cfg)
    except VesselApiError as exc:
        logger.info("VesselAPI registry %s: %s", mmsi, exc)
        return None
    record = parse_vessel(payload) if payload else {}
    cache_write("vesselapi_vessel", mmsi, record, cfg)
    return record


# --------------------------------------------------------------------------- positions


def parse_positions(payload: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """(positions, statics) from a ``vessels[]`` page; fixes flagged ``suspected_glitch`` are dropped."""
    positions: List[Dict[str, Any]] = []
    statics: List[Dict[str, Any]] = []
    for r in payload.get("vessels") or []:
        mmsi = str(r.get("mmsi") or "")
        if not mmsi.isdigit() or r.get("suspected_glitch"):
            continue
        lat, lon = _number(r.get("latitude")), _number(r.get("longitude"))
        if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
            sog, cog, heading = _number(r.get("sog")), _number(r.get("cog")), _number(r.get("heading"))
            status = r.get("nav_status")
            positions.append({
                "mmsi": mmsi,
                "name": str(r.get("vessel_name") or "").strip(),
                "lat": lat,
                "lon": lon,
                "sog": sog if sog is not None and sog < 102.3 else None,
                "cog": cog if cog is not None and cog < 360 else None,
                "heading": heading if heading is not None and heading < 360 else None,  # 511 = not available
                "nav_status": int(status) if isinstance(status, int) or str(status or "").isdigit() else None,
                "ts": r.get("timestamp") or None,
                "source": SOURCE,
            })
        if r.get("vessel_name") or r.get("imo"):
            statics.append({"mmsi": mmsi, "name": str(r.get("vessel_name") or "").strip(), "imo": r.get("imo")})
    return positions, statics


def fetch_positions(start: datetime.datetime, end: datetime.datetime,
                    settings: Optional[Settings] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Fixes over the EEZ bbox in [start, end] (≤ 4 h), at most ``MAX_PAGES_PER_POLL`` pages."""
    cfg = settings or get_settings()
    west, south, east, north = cfg.eez_bbox
    params: Dict[str, Any] = {
        "filter.lonLeft": west, "filter.lonRight": east, "filter.latBottom": south, "filter.latTop": north,
        "time.from": _rfc3339(max(start, end - MAX_WINDOW)), "time.to": _rfc3339(end), "pagination.limit": PAGE_SIZE,
    }
    positions: List[Dict[str, Any]] = []
    statics: List[Dict[str, Any]] = []
    for _ in range(MAX_PAGES_PER_POLL):
        payload = _get("/location/vessels/bounding-box", params, cfg, reserve=REGISTRY_RESERVE) or {}
        page_positions, page_statics = parse_positions(payload)
        positions += page_positions
        statics += page_statics
        token = payload.get("nextToken")
        if not token:
            break
        params["pagination.nextToken"] = token  # valid only with the same time bounds
    return positions, statics


_last_end: Dict[str, datetime.datetime] = {}


def poll_once(zone_of: Any, settings: Optional[Settings] = None) -> int:
    """Fetch the window since the previous poll and persist it; returns the number of fixes offered."""
    from app.sources import ais_store

    cfg = settings or get_settings()
    now = _now()
    end = now.replace(microsecond=now.microsecond // 1000 * 1000)  # RFC3339 bounds are sent at ms precision
    previous = _last_end.get("end")
    start = previous + datetime.timedelta(milliseconds=1) if previous else end - datetime.timedelta(minutes=cfg.vesselapi_poll_minutes)
    positions, statics = fetch_positions(start, end, cfg)
    _last_end["end"] = end
    from app.sources import feed_log

    feed_log.mark_span(SOURCE, max(start, end - MAX_WINDOW), end, len(positions))
    feed_log.flush(cfg)
    for item in statics:
        ais_store.store_static(item["mmsi"], name=item["name"], imo=item["imo"], settings=cfg)
    ais_store.store_positions(positions, zone_of, cfg, source=SOURCE)
    return len(positions)


async def run_poller(settings: Optional[Settings] = None) -> None:
    """Poll every ``vesselapi_poll_minutes`` (idle when 0 or without key). Never raises (except cancel)."""
    from app.sources.marine_regions import classify_zone, zone_geometries

    cfg = settings or get_settings()
    zones: Dict[str, Any] = {}
    while True:
        if not (cfg.vesselapi_api_key and cfg.vesselapi_poll_minutes > 0 and cfg.ais_collector_enabled):
            _last_end.clear()
            await asyncio.sleep(60)
            continue
        try:
            if not zones:
                zones = await asyncio.to_thread(zone_geometries, cfg)
            count = await asyncio.to_thread(poll_once, lambda lon, lat: classify_zone(lon, lat, zones), cfg)
            logger.debug("VesselAPI: %s position(s)", count)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("VesselAPI poll failed: %s", str(exc)[:160])
        await asyncio.sleep(max(5, cfg.vesselapi_poll_minutes) * 60)
