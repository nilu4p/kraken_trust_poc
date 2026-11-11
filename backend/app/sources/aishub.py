"""AISHub — second terrestrial AIS feed (https://www.aishub.net/, free for members).

AISHub aggregates the stations of its members; a username is issued to
members sharing the raw feed of one AIS receiver. Its stations complement
aisstream.io (different receivers → vessels heard by one feed only), so both
feeds are fused in the same history (``ais_positions.source``), the same fix
being stored once.

Webservice: ``GET https://data.aishub.net/ws.php`` — at most one call per
minute (AISHub returns nothing otherwise). Response (format=1, human
readable): ``[{"ERROR": false, "RECORDS": n, ...}, [{"MMSI": ..., ...}, ...]]``.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT

logger = logging.getLogger(__name__)

API = "https://data.aishub.net/ws.php"
MIN_POLL_SECONDS = 60


class AisHubError(RuntimeError):
    pass


def _number(value: Any) -> Optional[float]:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _time(value: Any) -> Optional[str]:
    """AISHub format 1 time: ``2026-10-20 10:00:00 GMT`` (UTC)."""
    text = str(value or "").replace("GMT", "").replace("UTC", "").strip()
    try:
        parsed = datetime.datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return parsed.replace(tzinfo=datetime.timezone.utc).isoformat()


def _length(a: Any, b: Any) -> Optional[float]:
    from app.sources.ais_store import dimension_length

    return dimension_length(a, b)


def parse_response(payload: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Return (positions, statics) from a format=1 JSON response; raise AisHubError on an API error."""
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        raise AisHubError("réponse AISHub inattendue")
    header = payload[0]
    if header.get("ERROR"):
        raise AisHubError(str(header.get("ERROR_MESSAGE") or "erreur AISHub"))
    records = payload[1] if len(payload) > 1 and isinstance(payload[1], list) else []
    positions: List[Dict[str, Any]] = []
    statics: List[Dict[str, Any]] = []
    for r in records:
        if not isinstance(r, dict) or not str(r.get("MMSI") or "").isdigit():
            continue
        mmsi = str(r["MMSI"])
        lat, lon = _number(r.get("LATITUDE")), _number(r.get("LONGITUDE"))
        # 91 / 181 = position not available in AIS
        if lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180:
            cog, sog, heading = _number(r.get("COG")), _number(r.get("SOG")), _number(r.get("HEADING"))
            positions.append({
                "mmsi": mmsi,
                "name": str(r.get("NAME") or "").strip(),
                "lat": lat,
                "lon": lon,
                "sog": sog if sog is not None and sog < 102.3 else None,
                "cog": cog if cog is not None and cog < 360 else None,
                "heading": heading if heading is not None and heading < 360 else None,
                "nav_status": int(r["NAVSTAT"]) if str(r.get("NAVSTAT") or "").isdigit() else None,
                "ts": _time(r.get("TIME")),
                "source": "aishub",
            })
        if r.get("NAME") or r.get("IMO") or r.get("CALLSIGN") or r.get("TYPE"):
            statics.append({
                "mmsi": mmsi,
                "name": str(r.get("NAME") or "").strip(),
                "imo": r.get("IMO"),
                "call_sign": str(r.get("CALLSIGN") or "").strip(),
                "ship_type": r.get("TYPE") or None,
                "length_m": _length(r.get("A"), r.get("B")),
            })
    return positions, statics


def fetch(settings: Optional[Settings] = None) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """One call over the EEZ (+1° margin). Raises AisHubError / httpx errors."""
    cfg = settings or get_settings()
    west, south, east, north = cfg.eez_bbox
    response = httpx.get(
        API,
        params={
            "username": cfg.aishub_username, "format": 1, "output": "json", "compress": 0,
            "latmin": south - 1, "latmax": north + 1, "lonmin": west - 1, "lonmax": east + 1,
            "interval": max(1, cfg.aishub_poll_seconds // 60 + 1),
        },
        headers={"User-Agent": USER_AGENT},
        timeout=cfg.http_timeout,
    )
    response.raise_for_status()
    if not response.content.strip():
        raise AisHubError("réponse vide (appel trop fréquent ? 1 appel / minute maximum)")
    return parse_response(response.json())


def poll_once(zone_of: Any, settings: Optional[Settings] = None) -> int:
    """Fetch and persist one AISHub snapshot; returns the number of new positions offered."""
    from app.sources import ais_store

    cfg = settings or get_settings()
    try:
        positions, statics = fetch(cfg)
    except Exception as exc:
        health.record("aishub", False, str(exc)[:120] if isinstance(exc, AisHubError) else type(exc).__name__)
        raise
    health.record("aishub", True)
    from app.sources import feed_log

    now = datetime.datetime.now(datetime.timezone.utc)
    # The webservice returns the fixes of the last ``interval`` minutes: that span was observed.
    feed_log.mark_span("aishub", now - datetime.timedelta(seconds=max(60, cfg.aishub_poll_seconds)), now, len(positions))
    feed_log.flush(cfg)
    for item in statics:
        ais_store.store_static(item["mmsi"], name=item["name"], imo=item["imo"], call_sign=item["call_sign"],
                               ship_type=item["ship_type"], length_m=item.get("length_m"), settings=cfg)
    ais_store.store_positions(positions, zone_of, cfg, source="aishub")
    return len(positions)


async def run_poller(settings: Optional[Settings] = None) -> None:
    """Poll AISHub every ``aishub_poll_seconds`` (idle without username). Never raises (except cancel)."""
    from app.sources.marine_regions import classify_zone, zone_geometries

    cfg = settings or get_settings()
    zones: Dict[str, Any] = {}
    backoff = 0.0
    while True:
        if not (cfg.aishub_username and cfg.ais_collector_enabled):
            await asyncio.sleep(30)
            continue
        try:
            if not zones:
                zones = await asyncio.to_thread(zone_geometries, cfg)
            count = await asyncio.to_thread(poll_once, lambda lon, lat: classify_zone(lon, lat, zones), cfg)
            logger.debug("AISHub: %s position(s)", count)
            backoff = 0.0
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            backoff = min(max(backoff * 2, 60.0), 1800.0)
            logger.warning("AISHub poll failed (%s) — next try in %.0fs", str(exc)[:120], cfg.aishub_poll_seconds + backoff)
        await asyncio.sleep(max(MIN_POLL_SECONDS, cfg.aishub_poll_seconds) + backoff)
