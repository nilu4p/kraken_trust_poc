"""Live AIS positions from aisstream.io WebSocket — free API key (https://aisstream.io/).

Listens for ``aisstream_listen_seconds`` on the EEZ bounding box and returns
the latest position per MMSI. Returns None when no key is configured or the
stream is unreachable so callers can fall back to another AIS source.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging
import time
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.sources import health

logger = logging.getLogger(__name__)

STREAM_URL = "wss://stream.aisstream.io/v0/stream"


def _first(mapping: Dict[str, Any], *names: str) -> Any:
    for name in names:
        if mapping.get(name) is not None:
            return mapping[name]
    return None


def parse_position_report(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise an aisstream PositionReport message to an AIS track dict."""
    if message.get("MessageType") != "PositionReport":
        return None
    meta = message.get("MetaData") or {}
    report = (message.get("Message") or {}).get("PositionReport") or {}
    lat = _first(meta, "latitude", "Latitude") if meta else None
    lon = _first(meta, "longitude", "Longitude") if meta else None
    if lat is None or lon is None:
        lat, lon = report.get("Latitude"), report.get("Longitude")
    mmsi = _first(meta, "MMSI", "mmsi") or report.get("UserID")
    if lat is None or lon is None or mmsi is None:
        return None
    return {
        "mmsi": str(mmsi),
        "name": str(_first(meta, "ShipName", "shipname") or "").strip() or "AIS",
        "lat": float(lat),
        "lon": float(lon),
        "sog_knots": report.get("Sog"),
        "cog_deg": report.get("Cog"),
        "heading_deg": report.get("TrueHeading"),
        "nav_status": report.get("NavigationalStatus"),
        "time_utc": _first(meta, "time_utc", "TimeUtc"),
        "source": "aisstream.io",
    }


async def _listen(cfg: Settings) -> List[Dict[str, Any]]:
    import websockets

    west, south, east, north = cfg.eez_bbox
    subscription = {
        "APIKey": cfg.aisstream_api_key,
        "BoundingBoxes": [[[south, west], [north, east]]],
        "FilterMessageTypes": ["PositionReport"],
    }
    tracks: Dict[str, Dict[str, Any]] = {}
    deadline = time.monotonic() + cfg.aisstream_listen_seconds
    async with websockets.connect(STREAM_URL, open_timeout=cfg.http_timeout) as ws:
        await ws.send(json.dumps(subscription))
        while (remaining := deadline - time.monotonic()) > 0:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if "error" in message:
                raise RuntimeError(str(message["error"]))
            track = parse_position_report(message)
            if track:
                tracks[track["mmsi"]] = track
    return list(tracks.values())


def fetch_live_ais(settings: Optional[Settings] = None) -> Optional[List[Dict[str, Any]]]:
    """Collect live AIS tracks over the EEZ, or None if unavailable."""
    cfg = settings or get_settings()
    if not cfg.aisstream_api_key:
        return None
    try:
        tracks = asyncio.run(_listen(cfg))
        logger.info("aisstream.io: %s vessel(s) heard in %.0fs", len(tracks), cfg.aisstream_listen_seconds)
        health.record("aisstream", True)
        return tracks
    except Exception as exc:
        logger.warning("aisstream.io unavailable: %s", exc)
        health.record("aisstream", False, type(exc).__name__)
        return None


# --------------------------------------------------------------------------- continuous collector

POSITION_TYPES = ("PositionReport", "StandardClassBPositionReport", "ExtendedClassBPositionReport")
SILENT_ALERT_MINUTES = 60

# Live collector state (shown in the coverage panel): a connected stream that
# delivers nothing means the zone is outside aisstream's receiver coverage.
COLLECTOR: Dict[str, Any] = {"connected_since": None, "messages": 0, "positions": 0, "last_message_at": None,
                             "last_error": None}


def _publish(force: bool = False) -> None:
    """Share the collector counters (the API reads them from another process)."""
    global _last_publish
    now = time.monotonic()
    if not force and now - _last_publish < 5:
        return
    _last_publish = now
    try:
        from app.core import shared

        shared.set_json("collector:aisstream", COLLECTOR, ttl=3600)
    except Exception:
        pass


_last_publish = 0.0


def collector_status() -> Dict[str, Any]:
    from app.core import shared

    try:
        published = shared.get_json("collector:aisstream")
    except Exception:
        published = None
    status = dict(published or COLLECTOR)
    since = status["connected_since"]
    silent_min = None
    if since:
        reference = status["last_message_at"] or since
        silent_min = round((time.time() - reference) / 60, 1)
    status["silent_minutes"] = silent_min
    for key in ("connected_since", "last_message_at"):
        if status[key]:
            status[key] = datetime.datetime.fromtimestamp(status[key], datetime.timezone.utc).isoformat()
    return status


def _report_silence(cfg: Settings) -> None:
    """Connected but nothing heard for an hour: say so once a day on #operations."""
    status = collector_status()
    if status["silent_minutes"] is None or status["silent_minutes"] < SILENT_ALERT_MINUTES:
        return
    from app.soc import notify

    notify.notify_source_health(
        "aisstream", "down",
        f"connecté mais aucun message AIS depuis {status['silent_minutes']:.0f} min sur la zone "
        "(couverture des récepteurs aisstream insuffisante ?) — ajoutez AISHub ou une station locale",
        cfg,
    )
STATIC_TYPES = ("ShipStaticData", "StaticDataReport")


def parse_message(message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalise any useful aisstream message into ``{"kind": "position"|"static", ...}``."""
    kind = message.get("MessageType")
    meta = message.get("MetaData") or {}
    body = (message.get("Message") or {}).get(kind) or {}
    mmsi = _first(meta, "MMSI", "mmsi") or body.get("UserID")
    if mmsi is None:
        return None
    mmsi = str(mmsi)
    if kind in POSITION_TYPES:
        lat = _first(meta, "latitude", "Latitude")
        lon = _first(meta, "longitude", "Longitude")
        if lat is None or lon is None:
            lat, lon = body.get("Latitude"), body.get("Longitude")
        if lat is None or lon is None or abs(float(lat)) > 90 or abs(float(lon)) > 180:
            return None
        cog = body.get("Cog")
        return {
            "kind": "position",
            "mmsi": mmsi,
            "name": str(_first(meta, "ShipName", "shipname") or "").strip(),
            "lat": float(lat),
            "lon": float(lon),
            "sog": body.get("Sog"),
            "cog": cog if cog is not None and cog < 360 else None,
            "heading": body.get("TrueHeading") if (body.get("TrueHeading") or 511) < 360 else None,
            "nav_status": body.get("NavigationalStatus"),
            "ts": _first(meta, "time_utc", "TimeUtc"),
        }
    if kind == "ShipStaticData":
        from app.sources.ais_store import dimension_length

        dim = body.get("Dimension") or {}
        return {"kind": "static", "mmsi": mmsi, "name": body.get("Name") or "", "imo": body.get("ImoNumber"),
                "call_sign": body.get("CallSign") or "", "ship_type": body.get("Type"),
                "length_m": dimension_length(dim.get("A"), dim.get("B"))}
    if kind == "StaticDataReport":
        report_a = body.get("ReportA") or {}
        report_b = body.get("ReportB") or {}
        from app.sources.ais_store import dimension_length

        dim = report_b.get("Dimension") or {}
        return {"kind": "static", "mmsi": mmsi, "name": report_a.get("Name") or "", "imo": None,
                "call_sign": report_b.get("CallSign") or "", "ship_type": report_b.get("ShipType"),
                "length_m": dimension_length(dim.get("A"), dim.get("B"))}
    return None


async def run_collector(settings: Optional[Settings] = None) -> None:
    """Stay subscribed to aisstream.io over the EEZ and persist the AIS picture.

    Reconnects with exponential backoff; idles while no key is configured (the
    key can be added later from the dashboard). Never raises (except cancel).
    """
    from app.sources import ais_store, feed_log
    from app.sources.marine_regions import classify_zone, zone_geometries

    cfg = settings or get_settings()
    backoff = 5.0
    zones: Dict[str, Any] = {}
    while True:
        if not (cfg.ais_collector_enabled and cfg.aisstream_api_key):
            await asyncio.sleep(30)
            continue
        key_in_use = cfg.aisstream_api_key
        buffer: List[Dict[str, Any]] = []
        last_flush = time.monotonic()
        try:
            import websockets

            if not zones:
                zones = await asyncio.to_thread(zone_geometries, cfg)

            def zone_of(lon: float, lat: float) -> Optional[str]:
                return classify_zone(lon, lat, zones)

            west, south, east, north = cfg.eez_bbox
            subscription = {
                "APIKey": key_in_use,
                # Slightly larger than the EEZ so vessels are tracked before they enter it.
                "BoundingBoxes": [[[south - 1, west - 1], [north + 1, east + 1]]],
                "FilterMessageTypes": list(POSITION_TYPES + STATIC_TYPES),
            }
            async with websockets.connect(STREAM_URL, open_timeout=cfg.http_timeout, ping_interval=30) as ws:
                await ws.send(json.dumps(subscription))
                logger.info("AIS collector connected (aisstream.io)")
                health.record("aisstream", True)
                COLLECTOR.update(connected_since=time.time(), last_error=None)
                _publish(force=True)
                backoff = 5.0
                last_summary = time.monotonic()
                last_log_flush = time.monotonic()
                silence_reported = False
                while cfg.aisstream_api_key == key_in_use and cfg.ais_collector_enabled:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=60)
                    except asyncio.TimeoutError:
                        raw = None
                    # Connected = listening this minute, even when nobody is heard (recv times out at 60 s).
                    feed_log.mark("aisstream", 1 if raw is not None else 0)
                    if time.monotonic() - last_log_flush > 60:
                        last_log_flush = time.monotonic()
                        await asyncio.to_thread(feed_log.flush, cfg)
                    _publish()
                    if time.monotonic() - last_summary > 600:
                        last_summary = time.monotonic()
                        status = collector_status()
                        logger.info("AIS collector: %s message(s), %s position(s) since connection; silent for %s min",
                                    status["messages"], status["positions"], status["silent_minutes"])
                        if not silence_reported and (status["silent_minutes"] or 0) >= SILENT_ALERT_MINUTES:
                            silence_reported = True
                            health.record("aisstream", False, "connecté mais aucun navire entendu sur la zone")
                            await asyncio.to_thread(_report_silence, cfg)
                    if raw is not None:
                        COLLECTOR["messages"] += 1
                        COLLECTOR["last_message_at"] = time.time()
                        _publish()
                        silence_reported = False
                        message = json.loads(raw)
                        if isinstance(message, dict) and message.get("error"):
                            raise RuntimeError(str(message["error"]))
                        parsed = parse_message(message) if isinstance(message, dict) else None
                        if parsed and parsed["kind"] == "static":
                            await asyncio.to_thread(
                                ais_store.store_static, parsed["mmsi"], name=parsed["name"], imo=parsed["imo"],
                                call_sign=parsed["call_sign"], ship_type=parsed["ship_type"],
                                length_m=parsed.get("length_m"), settings=cfg,
                            )
                        elif parsed and ais_store.should_store(parsed["mmsi"], cfg.ais_store_interval_s):
                            COLLECTOR["positions"] += 1
                            buffer.append(parsed)
                    if buffer and (len(buffer) >= 200 or time.monotonic() - last_flush > 10):
                        await asyncio.to_thread(ais_store.store_positions, buffer, zone_of, cfg)
                        buffer, last_flush = [], time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            health.record("aisstream", False, type(exc).__name__)
            COLLECTOR.update(connected_since=None, last_error=f"{type(exc).__name__}: {str(exc)[:120]}")
            _publish(force=True)
            logger.warning("AIS collector disconnected (%s: %s) — retry in %.0fs", type(exc).__name__, str(exc)[:120], backoff)
        if buffer:
            try:
                await asyncio.to_thread(ais_store.store_positions, buffer, None, cfg)
            except Exception:
                pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300.0)
