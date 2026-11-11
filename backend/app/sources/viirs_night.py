"""Night lights of vessels at sea — VIIRS Day/Night Band (DNB) + thermal band I5, NASA GIBS, no key.

Fishing vessels working at night (lamparo / light-luring, deck lights of
trawlers and tapouilles, large vessels) are visible from space as isolated
points of light on a dark sea. Three VIIRS satellites (Suomi NPP, NOAA-20,
NOAA-21) image French Guiana around 01:30 local time every night (~750 m).

NASA GIBS serves the calibrated products as colour-mapped images with their
colour maps; decoding the colour map gives back the physical quantity:

* DNB at-sensor radiance (nW·cm⁻²·sr⁻¹): the lights;
* I5 brightness temperature (K, 11.45 µm, 375 m): cloud screening — moonlit
  cloud tops are bright in the DNB but cold (< ``CLOUD_BT_K``) in I5, the
  tropical sea is ~300 K.

Detection (per night and satellite) on the EEZ grid (~0.0045°):
sea mask = EEZ polygon eroded by ``COAST_BUFFER_PX`` (no coastal town or port
lights), local CFAR on radiance (background 31 px, guard 5 px) with
``night_light_k`` deviations and a minimum radiance step, isolated blobs only
(≤ ``MAX_LIGHT_PX`` px: a city or a lit cloud is larger), cloud-free
surroundings. Acquisition time is the nominal night pass (± 1 h): GIBS daily
composites do not carry per-pixel time.
"""

from __future__ import annotations

import datetime
import io
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT, atomic_write

logger = logging.getLogger(__name__)

WMS = "https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi"
COLORMAP = "https://gibs.earthdata.nasa.gov/colormaps/v1.3/{name}.xml"
SATELLITES = {
    "snpp": {"label": "Suomi NPP", "dnb": "VIIRS_SNPP_DayNightBand_At_Sensor_Radiance",
             "i5": "VIIRS_SNPP_Brightness_Temp_BandI5_Night", "pass_utc": (4, 30)},
    "noaa20": {"label": "NOAA-20", "dnb": "VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance",
               "i5": "VIIRS_NOAA20_Brightness_Temp_BandI5_Night", "pass_utc": (3, 40)},
    "noaa21": {"label": "NOAA-21", "dnb": "VIIRS_NOAA21_DayNightBand_At_Sensor_Radiance",
               "i5": "VIIRS_NOAA21_Brightness_Temp_BandI5_Night", "pass_utc": (4, 5)},
}
COLORMAPS = {"dnb": "VIIRS_DayNightBand_At_Sensor_Radiance", "i5": "VIIRS_Brightness_Temp_BandI5"}
DEGREES_PER_PX = 0.0045
COAST_BUFFER_PX = 4           # ~2 km: harbour and coastal town lights excluded
MAX_LIGHT_PX = 9              # an isolated vessel (or a small group) — larger is a city or a lit cloud
MIN_RADIANCE_STEP = 2.0       # nW·cm⁻²·sr⁻¹ above the local background
CLOUD_BT_K = 285.0            # I5 colder than this around the light: cloud (absolute floor)…
CLOUD_BT_DROP_K = 4.0         # …or colder than the night's sea (median) by this much: low warm tropical clouds
CLOUD_SEARCH_PX = 3
TIME_UNCERTAINTY_MIN = 60


class NightError(RuntimeError):
    pass


# --------------------------------------------------------------------------- colour maps


def parse_colormap(xml: str) -> Dict[int, float]:
    """{packed RGB: physical value (interval midpoint)} from a GIBS v1.3 colour map; no-data entries skipped."""
    table: Dict[int, float] = {}
    for entry in re.finditer(r"<ColorMapEntry ([^>]*)/>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', entry.group(1)))
        if attrs.get("nodata") == "true" or "value" not in attrs:
            continue
        numbers = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?", attrs["value"])]
        if not numbers:
            continue
        if len(numbers) >= 2 and numbers[1] > 2 * max(numbers[0], 1e-6) + 1:
            value = numbers[0]  # open-ended saturation class: its lower bound is what is known
        else:
            value = sum(numbers[:2]) / min(len(numbers), 2)
        r, g, b = (int(x) for x in attrs["rgb"].split(","))
        table[(r << 16) | (g << 8) | b] = value
    return table


def decode(rgba: np.ndarray, table: Dict[int, float]) -> np.ndarray:
    """Physical values of a colour-mapped RGBA image (NaN for transparent / unknown colours)."""
    packed = (rgba[..., 0].astype(np.int64) << 16) | (rgba[..., 1].astype(np.int64) << 8) | rgba[..., 2].astype(np.int64)
    keys = np.array(sorted(table), dtype=np.int64)
    values = np.array([table[k] for k in keys], dtype=np.float64)
    if keys.size == 0:
        return np.full(packed.shape, np.nan)
    index = np.clip(np.searchsorted(keys, packed), 0, keys.size - 1)
    out = np.where(keys[index] == packed, values[index], np.nan)
    if rgba.shape[-1] == 4:
        out = np.where(rgba[..., 3] > 0, out, np.nan)
    return out


def _cached(path: Path, fetch: Any) -> bytes:
    if path.is_file():
        return path.read_bytes()
    data = fetch()
    atomic_write(path, data)
    return data


def _get(url: str, params: Optional[Dict[str, Any]], cfg: Settings) -> bytes:
    import httpx

    try:
        response = httpx.get(url, params=params, headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout * 4)
        response.raise_for_status()
    except Exception as exc:
        health.record("viirs_gibs", False, type(exc).__name__)
        raise NightError(f"NASA GIBS injoignable ({type(exc).__name__})") from exc
    health.record("viirs_gibs", True)
    return response.content


def colormap(kind: str, cfg: Settings) -> Dict[int, float]:
    name = COLORMAPS[kind]
    xml = _cached(Path(cfg.cache_dir) / "viirs" / f"{name}.xml", lambda: _get(COLORMAP.format(name=name), None, cfg))
    return parse_colormap(xml.decode("utf-8"))


def grid(cfg: Settings) -> Tuple[Tuple[float, float, float, float], int, int]:
    west, south, east, north = cfg.eez_bbox
    return (west, south, east, north), int(round((east - west) / DEGREES_PER_PX)), int(round((north - south) / DEGREES_PER_PX))


def fetch_layer(layer: str, day: datetime.date, cfg: Settings) -> np.ndarray:
    """RGBA image of a GIBS layer on the EEZ grid for one day (cached once complete)."""
    from PIL import Image

    bbox, width, height = grid(cfg)
    params = {"SERVICE": "WMS", "REQUEST": "GetMap", "VERSION": "1.1.1", "LAYERS": layer, "SRS": "EPSG:4326",
              "BBOX": ",".join(str(v) for v in bbox), "WIDTH": width, "HEIGHT": height, "FORMAT": "image/png",
              "TRANSPARENT": "TRUE", "TIME": day.isoformat()}
    path = Path(cfg.cache_dir) / "viirs" / f"{layer}_{day.isoformat()}.png"
    complete = day < datetime.datetime.now(datetime.timezone.utc).date() - datetime.timedelta(days=1)
    data = _cached(path, lambda: _get(WMS, params, cfg)) if complete else _get(WMS, params, cfg)
    return np.array(Image.open(io.BytesIO(data)).convert("RGBA"))


# --------------------------------------------------------------------------- detection


def _erode(mask: np.ndarray, radius: int) -> np.ndarray:
    from app.pipeline.optical_detection import _dilate

    return ~_dilate(~mask, radius)


def sea_mask(cfg: Settings, shape: Tuple[int, int]) -> np.ndarray:
    """EEZ waters on the grid, away from the coast."""
    from rasterio.features import rasterize
    from rasterio.transform import from_bounds

    from app.sources.marine_regions import eez_geometry

    bbox, width, height = grid(cfg)
    transform = from_bounds(*bbox, width, height)
    mask = rasterize([(eez_geometry(cfg), 1)], out_shape=shape, transform=transform, fill=0, dtype="uint8").astype(bool)
    return _erode(mask, COAST_BUFFER_PX)


def detect_lights(radiance: np.ndarray, sea: np.ndarray, bt: Optional[np.ndarray], k: float) -> List[Dict[str, Any]]:
    """Isolated lights at sea (pixel space)."""
    from app.pipeline.detection import cfar_stats, label_blobs

    values = np.where(sea & np.isfinite(radiance), np.maximum(radiance, 0.0) + 1e-3, 0.0)
    _v, mean, std = cfar_stats(values, background=31, guard=5)
    sigma = np.where(np.isfinite(std), np.maximum(std, 0.3), np.inf)
    with np.errstate(invalid="ignore"):
        mask = sea & (values > mean + k * sigma) & (values > mean + MIN_RADIANCE_STEP)
    cloud_limit = CLOUD_BT_K
    if bt is not None and np.isfinite(bt[sea]).any():
        cloud_limit = max(CLOUD_BT_K, float(np.nanmedian(bt[sea])) - CLOUD_BT_DROP_K)
    lights = []
    for pixels in label_blobs(mask):
        if len(pixels) > MAX_LIGHT_PX:
            continue
        rows, cols = pixels[:, 0], pixels[:, 1]
        peak = int(np.argmax(values[rows, cols]))
        row, col = int(rows[peak]), int(cols[peak])
        cloud_bt = None
        if bt is not None:
            r0, r1 = max(0, row - CLOUD_SEARCH_PX), row + CLOUD_SEARCH_PX + 1
            c0, c1 = max(0, col - CLOUD_SEARCH_PX), col + CLOUD_SEARCH_PX + 1
            around = bt[r0:r1, c0:c1]
            if np.isfinite(around).any():
                cloud_bt = float(np.nanmin(around))
                if cloud_bt < cloud_limit:
                    continue  # moonlit or lightning-lit cloud
        radiance_peak = float(values[row, col])
        contrast = (radiance_peak - float(mean[row, col])) / float(sigma[row, col])
        confidence = 0.55 + 0.35 * float(np.tanh(max(0.0, contrast - k) / (2 * k)))
        if bt is None or cloud_bt is None:
            confidence -= 0.15  # no thermal screening available
        lights.append({"row": row, "col": col, "pixels": int(len(pixels)), "radiance": round(radiance_peak, 2),
                       "background": round(float(mean[row, col]), 2), "contrast": round(contrast, 1),
                       "min_bt_k": round(cloud_bt, 1) if cloud_bt is not None else None,
                       "confidence": round(min(max(confidence, 0.3), 0.9), 2)})
    return lights


def scan_night(satellite: str, day: datetime.date, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Lights at sea seen by one satellite during the night of ``day`` (UTC date of the pass)."""
    cfg = settings or get_settings()
    spec = SATELLITES[satellite]
    radiance = decode(fetch_layer(spec["dnb"], day, cfg), colormap("dnb", cfg))
    coverage = float(np.isfinite(radiance).mean())
    if coverage < 0.05:
        return {"satellite": satellite, "day": day.isoformat(), "available": False, "detections": []}
    try:
        bt = decode(fetch_layer(spec["i5"], day, cfg), colormap("i5", cfg))
    except NightError:
        bt = None
    sea = sea_mask(cfg, radiance.shape)
    lights = detect_lights(radiance, sea, bt, cfg.night_light_k)
    (west, _south, _east, north), _w, _h = grid(cfg)
    hour, minute = spec["pass_utc"]
    acquired = datetime.datetime.combine(day, datetime.time(hour, minute), tzinfo=datetime.timezone.utc)
    detections = []
    for light in lights:
        lat = north - (light["row"] + 0.5) * DEGREES_PER_PX
        lon = west + (light["col"] + 0.5) * DEGREES_PER_PX
        detections.append({
            "sensor": "viirs_dnb", "platform": spec["label"], "scene_id": f"{satellite}:{day.isoformat()}",
            "acquired_at": acquired.isoformat(), "time_uncertainty_min": TIME_UNCERTAINTY_MIN,
            "lat": round(lat, 4), "lon": round(lon, 4), "length_m": None, "confidence": light["confidence"],
            "attributes": {k: light[k] for k in ("pixels", "radiance", "background", "contrast", "min_bt_k")},
        })
    return {"satellite": satellite, "day": day.isoformat(), "available": True, "coverage": round(coverage, 3),
            "cloud_screening": bt is not None, "detections": detections}
