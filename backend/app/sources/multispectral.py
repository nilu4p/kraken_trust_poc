"""Optical and thermal imagery readers for vessel detection (keyless public archives).

| Sensor | Resolution | Archive | Masks | Extra |
| --- | --- | --- | --- | --- |
| Sentinel-2 MSI L2A | 10 m (NIR, RGB) | Earth Search / AWS open data (Element 84) | scene classification (SCL) | — |
| Landsat 8/9 OLI-TIRS L2 | 30 m (NIR, RGB), 100 m thermal | Microsoft Planetary Computer (anonymous SAS token) | QA_PIXEL bits | surface temperature (K) |

Both archives publish Cloud-Optimised GeoTIFFs: only the windows needed are
read (HTTP range requests). Optical satellites image land and **coastal waters**
(Sentinel-2: ≈ 20 km offshore; Landsat: the WRS-2 scenes along the coast), where
most inshore illegal fishing takes place; the open EEZ is covered by
Sentinel-1 radar and VIIRS night lights.

Asset URLs are only ever taken from the catalogue responses and checked against
the archive host (no user-supplied URL is opened).
"""

from __future__ import annotations

import datetime
import logging
import math
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

from app.config import Settings, get_settings
from app.core.geo import point_in_geometry
from app.pipeline import optical_detection
from app.sources import health
from app.sources.http import USER_AGENT

logger = logging.getLogger(__name__)

WINDOW_PX = 1024


def _s2_masks(scl: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    water = scl == 6
    invalid = np.isin(scl, (0, 1, 3, 8, 9, 10))  # no data, saturated, cloud shadow, clouds, cirrus
    return water, invalid


def _landsat_masks(qa: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    qa = qa.astype(np.uint32)
    water = (qa & (1 << 7)) > 0
    invalid = (qa & 0b11111) > 0  # fill, dilated cloud, cirrus, cloud, cloud shadow
    return water, invalid


SENSORS: Dict[str, Dict[str, Any]] = {
    "s2_msi": {
        "label": "Sentinel-2 MSI (10 m)",
        "stac": "https://earth-search.aws.element84.com/v1/search",
        "collection": "sentinel-2-l2a",
        "nir": "nir", "rgb": ("red", "green", "blue"), "mask": "scl", "thermal": None, "swir": "swir16",
        "pixel_m": 10.0, "masks": _s2_masks,
        "href": re.compile(r"^https://sentinel-cogs\.s3\.us-west-2\.amazonaws\.com/[A-Za-z0-9/_.-]+\.tif$"),
        "sign": None, "default_scale": (1e-4, 0.0),
    },
    "landsat_oli": {
        "label": "Landsat 8/9 OLI-TIRS (30 m, thermique 100 m)",
        "stac": "https://planetarycomputer.microsoft.com/api/stac/v1/search",
        "collection": "landsat-c2-l2",
        "nir": "nir08", "rgb": ("red", "green", "blue"), "mask": "qa_pixel", "thermal": "lwir11", "swir": "swir16",
        "pixel_m": 30.0, "masks": _landsat_masks,
        "href": re.compile(r"^https://landsateuwest\.blob\.core\.windows\.net/landsat-c2/[A-Za-z0-9/_.-]+\.TIF$"),
        "sign": "landsat-c2-l2", "default_scale": (2.75e-5, -0.2),
        "query": {"platform": {"in": ["landsat-8", "landsat-9"]}},
    },
}


class ImageryError(RuntimeError):
    pass


def _now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# --------------------------------------------------------------------------- catalogue


def search(sensor: str, bbox: Tuple[float, float, float, float], start: datetime.datetime, end: datetime.datetime,
           max_cloud: float, cfg: Settings, limit: int = 20) -> List[Dict[str, Any]]:
    """Catalogue items (newest first) intersecting ``bbox`` in ``[start, end]`` under the cloud ceiling."""
    import httpx

    spec = SENSORS[sensor]
    query: Dict[str, Any] = {"eo:cloud_cover": {"lt": max_cloud}, **(spec.get("query") or {})}
    body = {
        "collections": [spec["collection"]], "bbox": list(bbox), "limit": limit, "query": query,
        "datetime": f"{start:%Y-%m-%dT%H:%M:%SZ}/{end:%Y-%m-%dT%H:%M:%SZ}",
        "sortby": [{"field": "properties.datetime", "direction": "desc"}],
    }
    try:
        response = httpx.post(spec["stac"], json=body, headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
        response.raise_for_status()
        items = response.json().get("features") or []
    except Exception as exc:
        health.record(sensor, False, type(exc).__name__)
        raise ImageryError(f"catalogue {spec['label']} injoignable ({type(exc).__name__})") from exc
    health.record(sensor, True)
    return items


def _href(item: Dict[str, Any], asset: str, sensor: str, cfg: Settings) -> Tuple[str, float, float]:
    spec = SENSORS[sensor]
    meta = (item.get("assets") or {}).get(asset) or {}
    href = meta.get("href") or ""
    if not spec["href"].match(href):
        raise ImageryError(f"bande {asset} hors de l'archive publique attendue")
    bands = meta.get("raster:bands") or [{}]
    scale = float(bands[0].get("scale", spec["default_scale"][0] if asset != spec["mask"] else 1.0))
    offset = float(bands[0].get("offset", spec["default_scale"][1] if asset != spec["mask"] else 0.0))
    if (item.get("properties") or {}).get("earthsearch:boa_offset_applied"):
        offset = 0.0  # Earth Search already removed the +1000 DN offset of baseline ≥ 04.00 (metadata still lists it)
    if spec["sign"]:
        from app.sources.planetary import sign_collection

        href = sign_collection(href, spec["sign"], cfg)
    return href, scale, offset


def _env(cfg: Settings) -> Any:
    import rasterio

    return rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", AWS_NO_SIGN_REQUEST="YES", GDAL_HTTP_MULTIRANGE="YES",
                        GDAL_HTTP_MERGE_CONSECUTIVE_RANGES="YES", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF",
                        GDAL_HTTP_TIMEOUT=str(int(cfg.http_timeout * 3)), GDAL_HTTP_USERAGENT=USER_AGENT)


# --------------------------------------------------------------------------- window reading


def _read_bands(item: Dict[str, Any], sensor: str, window: Any, reference: Any, cfg: Settings) -> Dict[str, np.ndarray]:
    """All detection bands of one window, on the grid of the NIR band (masks resampled nearest)."""
    import rasterio

    spec = SENSORS[sensor]
    shape = (int(window.height), int(window.width))
    names = [spec["nir"], *spec["rgb"], spec["mask"], spec["swir"]] + ([spec["thermal"]] if spec["thermal"] else []) \
        + ([spec["cloud_prob"]] if spec.get("cloud_prob") and spec["cloud_prob"] in (item.get("assets") or {}) else [])
    arrays: Dict[str, np.ndarray] = {}
    for name in names:
        href, scale, offset = _href(item, name, sensor, cfg)
        with rasterio.open(href) as src:
            if src.crs != reference.crs:
                raise ImageryError("bandes de projections différentes")
            # Same window in the band's own grid (20 m SCL, 100 m thermal resampled to 30 m…).
            from rasterio.windows import from_bounds

            bounds = rasterio.windows.bounds(window, reference.transform)
            own = from_bounds(*bounds, transform=src.transform)
            raw = src.read(1, window=own, out_shape=shape, boundless=True, fill_value=0)
        if name == spec["mask"]:
            arrays["mask"] = raw
        elif name == spec.get("cloud_prob"):
            arrays["cloud_prob"] = raw  # % cloud probability (Sentinel-2 L2A CLD)
        else:
            values = raw.astype(np.float64)
            values = np.where(raw == 0, np.nan, values * scale + offset)
            arrays[name] = values
    return arrays


def _to_lonlat(reference: Any, window: Any) -> Callable[[float, float], Tuple[float, float]]:
    from rasterio.warp import transform as warp
    from rasterio.windows import transform as window_transform

    affine = window_transform(window, reference.transform)

    def convert(row: float, col: float) -> Tuple[float, float]:
        x, y = affine * (col + 0.5, row + 0.5)
        lon, lat = warp(reference.crs, "EPSG:4326", [x], [y])
        return float(lon[0]), float(lat[0])

    return convert


def detect_window(item: Dict[str, Any], sensor: str, window: Any, reference: Any, cfg: Settings,
                  *, chips: bool = True, eez: Optional[Dict[str, Any]] = None) -> Tuple[List[Dict[str, Any]], Dict[str, float]]:
    """Vessels in one window of one scene, geolocated; plus the window's water / cloud shares."""
    spec = SENSORS[sensor]
    bands = _read_bands(item, sensor, window, reference, cfg)
    water, invalid = spec["masks"](bands["mask"])
    if "cloud_prob" in bands:
        invalid = invalid | (bands["cloud_prob"] >= 20)
    nir = bands[spec["nir"]]
    water = optical_detection.water_mask(nir, bands.get("green"), water, invalid)
    thermal = bands.get(spec["thermal"]) if spec["thermal"] else None
    targets = optical_detection.detect(nir, water, spec["pixel_m"], k=cfg.optical_cfar_k, thermal_k=thermal, invalid=invalid,
                                       swir=bands.get(spec["swir"]), green=bands.get("green"))
    shares = {"water": float(water.mean()), "cloud": float(invalid.mean())}
    if not targets:
        return [], shares
    rgb = np.dstack([bands[b] for b in spec["rgb"]])
    to_lonlat = _to_lonlat(reference, window)
    props = item.get("properties") or {}
    result = []
    for t in targets:
        lon, lat = to_lonlat(t["row"], t["col"])
        if eez is not None and not point_in_geometry(lon, lat, eez):
            continue
        if _fixed_structure(lon, lat, cfg):
            continue  # quay, jetty, breakwater, beacon mapped by OpenStreetMap
        attributes = {k: t[k] for k in ("pixels", "elongation", "nir", "water_nir", "contrast", "swir_delta", "bt_k", "water_bt_k",
                                        "thermal_delta_k") if k in t}
        attributes["cloud_cover_scene"] = props.get("eo:cloud_cover")
        attributes["window_cloud_pct"] = round(shares["cloud"] * 100)
        # Small cumulus are the residual false alarm of optical detection: the cloudier the
        # surroundings, the less a lone optical target weighs (fusion needs corroboration).
        confidence = round(max(0.2, t["confidence"] * (1 - 0.6 * shares["cloud"])), 2)
        if chips:
            attributes["chip"] = optical_detection.chip_png(rgb, t["row"], t["col"])
        result.append({
            "sensor": sensor, "platform": str(props.get("platform") or ""), "scene_id": item["id"],
            "acquired_at": props.get("datetime"), "time_uncertainty_min": 0,
            "lat": round(lat, 5), "lon": round(lon, 5), "length_m": t["length_m"], "confidence": confidence,
            "attributes": attributes,
        })
    return result, shares


def _fixed_structure(lon: float, lat: float, cfg: Settings) -> bool:
    """On land, on the shoreline (< 60 m) or on a mapped fixed structure (French Guiana coastline data)."""
    try:
        from app.sources import coastline

        located = coastline.locate(lon, lat, cfg)
        if located and (located.get("on_land") or located.get("distance_m", 1e9) < 60):
            return True
        return coastline.nearest_structure(lon, lat, 80.0, cfg) is not None
    except Exception:
        return False


def _reference(item: Dict[str, Any], sensor: str, cfg: Settings) -> Any:
    import rasterio

    href, _scale, _offset = _href(item, SENSORS[sensor]["nir"], sensor, cfg)
    return rasterio.open(href)


def scan_area(lat: float, lon: float, radius_km: float, days: int, settings: Optional[Settings] = None,
              sensors: Tuple[str, ...] = ("s2_msi", "landsat_oli"), max_scenes: int = 4) -> Dict[str, Any]:
    """On-demand analysis around a point: the latest usable scenes of each sensor, vessels with chips."""
    from rasterio.warp import transform_bounds
    from rasterio.windows import from_bounds

    cfg = settings or get_settings()
    dlat = radius_km / 110.57
    dlon = radius_km / (111.32 * max(math.cos(math.radians(lat)), 1e-6))
    bbox = (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
    end = _now()
    start = end - datetime.timedelta(days=days)
    scenes: List[Dict[str, Any]] = []
    detections: List[Dict[str, Any]] = []
    errors: List[str] = []
    for sensor in sensors:
        try:
            items = search(sensor, bbox, start, end, cfg.optical_max_cloud, cfg, limit=max_scenes)
        except ImageryError as exc:
            errors.append(str(exc))
            continue
        for item in items[:max_scenes]:
            summary = {"sensor": sensor, "label": SENSORS[sensor]["label"], "scene_id": item["id"],
                       "acquired_at": (item.get("properties") or {}).get("datetime"),
                       "cloud_cover": (item.get("properties") or {}).get("eo:cloud_cover")}
            try:
                with _env(cfg):
                    with _reference(item, sensor, cfg) as reference:
                        bounds = transform_bounds("EPSG:4326", reference.crs, *bbox)
                        window = from_bounds(*bounds, transform=reference.transform).round_offsets().round_lengths()
                        window = window.intersection(rasterio_window_full(reference))
                        found, shares = detect_window(item, sensor, window, reference, cfg)
                summary.update(water_pct=round(shares["water"] * 100), cloud_pct=round(shares["cloud"] * 100),
                               detections=len(found))
                detections.extend(found)
            except Exception as exc:  # one unreadable scene must not hide the others
                logger.warning("Optical scan of %s failed: %s", item.get("id"), exc)
                summary["error"] = str(exc)[:200]
            scenes.append(summary)
    detections = merge_duplicates(detections)
    for det in detections:
        from app.core.geo import haversine_km

        det["distance_km"] = round(haversine_km(lat, lon, det["lat"], det["lon"]), 2)
    detections.sort(key=lambda d: (d["acquired_at"] or "", -d["confidence"]), reverse=True)
    return {"center": {"lat": lat, "lon": lon}, "radius_km": radius_km, "days": days, "scenes": scenes,
            "detections": detections, "errors": errors}


def merge_duplicates(detections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One detection per vessel and acquisition.

    Overlapping tiles of the same pass (Sentinel-2 MGRS tiles overlap by ~10 km)
    see the same vessels twice, and a large hull with its wake can split into
    fragments: detections of the same platform within a minute and closer than
    half the longest of them (60 m at least) are merged, the most confident kept.
    """
    from app.core.geo import haversine_km

    def instant(d: Dict[str, Any]) -> float:
        try:
            return datetime.datetime.fromisoformat(str(d["acquired_at"]).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0.0

    kept: List[Dict[str, Any]] = []
    for det in sorted(detections, key=lambda d: (-d["confidence"], -(d.get("length_m") or 0))):
        duplicate = False
        for other in kept:
            if other["sensor"] != det["sensor"] or abs(instant(other) - instant(det)) > 60:
                continue
            gate_km = max(0.06, max(other.get("length_m") or 0, det.get("length_m") or 0) / 2000 + 0.03)
            if haversine_km(other["lat"], other["lon"], det["lat"], det["lon"]) <= gate_km:
                duplicate = True
                other.setdefault("attributes", {})["merged"] = int(other["attributes"].get("merged", 0)) + 1
                break
        if not duplicate:
            kept.append(det)
    return kept


def rasterio_window_full(src: Any) -> Any:
    from rasterio.windows import Window

    return Window(0, 0, src.width, src.height)


def _scene_windows(item: Dict[str, Any], sensor: str, reference: Any, eez: Dict[str, Any], cfg: Settings) -> List[Any]:
    """Water-dominant, little-cloud windows of a scene whose centre is inside the EEZ (best first)."""
    import rasterio
    from rasterio.windows import Window

    spec = SENSORS[sensor]
    href, _s, _o = _href(item, spec["mask"], sensor, cfg)
    factor = 16
    with rasterio.open(href) as src:
        overview = src.read(1, out_shape=(max(1, reference.height // factor), max(1, reference.width // factor)))
    water, invalid = spec["masks"](overview)
    block = max(1, WINDOW_PX // factor)
    candidates = []
    to_lonlat = _to_lonlat(reference, rasterio_window_full(reference))
    for r0 in range(0, overview.shape[0], block):
        for c0 in range(0, overview.shape[1], block):
            w = water[r0:r0 + block, c0:c0 + block]
            if w.size == 0:
                continue
            water_share = float(w.mean())
            cloud_share = float(invalid[r0:r0 + block, c0:c0 + block].mean())
            if water_share < 0.2 or cloud_share > 0.7:
                continue
            row, col = r0 * factor, c0 * factor
            lon, lat = to_lonlat(row + WINDOW_PX / 2, col + WINDOW_PX / 2)
            if not point_in_geometry(lon, lat, eez):
                continue
            window = Window(col, row, min(WINDOW_PX, reference.width - col), min(WINDOW_PX, reference.height - row))
            candidates.append((water_share * (1 - cloud_share), window))
    candidates.sort(key=lambda c: c[0], reverse=True)
    return [w for _score, w in candidates[: cfg.optical_max_windows]]


def scan_scene(item: Dict[str, Any], sensor: str, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Whole-scene analysis (automatic watch): bounded number of windows over EEZ waters."""
    from app.sources.marine_regions import eez_geometry

    cfg = settings or get_settings()
    eez = eez_geometry(cfg)
    found: List[Dict[str, Any]] = []
    with _env(cfg):
        with _reference(item, sensor, cfg) as reference:
            for window in _scene_windows(item, sensor, reference, eez, cfg):
                try:
                    dets, _shares = detect_window(item, sensor, window, reference, cfg, eez=eez)
                    found.extend(dets)
                except Exception as exc:
                    logger.warning("Optical window of %s failed: %s", item.get("id"), exc)
    return merge_duplicates(found)
