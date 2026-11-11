"""Ocean context from NOAA CoastWatch ERDDAP (free, no key), fused with Copernicus Marine.

- Bathymetry (ETOPO 1'): very shallow targets are often shoals, rocks or the
  Amazon-fed mud banks of the Guiana coast → detection false positives. The
  whole EEZ grid (~100 000 cells) is downloaded once and kept for a year:
  lookups are instant and work offline (a per-point query took ~20 s).
- Chlorophyll-a (VIIRS NOAA-20, weekly): productive waters attract fishing
  fleets. Best effort: the chlorophyll service is slow, so a short timeout
  and a 24 h cache are used; ``None`` when unavailable.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import httpx
import numpy as np

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT, cache_read, cache_write

logger = logging.getLogger(__name__)

# Mirrors of the same NOAA ERDDAP catalogue, fastest first (upwell answers in ~2 s where
# coastwatch can take minutes); the next one is tried when a server fails.
ERDDAP_SERVERS = ("https://upwell.pfeg.noaa.gov/erddap/griddap", "https://coastwatch.pfeg.noaa.gov/erddap/griddap")
ERDDAP = ERDDAP_SERVERS[0]
GRID_TTL = 365 * 24 * 3600
GRID_MARGIN_DEG = 0.1
_grid_lock = threading.Lock()
_grid: Dict[str, Any] = {}
BATHY_DATASET = "etopo180"
CHLA_DATASET = "nesdisVHNnoaa20chlaWeekly"


def _query(url: str, timeout: float, source: str) -> Optional[Dict[str, Any]]:
    try:
        response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        if response.status_code >= 400:
            health.record(source, False, f"HTTP {response.status_code}")
            return None
        health.record(source, True)
        return response.json().get("table")
    except Exception as exc:
        health.record(source, False, type(exc).__name__)
        return None


def _value(table: Optional[Dict[str, Any]], column: str) -> Optional[float]:
    if not table or not table.get("rows"):
        return None
    try:
        value = table["rows"][0][table["columnNames"].index(column)]
        return None if value is None else float(value)
    except (ValueError, IndexError, TypeError):
        return None


def _grid_path(cfg: Settings) -> Path:
    west, south, east, north = cfg.eez_bbox
    return Path(cfg.cache_dir) / "bathymetry" / f"etopo-{west}_{south}_{east}_{north}.npz"


def _download_grid(cfg: Settings) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    west, south, east, north = cfg.eez_bbox
    m = GRID_MARGIN_DEG
    query = f"altitude%5B({south - m}):1:({north + m})%5D%5B({west - m}):1:({east + m})%5D"
    for server in ERDDAP_SERVERS:
        try:
            response = httpx.get(f"{server}/{BATHY_DATASET}.csv0?{query}", headers={"User-Agent": USER_AGENT}, timeout=90)
            response.raise_for_status()
            values = np.loadtxt(response.text.splitlines(), delimiter=",", dtype=np.float64)
            lats, lons = np.unique(values[:, 0]), np.unique(values[:, 1])
            altitude = values[:, 2].reshape(len(lats), len(lons)).astype(np.int16)
            health.record("noaa_bathymetry", True)
            logger.info("Bathymetry grid downloaded from %s (%sx%s cells)", server, len(lats), len(lons))
            return lats, lons, altitude
        except Exception as exc:
            logger.warning("Bathymetry grid unavailable from %s: %s", server, type(exc).__name__)
    health.record("noaa_bathymetry", False, "grille ETOPO indisponible")
    return None


def _load_grid(cfg: Settings) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """EEZ bathymetry grid: memory, then disk cache (1 year), then one download."""
    import time

    path = _grid_path(cfg)
    with _grid_lock:
        if _grid.get("path") == str(path):
            return _grid["data"]
        data = None
        if path.is_file() and time.time() - path.stat().st_mtime < GRID_TTL:
            with np.load(path) as stored:
                data = (stored["lats"], stored["lons"], stored["altitude"])
        if data is None:
            if _grid.get("failed_at", 0) > time.monotonic() - 600:
                return None  # do not hammer NOAA: retry the download after 10 min
            data = _download_grid(cfg)
            if data is None:
                _grid["failed_at"] = time.monotonic()
                return None
            path.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(path, lats=data[0], lons=data[1], altitude=data[2])
        _grid.update(path=str(path), data=data)
        return data


def _grid_value(lat: float, lon: float, cfg: Settings) -> Optional[float]:
    grid = _load_grid(cfg)
    if grid is None:
        return None
    lats, lons, altitude = grid
    if not (lats[0] <= lat <= lats[-1] and lons[0] <= lon <= lons[-1]):
        return None
    i = int(np.abs(lats - lat).argmin())
    j = int(np.abs(lons - lon).argmin())
    return float(altitude[i, j])


def depth_m(lat: float, lon: float, settings: Optional[Settings] = None) -> Optional[float]:
    """Seafloor altitude in metres (negative = depth below sea level)."""
    cfg = settings or get_settings()
    in_grid = _grid_value(lat, lon, cfg)
    if in_grid is not None:
        return in_grid
    key = f"{lat:.2f},{lon:.2f}"
    cached = cache_read("bathymetry", key, 365 * 24 * 3600, cfg)
    if cached is not None:
        return cached.get("altitude")
    table = _query(f"{ERDDAP}/{BATHY_DATASET}.json?altitude%5B({lat:.4f})%5D%5B({lon:.4f})%5D", cfg.http_timeout, "noaa_bathymetry")
    altitude = _value(table, "altitude")
    if altitude is not None:
        cache_write("bathymetry", key, {"altitude": altitude}, cfg)
    return altitude


ISOBATHS_M = (20, 50, 100, 200, 1000, 2000, 3000, 4000)
_isobaths: Dict[str, Any] = {}


def _contour_segments(lats: np.ndarray, lons: np.ndarray, values: np.ndarray, level: float) -> list:
    """Marching squares on a regular grid: one [[lon, lat], [lon, lat]] segment per crossed cell."""
    above = values >= level
    a, b, c, d = above[:-1, :-1], above[:-1, 1:], above[1:, 1:], above[1:, :-1]  # SW, SE, NE, NW corners
    case = a * 1 + b * 2 + c * 4 + d * 8
    rows_i, cols_j = np.nonzero((case > 0) & (case < 15))
    segments = []

    def cross(v1: float, v2: float) -> float:
        return 0.5 if v1 == v2 else min(1.0, max(0.0, (level - v1) / (v2 - v1)))

    for i, j in zip(rows_i.tolist(), cols_j.tolist()):
        z_sw, z_se, z_ne, z_nw = values[i, j], values[i, j + 1], values[i + 1, j + 1], values[i + 1, j]
        lat0, lat1, lon0, lon1 = lats[i], lats[i + 1], lons[j], lons[j + 1]
        edges = {
            "s": (lon0 + cross(z_sw, z_se) * (lon1 - lon0), lat0),
            "e": (lon1, lat0 + cross(z_se, z_ne) * (lat1 - lat0)),
            "n": (lon0 + cross(z_nw, z_ne) * (lon1 - lon0), lat1),
            "w": (lon0, lat0 + cross(z_sw, z_nw) * (lat1 - lat0)),
        }
        k = int(case[i, j])
        corners = [k & 1, k & 2, k & 4, k & 8]
        crossed = [name for name, (p, q) in (("s", (0, 1)), ("e", (1, 2)), ("n", (2, 3)), ("w", (3, 0)))
                   if bool(corners[p]) != bool(corners[q])]
        # 2 crossed edges: one segment; 4 (saddle, cases 5 and 10): two segments, s-w / n-e or s-e / n-w.
        pairs = [crossed] if len(crossed) == 2 else ([["s", "w"], ["n", "e"]] if k == 5 else [["s", "e"], ["n", "w"]])
        for e1, e2 in pairs:
            segments.append([[round(float(edges[e1][0]), 4), round(float(edges[e1][1]), 4)],
                             [round(float(edges[e2][0]), 4), round(float(edges[e2][1]), 4)]])
    return segments


def isobaths(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Depth contours of the EEZ (ETOPO grid): continental shelf, shelf break (200 m), slope and abyss."""
    cfg = settings or get_settings()
    grid = _load_grid(cfg)
    if grid is None:
        return {"type": "FeatureCollection", "features": [], "available": False, "reason": "grille ETOPO indisponible"}
    if _isobaths.get("path") == str(_grid_path(cfg)):
        return _isobaths["data"]
    lats, lons, altitude = grid
    depth = -altitude.astype(np.float32)  # positive below sea level, negative on land
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "MultiLineString", "coordinates": _contour_segments(lats, lons, depth, level)},
            "properties": {"type": "isobath", "depth_m": level},
        }
        for level in ISOBATHS_M
    ]
    data = {"type": "FeatureCollection", "features": [f for f in features if f["geometry"]["coordinates"]],
            "available": True, "source": "NOAA ETOPO 1' (ERDDAP)"}
    _isobaths.update(path=str(_grid_path(cfg)), data=data)
    return data


def chlorophyll_mg_m3(lat: float, lon: float, settings: Optional[Settings] = None) -> Optional[float]:
    """Latest weekly chlorophyll-a concentration (mg/m³), best effort."""
    cfg = settings or get_settings()
    # 0.25° grid: one slow request covers every target of the cell.
    lat, lon = round(lat * 4) / 4, round(lon * 4) / 4
    key = f"{lat:.2f},{lon:.2f}"
    cached = cache_read("chlorophyll", key, 24 * 3600, cfg)
    if cached is not None:
        return cached.get("chlor_a")
    url = f"{ERDDAP}/{CHLA_DATASET}.json?chlor_a%5B(last)%5D%5B(0.0)%5D%5B({lat:.3f})%5D%5B({lon:.3f})%5D"
    value = _value(_query(url, min(cfg.http_timeout, 12.0), "noaa_chlorophyll"), "chlor_a")
    cache_write("chlorophyll", key, {"chlor_a": value}, cfg)  # cache misses too: the service is slow
    return value


def ocean_context(lat: float, lon: float, settings: Optional[Settings] = None, at: Any = None) -> Dict[str, Any]:
    """Fused ocean context at ``at`` (latest when None): ERDDAP bathymetry + Copernicus Marine.

    Chlorophyll comes from Copernicus Marine (fast, daily gap-free) and falls
    back to the slow NOAA VIIRS weekly product — for the latest maps only: that
    fallback reads the last week, which would mis-date a past instant.
    """
    from app.core.timeframe import parse_instant

    at = parse_instant(at)
    from app.sources.cmems import ocean_point

    from concurrent.futures import ThreadPoolExecutor

    cfg = settings or get_settings()
    with ThreadPoolExecutor(max_workers=2) as pool:  # independent sources: in parallel
        f_depth = pool.submit(depth_m, lat, lon, cfg)
        f_marine = pool.submit(ocean_point, lat, lon, cfg, at)
        altitude, marine = f_depth.result(), (f_marine.result() or {})
    chla = marine.get("chlorophyll_mg_m3")
    sources = ["NOAA ETOPO"]
    if marine:
        sources.append("Copernicus Marine")
    if chla is None and at is None:
        chla = chlorophyll_mg_m3(lat, lon, cfg)
        if chla is not None:
            sources.append("NOAA VIIRS")
    from app.sources import coastline

    # Land / sea from the OSM coastline (metres) rather than the ~1.8 km bathymetry cell,
    # which calls "land" points 2–3 km off the Guiana mud coast.
    shore = coastline.locate(lon, lat, cfg)
    if shore is not None:
        sources.append("OpenStreetMap (trait de côte)")
    return {
        "depth_m": -altitude if altitude is not None and altitude < 0 else (0.0 if altitude is not None else None),
        "on_land": shore["on_land"] if shore is not None else (altitude is not None and altitude >= 0),
        "shore_distance_m": shore["distance_m"] if shore is not None else None,
        "chlorophyll_mg_m3": chla,
        "productive_waters": chla is not None and chla >= 1.0,
        "sst_c": marine.get("sst_c"),
        "sst_gradient_c_10km": marine.get("sst_gradient_c_10km"),
        "thermal_front": bool(marine.get("thermal_front")),
        "current_speed_kn": marine.get("current_speed_kn"),
        "current_towards_deg": marine.get("current_towards_deg"),
        "source": " · ".join(sources),
        "valid_for": marine.get("valid_for"),
    }
