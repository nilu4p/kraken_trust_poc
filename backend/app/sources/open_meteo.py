"""Sea state & wind from Open-Meteo (Marine + Forecast APIs) — free, no API key.

Sea state matters for SAR ship detection: strong wind roughens the sea surface,
raising clutter and false alarms, while calm seas make small hulls stand out.
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

from app.config import Settings, get_settings
from app.sources.http import cache_read, cache_write, get_json

logger = logging.getLogger(__name__)

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Default probe point: offshore Cayenne / Kourou, inside the EEZ.
DEFAULT_POINT = (5.6, -52.3)


def sar_reliability(wind_speed_ms: Optional[float], wave_height_m: Optional[float]) -> Dict[str, Any]:
    """Qualitative SAR detection reliability from wind (m/s) and wave height (m)."""
    if wind_speed_ms is None and wave_height_m is None:
        return {"level": "unknown", "factor": 1.0, "reason": "Météo marine indisponible"}
    wind = wind_speed_ms or 0.0
    waves = wave_height_m or 0.0
    if wind > 12.0 or waves > 3.5:
        return {"level": "low", "factor": 0.7, "reason": "Mer forte : fouillis radar élevé"}
    if wind > 7.0 or waves > 2.0:
        return {"level": "medium", "factor": 0.9, "reason": "Mer agitée : fausses alarmes possibles"}
    if wind < 3.0:
        # Little wind = dark, smooth sea: the best background for a ship echo (fewer false alarms).
        return {"level": "high", "factor": 1.0, "reason": "Mer calme : fond radar sombre, contraste des navires maximal"}
    return {"level": "high", "factor": 1.0, "reason": "Conditions favorables à la détection SAR"}


def _hour(value: Any) -> Optional[datetime.datetime]:
    from app.pipeline.detection import _parse_time

    parsed = _parse_time(value) if value else None
    return parsed.replace(minute=0, second=0, microsecond=0) if parsed else None


def sea_state_at(lat: float, lon: float, at: Any, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Wind / waves / SST at a past instant (e.g. the SAR pass), hourly archive (≈ 3 months).

    Less than an hour old: current conditions (``at_pass=True``). Archive
    unavailable: ``available=False`` — never today's sea in place of the pass's,
    which would wrongly weight the detection reliability and the risk.
    """
    cfg = settings or get_settings()
    hour = _hour(at)
    now = datetime.datetime.now(datetime.timezone.utc)
    if hour is None or now - hour < datetime.timedelta(hours=1):
        return {**sea_state(lat, lon, cfg), "at_pass": hour is not None}
    stamp = hour.strftime("%Y-%m-%dT%H:00")
    key = f"{lat:.2f},{lon:.2f}@{stamp}"
    cached = cache_read("open_meteo", key, 30 * 24 * 3600, cfg)
    if cached:
        return cached
    window = {"start_hour": stamp, "end_hour": stamp}
    marine = get_json(
        MARINE_URL,
        params={"latitude": lat, "longitude": lon, **window,
                "hourly": "wave_height,wave_direction,wave_period,ocean_current_velocity,ocean_current_direction,sea_surface_temperature"},
        settings=cfg, source="open_meteo",
    )
    wind = get_json(
        FORECAST_URL,
        params={"latitude": lat, "longitude": lon, **window, "hourly": "wind_speed_10m,wind_direction_10m,visibility",
                "wind_speed_unit": "ms"},
        settings=cfg, source="open_meteo",
    )

    def first(payload: Optional[Dict[str, Any]], name: str) -> Any:
        values = ((payload or {}).get("hourly") or {}).get(name) or []
        return values[0] if values else None

    wind_speed, wave_height = first(wind, "wind_speed_10m"), first(marine, "wave_height")
    if wind_speed is None and wave_height is None:
        logger.warning("Open-Meteo archive unavailable for %s — sea state unknown at that instant", stamp)
        return {"available": False, "at_pass": False, "time": stamp, "lat": lat, "lon": lon, "source": "open-meteo.com",
                "reason": "archive météo indisponible pour cet instant"}
    result = {
        "available": True,
        "source": "open-meteo.com",
        "lat": lat,
        "lon": lon,
        "time": stamp,
        "at_pass": True,
        "wind_speed_ms": wind_speed,
        "wind_direction_deg": first(wind, "wind_direction_10m"),
        "visibility_m": first(wind, "visibility"),
        "wave_height_m": wave_height,
        "wave_direction_deg": first(marine, "wave_direction"),
        "wave_period_s": first(marine, "wave_period"),
        "current_velocity_kmh": first(marine, "ocean_current_velocity"),
        "current_direction_deg": first(marine, "ocean_current_direction"),
        "sea_surface_temperature_c": first(marine, "sea_surface_temperature"),
        "sar_reliability": sar_reliability(wind_speed, wave_height),
    }
    cache_write("open_meteo", key, result, cfg)
    return result


def sea_state(
    lat: float = DEFAULT_POINT[0],
    lon: float = DEFAULT_POINT[1],
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Current wind / waves / SST at a point. Never raises; ``available`` flags data."""
    cfg = settings or get_settings()
    key = f"{lat:.2f},{lon:.2f}"
    cached = cache_read("open_meteo", key, cfg.weather_ttl_minutes * 60, cfg)
    if cached:
        return cached

    marine = get_json(
        MARINE_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "wave_height,wave_direction,wave_period,ocean_current_velocity,"
            "ocean_current_direction,sea_surface_temperature",
        },
        settings=cfg,
        source="open_meteo",
    )
    wind = get_json(
        FORECAST_URL,
        params={
            "latitude": lat,
            "longitude": lon,
            "current": "wind_speed_10m,wind_direction_10m,visibility",
            "wind_speed_unit": "ms",
        },
        settings=cfg,
        source="open_meteo",
    )
    marine_now = (marine or {}).get("current") or {}
    wind_now = (wind or {}).get("current") or {}
    wind_speed = wind_now.get("wind_speed_10m")
    wave_height = marine_now.get("wave_height")
    result: Dict[str, Any] = {
        "available": bool(marine_now or wind_now),
        "source": "open-meteo.com",
        "lat": lat,
        "lon": lon,
        "time": marine_now.get("time") or wind_now.get("time"),
        "wind_speed_ms": wind_speed,
        "wind_direction_deg": wind_now.get("wind_direction_10m"),
        "visibility_m": wind_now.get("visibility"),
        "wave_height_m": wave_height,
        "wave_direction_deg": marine_now.get("wave_direction"),
        "wave_period_s": marine_now.get("wave_period"),
        "current_velocity_kmh": marine_now.get("ocean_current_velocity"),
        "current_direction_deg": marine_now.get("ocean_current_direction"),
        "sea_surface_temperature_c": marine_now.get("sea_surface_temperature"),
        "sar_reliability": sar_reliability(wind_speed, wave_height),
    }
    if result["available"]:
        cache_write("open_meteo", key, result, cfg)
    else:
        logger.warning("Open-Meteo unavailable for %s", key)
    return result
