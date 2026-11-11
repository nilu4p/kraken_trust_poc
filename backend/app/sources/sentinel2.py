"""Sentinel-2 L2A true colour at 10 m over the EEZ — Earth Search STAC (Element 84), no key.

Scenes are the most recent acquisitions *before the reference instant* (live or
replay), one per MGRS tile, under a cloud-cover ceiling. Map tiles are rendered
here from the public ``TCI`` Cloud-Optimised GeoTIFF (8-bit true colour, AWS open
data): only the needed overview / window is read, reprojected to Web Mercator,
and cached on disk. Sentinel-2 images land and coastal waters (≈ 20 km off the
coast), which is where most of the inshore illegal fishing happens; the open
EEZ is covered by Sentinel-1 radar instead.
"""

from __future__ import annotations

import datetime
import io
import logging
import math
import re
import threading
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT, atomic_write, cache_read, cache_write

logger = logging.getLogger(__name__)

STAC = "https://earth-search.aws.element84.com/v1"
COLLECTION = "sentinel-2-l2a"
# Imagery is only ever read from the public Sentinel-2 COG bucket (no user-supplied URL).
ALLOWED_HREF = re.compile(r"^https://sentinel-cogs\.s3\.us-west-2\.amazonaws\.com/[A-Za-z0-9/_.-]+\.tif$")
ITEM_ID = re.compile(r"^S2[ABC]_[0-9]{2}[A-Z]{3}_[0-9]{8}_[0-9]+_L2A$")
MIN_ZOOM, MAX_ZOOM = 8, 16
TILE = 256
HALF_WORLD = 20037508.342789244
_read_lock = threading.BoundedSemaphore(4)  # remote COG reads in parallel, bounded


class Sentinel2Error(RuntimeError):
    pass


def _post(url: str, body: Dict[str, Any], cfg: Settings) -> Optional[Dict[str, Any]]:
    import httpx

    try:
        response = httpx.post(url, json=body, headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
    except Exception as exc:
        health.record("sentinel2", False, type(exc).__name__)
        return None
    if response.status_code >= 400:
        health.record("sentinel2", False, f"HTTP {response.status_code}")
        return None
    health.record("sentinel2", True)
    return response.json()


def scenes(at: Optional[datetime.datetime] = None, days: int = 10, max_cloud: float = 60.0,
           settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Latest scene per MGRS tile acquired in the ``days`` before ``at`` (now when None)."""
    cfg = settings or get_settings()
    end = at or datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(days=days)
    key = f"{start:%Y%m%dT%H}/{end:%Y%m%dT%H}/{max_cloud}"
    cached = cache_read("sentinel2", key, (24 * 3600) if at else 1800, cfg)
    if cached is not None:
        return cached
    body = {
        "collections": [COLLECTION],
        "bbox": list(cfg.eez_bbox),
        "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
        "query": {"eo:cloud_cover": {"lt": max_cloud}},
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
        "limit": 200,
    }
    data = _post(f"{STAC}/search", body, cfg)
    if data is None:
        return {"type": "FeatureCollection", "features": [], "available": False, "reason": "catalogue Earth Search injoignable"}
    latest: Dict[str, Dict[str, Any]] = {}
    for item in data.get("features") or []:
        props = item.get("properties") or {}
        tile = props.get("grid:code") or item["id"].split("_")[1]
        if tile in latest or "visual" not in (item.get("assets") or {}):
            continue  # sorted newest first: the first one per tile wins
        latest[tile] = {
            "type": "Feature",
            "geometry": item.get("geometry"),
            "bbox": item.get("bbox"),
            "properties": {
                "type": "s2_scene",
                "item_id": item["id"],
                "datetime": props.get("datetime"),
                "cloud_cover": round(float(props.get("eo:cloud_cover") or 0), 1),
                "tile": tile,
                "platform": props.get("platform"),
            },
        }
    payload = {
        "type": "FeatureCollection",
        "features": sorted(latest.values(), key=lambda f: f["properties"]["datetime"] or ""),  # newest drawn last
        "available": True,
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "source": "Copernicus Sentinel-2 L2A via Earth Search (Element 84)",
    }
    cache_write("sentinel2", key, payload, cfg)
    return payload


def _visual_href(item_id: str, cfg: Settings) -> str:
    if not ITEM_ID.match(item_id):
        raise Sentinel2Error("identifiant de scène invalide")
    cached = cache_read("sentinel2_items", item_id, 30 * 24 * 3600, cfg)
    if cached is None:
        import httpx

        try:
            response = httpx.get(f"{STAC}/collections/{COLLECTION}/items/{item_id}",
                                 headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
        except Exception as exc:
            raise Sentinel2Error(f"catalogue injoignable ({type(exc).__name__})") from exc
        if response.status_code >= 400:
            raise Sentinel2Error(f"scène introuvable (HTTP {response.status_code})")
        cached = {"href": ((response.json().get("assets") or {}).get("visual") or {}).get("href", "")}
        cache_write("sentinel2_items", item_id, cached, cfg)
    href = cached["href"]
    if not ALLOWED_HREF.match(href or ""):
        raise Sentinel2Error("image hors du dépôt Sentinel-2 public")
    return href


def tile_bounds(z: int, x: int, y: int) -> tuple:
    """Web-Mercator bounds (minx, miny, maxx, maxy) of an XYZ tile."""
    size = 2 * HALF_WORLD / (2 ** z)
    minx = -HALF_WORLD + x * size
    maxy = HALF_WORLD - y * size
    return minx, maxy - size, minx + size, maxy


def tile_in_territory(z: int, x: int, y: int, settings: Optional[Settings] = None, margin_deg: float = 0.5) -> bool:
    """True when the XYZ tile overlaps the EEZ bounding box (with a margin)."""
    west, south, east, north = (settings or get_settings()).eez_bbox
    n = 2 ** z
    lon_w, lon_e = x / n * 360.0 - 180.0, (x + 1) / n * 360.0 - 180.0
    lat_n = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    lat_s = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return lon_e >= west - margin_deg and lon_w <= east + margin_deg and lat_n >= south - margin_deg and lat_s <= north + margin_deg


def render_tile(item_id: str, z: int, x: int, y: int, settings: Optional[Settings] = None) -> bytes:
    """PNG (RGBA, transparent outside the image) of one map tile of a scene; disk-cached."""
    cfg = settings or get_settings()
    if not (MIN_ZOOM <= z <= MAX_ZOOM and 0 <= x < 2 ** z and 0 <= y < 2 ** z):
        raise Sentinel2Error("tuile hors limites")
    if not ITEM_ID.match(item_id):
        raise Sentinel2Error("identifiant de scène invalide")
    if not tile_in_territory(z, x, y, cfg):
        # Tiles are rendered (remote read) and cached on disk: only over the territory.
        raise Sentinel2Error("tuile hors du territoire")
    path = Path(cfg.cache_dir) / "s2tiles" / item_id / str(z) / str(x) / f"{y}.png"
    if path.is_file():
        return path.read_bytes()
    href = _visual_href(item_id, cfg)
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.vrt import WarpedVRT
    from PIL import Image

    minx, miny, maxx, maxy = tile_bounds(z, x, y)
    with _read_lock, rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES",
                                  GDAL_HTTP_TIMEOUT=str(int(cfg.http_timeout)), CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif"):
        try:
            with rasterio.open(href) as src, WarpedVRT(
                src, crs="EPSG:3857", transform=from_bounds(minx, miny, maxx, maxy, TILE, TILE),
                width=TILE, height=TILE, resampling=Resampling.bilinear, nodata=0,
            ) as vrt:
                rgb = vrt.read(indexes=[1, 2, 3])
        except Exception as exc:
            health.record("sentinel2", False, type(exc).__name__)
            raise Sentinel2Error(f"lecture de l'image impossible ({type(exc).__name__})") from exc
    health.record("sentinel2", True)
    alpha = np.where(rgb.max(axis=0) > 0, 255, 0).astype(np.uint8)
    image = Image.fromarray(np.dstack([rgb[0], rgb[1], rgb[2], alpha]), "RGBA")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    try:
        atomic_write(path, data)
    except OSError as exc:
        logger.warning("Sentinel-2 tile not cached: %s", exc)
    return data


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple:
    n = 2 ** z
    lat_r = math.radians(max(min(lat, 85.0), -85.0))
    return int((lon + 180.0) / 360.0 * n), int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
