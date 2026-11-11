"""Copernicus Marine Service (Mercator Ocean, Toulouse) — public WMTS, no key.

Point values are read with WMTS ``GetFeatureInfo`` (JSON) on three products:

- surface currents (global analysis/forecast, 1/12°, 6-hourly) — drift of a
  target between two observations, context of a stationary echo;
- sea surface temperature (OSTIA L4, daily) — a strong horizontal gradient is
  a thermal front, where fish (and fishing fleets) aggregate;
- chlorophyll-a (multi-sensor L4 gap-free, daily) — productive waters.

The same layers are served as map tiles (``TILE_LAYERS``) for the dashboard.
Every read takes the reference instant (``TIME`` dimension): the ocean of a past
radar pass or of a replay is the ocean of that day, never today's — the
chlorophyll of one point can double from one week to the next. A date outside
a product's time range gives "unknown", never the latest map in its place.
Layer identifiers carry a product version: when Copernicus Marine releases a
new version, update ``LAYERS`` (calls fail gracefully meanwhile).
"""

from __future__ import annotations

import datetime
import logging
import math
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT, cache_read, cache_write

logger = logging.getLogger(__name__)

WMTS = "https://wmts.marine.copernicus.eu/teroWmts"
LAYERS = {
    "current": "GLOBAL_ANALYSISFORECAST_PHY_001_024/cmems_mod_glo_phy-cur_anfc_0.083deg_PT6H-i_202406/sea_water_velocity",
    "sst": "SST_GLO_SST_L4_NRT_OBSERVATIONS_010_001/METOFFICE-GLO-SST-L4-NRT-OBS-SST-V2/analysed_sst",
    "chl": "OCEANCOLOUR_GLO_BGC_L4_NRT_009_102/cmems_obs-oc_glo_bgc-plankton_nrt_l4-gapfree-multi-4km_P1D_202311/CHL",
}
# Map overlays offered to the dashboard (style ids from the WMTS capabilities).
TILE_LAYERS = {
    "sst": {"layer": LAYERS["sst"], "style": "cmap:thermal", "label": "Température de surface (OSTIA)"},
    "chl": {"layer": LAYERS["chl"], "style": "cmap:algae,logScale", "label": "Chlorophylle-a"},
    "current": {"layer": LAYERS["current"], "style": "cmap:speed,vectorStyle:solidAndVector", "label": "Courants de surface"},
}
ZOOM = 9  # GetFeatureInfo pixel ≈ 300 m: finer than every product grid
FRONT_STEP_DEG = 0.1
CELL_DEG = 0.05


def _tile_pixel(lat: float, lon: float, zoom: int = ZOOM) -> Tuple[int, int, int, int]:
    """Web-Mercator tile (col, row) and pixel (i, j) containing a point."""
    n = 2 ** zoom
    x = (lon + 180.0) / 360.0 * n
    lat_r = math.radians(max(min(lat, 85.0), -85.0))
    y = (1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n
    return int(x), int(y), min(int((x - int(x)) * 256), 255), min(int((y - int(y)) * 256), 255)


def time_param(kind: str, at: Optional[datetime.datetime]) -> Optional[str]:
    """WMTS TIME of a product at ``at``: daily products at 00:00 Z, currents at the nearest 6-hour step."""
    if at is None:
        return None
    at = at.astimezone(datetime.timezone.utc)
    if kind == "current":
        stamp = at.replace(minute=0, second=0, microsecond=0)
        hours = round(stamp.hour / 6) * 6
        stamp = stamp.replace(hour=0) + datetime.timedelta(hours=hours)
    else:
        stamp = at.replace(hour=0, minute=0, second=0, microsecond=0)
    return stamp.strftime("%Y-%m-%dT%H:%M:%SZ")


def feature_info(kind: str, lat: float, lon: float, cfg: Settings,
                 at: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Properties of the pixel under (lat, lon) for one layer at ``at`` (latest when None), or None."""
    col, row, i, j = _tile_pixel(lat, lon)
    params = {
        "SERVICE": "WMTS", "REQUEST": "GetFeatureInfo", "VERSION": "1.0.0", "LAYER": LAYERS[kind], "STYLE": "",
        "TILEMATRIXSET": "EPSG:3857", "TILEMATRIX": str(ZOOM), "TILEROW": row, "TILECOL": col, "I": i, "J": j,
        "INFOFORMAT": "application/json",
    }
    stamp = time_param(kind, at)
    if stamp:
        params["TIME"] = stamp
    try:
        response = httpx.get(WMTS, params=params, headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
    except Exception as exc:
        health.record("cmems", False, type(exc).__name__)
        return None
    if response.status_code >= 400:
        health.record("cmems", False, f"HTTP {response.status_code} ({kind})")
        return None
    health.record("cmems", True)
    try:
        features = response.json().get("features") or []
    except ValueError:
        return None
    props = (features[0].get("properties") or {}) if features else {}
    return props if props.get("value") is not None else None


def _sst_c(lat: float, lon: float, cfg: Settings, at: Optional[datetime.datetime] = None) -> Optional[float]:
    props = feature_info("sst", lat, lon, cfg, at)
    if not props:
        return None
    value = float(props["value"])
    return value - 273.15 if (props.get("units") or "").lower().startswith("kelvin") or value > 150 else value


def front_gradient(lat: float, lon: float, cfg: Settings, at: Optional[datetime.datetime] = None) -> Optional[float]:
    """Horizontal SST gradient in °C per 10 km (central differences over ±0.1°)."""
    step = FRONT_STEP_DEG
    with ThreadPoolExecutor(max_workers=4) as pool:
        north, south, east, west = pool.map(
            lambda p: _sst_c(p[0], p[1], cfg, at),
            [(lat + step, lon), (lat - step, lon), (lat, lon + step), (lat, lon - step)],
        )
    dy_km = 2 * step * 110.57
    dx_km = 2 * step * 111.32 * max(math.cos(math.radians(lat)), 1e-6)
    gy = (north - south) / dy_km if north is not None and south is not None else None
    gx = (east - west) / dx_km if east is not None and west is not None else None
    if gx is None and gy is None:
        return None
    return round(math.hypot(gx or 0.0, gy or 0.0) * 10.0, 3)


def ocean_point(lat: float, lon: float, settings: Optional[Settings] = None,
                at: Optional[datetime.datetime] = None) -> Optional[Dict[str, Any]]:
    """Currents, SST, thermal front and chlorophyll at a point at ``at`` (latest when None).

    Cached per 0.05° cell and per product time step: 6 h for the latest maps,
    30 days for a past date (published analyses barely change).
    """
    cfg = settings or get_settings()
    if not cfg.cmems_enabled:
        return None
    lat_c, lon_c = round(lat / CELL_DEG) * CELL_DEG, round(lon / CELL_DEG) * CELL_DEG
    key = f"{lat_c:.2f},{lon_c:.2f}" + (f"@{time_param('current', at)}" if at else "")
    cached = cache_read("cmems", key, (30 * 24 if at else 6) * 3600, cfg)
    if cached is not None:
        return cached or None
    with ThreadPoolExecutor(max_workers=3) as pool:
        current_f = pool.submit(feature_info, "current", lat_c, lon_c, cfg, at)
        sst_f = pool.submit(_sst_c, lat_c, lon_c, cfg, at)
        chl_f = pool.submit(feature_info, "chl", lat_c, lon_c, cfg, at)
        current, sst, chl = current_f.result(), sst_f.result(), chl_f.result()
    result: Dict[str, Any] = {}
    if current:
        u, v = current.get("component1Value"), current.get("component2Value")
        speed = float(current["value"])
        result["current_speed_kn"] = round(speed * 1.94384, 2)
        if u is not None and v is not None:
            result["current_towards_deg"] = round(math.degrees(math.atan2(float(u), float(v))) % 360)
    if sst is not None:
        result["sst_c"] = round(sst, 2)
        gradient = front_gradient(lat_c, lon_c, cfg, at)
        if gradient is not None:
            result["sst_gradient_c_10km"] = gradient
            result["thermal_front"] = gradient >= cfg.sst_front_threshold
    if chl:
        result["chlorophyll_mg_m3"] = round(float(chl["value"]), 3)
    if result:
        result["source"] = "Copernicus Marine (Mercator Ocean)"
        if at is not None:
            result["valid_for"] = time_param("sst", at)[:10]
    cache_write("cmems", key, result, cfg)
    return result or None


def tile_layers(at: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """WMTS KVP tile URL templates for Leaflet ({z}/{x}/{y} in EPSG:3857), at ``at`` (latest when None)."""
    out = {}
    for kind, meta in TILE_LAYERS.items():
        stamp = time_param(kind, at)
        out[kind] = {
            "label": meta["label"],
            "url": (
                f"{WMTS}?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER={meta['layer']}&STYLE={meta['style']}"
                "&TILEMATRIXSET=EPSG:3857&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&FORMAT=image/png"
                + (f"&TIME={stamp}" if stamp else "")
            ),
            "time": stamp,
            "legend": f"{WMTS}?SERVICE=WMTS&REQUEST=GetLegend&LAYER={meta['layer']}&STYLE={meta['style']}&FORMAT=image/svg%2Bxml",
            "attribution": "E.U. Copernicus Marine Service Information",
        }
    return out
