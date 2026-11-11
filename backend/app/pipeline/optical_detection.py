"""Vessel detection in multispectral imagery (Sentinel-2 MSI 10 m, Landsat 8/9 OLI 30 m + TIRS).

Physics: open water absorbs near-infrared light (reflectance ≈ 0.01-0.05, more in
the very turbid Amazon plume along French Guiana); a hull, a deck or a wake
reflects it (0.1-0.4). Ships therefore stand out in the NIR band against the
surrounding water. The detector is a local CFAR on NIR reflectance restricted to
**cloud-free water pixels** (scene classification / quality band + NDWI):

1. water mask = classified water (or NDWI > 0) minus clouds, cirrus, shadows and
   saturated pixels; land, clouds and their edges never produce a target;
2. local two-parameter CFAR on NIR (background 41 px, guard 11 px), threshold
   ``k`` local standard deviations *and* at least ``MIN_DELTA_REFLECTANCE``
   above the local water level (turbid-water texture is not a ship);
3. connected blobs within a size range; blobs touching a non-water pixel
   (shoreline, cloud edge, sand bank) are discarded;
4. per target: length (pixel extent), elongation, contrast, confidence; with a
   thermal band (Landsat), the brightness-temperature difference between the
   target and the surrounding water (a running engine / warm hull is a hint;
   the 100 m TIRS resolution only resolves large vessels).

Pure numpy: the readers in :mod:`app.sources.multispectral` feed it arrays.
"""

from __future__ import annotations

import base64
import io
from typing import Any, Dict, List, Optional

import numpy as np

from app.pipeline.detection import _elongation, cfar_stats, label_blobs

MIN_DELTA_REFLECTANCE = 0.02
MIN_PIXELS = 2
MAX_LENGTH_M = 400.0
EDGE_BUFFER_PX = 2
CLOUD_BUFFER_M = 150.0      # cloud / shadow edges (haze, sub-pixel cumulus) are never searched
SHADOW_SEARCH_M = 600.0     # a small cumulus casts its shadow a few hundred metres away
SHADOW_SPREAD_K = 4.0        # "dark" = below the surrounding water by 4 robust deviations (MAD)…
SHADOW_MIN_DELTA = 0.004     # …and by at least this reflectance (clear-water noise is not a shadow)
SHADOW_AREA_RATIO = 0.3      # a cumulus shadow is about as large as the cloud; a hull's shadow is a sliver
PIER_ELONGATION = 8.0        # very thin and long: jetty, pipeline, wake without its ship
PIER_MIN_LENGTH_M = 150.0
SWIR_MIN_DELTA = 0.012      # hull / deck reflect shortwave infrared, water (even turbid) and foam absorb it
COLD_CLOUD_DELTA_K = -1.5    # thermal band: clouds are colder than the sea, hulls are not
THERMAL_RING_PX = (3, 8)


def _dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """Binary dilation by a square of side 2·radius+1 (no scipy)."""
    out = mask.copy()
    for dr in range(-radius, radius + 1):
        for dc in range(-radius, radius + 1):
            if dr == 0 and dc == 0:
                continue
            shifted = np.zeros_like(mask)
            rs = slice(max(dr, 0), mask.shape[0] + min(dr, 0))
            rd = slice(max(-dr, 0), mask.shape[0] + min(-dr, 0))
            cs = slice(max(dc, 0), mask.shape[1] + min(dc, 0))
            cd = slice(max(-dc, 0), mask.shape[1] + min(-dc, 0))
            shifted[rs, cs] = mask[rd, cd]
            out |= shifted
    return out


def water_mask(nir: np.ndarray, green: Optional[np.ndarray], classified_water: Optional[np.ndarray],
               invalid: Optional[np.ndarray]) -> np.ndarray:
    """Cloud-free water pixels: the sensor's classification when available, NDWI > 0 otherwise."""
    if classified_water is not None:
        water = classified_water.astype(bool)
    elif green is not None:
        with np.errstate(invalid="ignore", divide="ignore"):
            ndwi = (green - nir) / (green + nir)
        water = np.nan_to_num(ndwi, nan=-1.0) > 0.0
    else:
        water = np.ones(nir.shape, dtype=bool)
    if invalid is not None:
        water &= ~invalid.astype(bool)
    return water & np.isfinite(nir)


def detect(
    nir: np.ndarray,
    water: np.ndarray,
    pixel_m: float,
    *,
    k: float = 6.0,
    thermal_k: Optional[np.ndarray] = None,
    invalid: Optional[np.ndarray] = None,
    swir: Optional[np.ndarray] = None,
    green: Optional[np.ndarray] = None,
) -> List[Dict[str, Any]]:
    """Ship candidates (pixel space) in one window.

    ``water`` is the cloud-free water mask, ``invalid`` the clouds / shadows /
    no-data. Vessels themselves are often classified "not water" by scene
    classifiers: small non-water patches enclosed by water are part of the
    search area, while the *background statistics* use water only.
    """
    nir = np.asarray(nir, dtype=np.float64)
    if nir.size == 0 or not water.any():
        return []
    max_px = int((MAX_LENGTH_M / pixel_m) ** 2 / 4) + 4
    blocked = invalid.astype(bool) if invalid is not None else np.zeros(water.shape, dtype=bool)
    # Scene classifiers often call a bright hull "cloud": a "cloud" smaller than a ship stays a candidate
    # (the shadow, SWIR and thermal tests below sort real cumulus out).
    blocked &= ~_small_patches(blocked, max_px)
    sea_area = _fill_small_holes(water | blocked, max_px=max_px)
    sea_area &= ~_dilate(~np.isfinite(nir), max(EDGE_BUFFER_PX, int(np.ceil(CLOUD_BUFFER_M / pixel_m))))  # no data, swath edge
    sea_area &= ~_dilate(blocked, max(EDGE_BUFFER_PX, int(np.ceil(CLOUD_BUFFER_M / pixel_m))))
    background = np.where(water, nir, 0.0)
    values, mean, std = cfar_stats(np.where(background > 0, background, 0.0))
    raw = np.where(sea_area & np.isfinite(nir), nir, 0.0)
    sigma = np.where(np.isfinite(std), np.maximum(std, 0.003), np.inf)
    with np.errstate(invalid="ignore"):
        mask = sea_area & (raw > mean + k * sigma) & (raw > mean + MIN_DELTA_REFLECTANCE)
    outside = _dilate(~sea_area, EDGE_BUFFER_PX)
    targets: List[Dict[str, Any]] = []
    for pixels in label_blobs(mask):
        if len(pixels) < MIN_PIXELS:
            continue
        rows, cols = pixels[:, 0], pixels[:, 1]
        extent = float(np.hypot(rows.max() - rows.min() + 1, cols.max() - cols.min() + 1)) * pixel_m
        if extent > MAX_LENGTH_M:
            continue  # sand bank, foam line, raft of debris
        if outside[rows, cols].any():
            continue  # touches the shore, a cloud or the image edge
        if extent >= PIER_MIN_LENGTH_M and _elongation(pixels) >= PIER_ELONGATION:
            continue  # thin straight line: jetty, pipeline or a wake
        if _shadow_nearby(green if green is not None else nir, water, rows, cols, pixel_m):
            continue  # bright speck + dark patch next to it: a small cumulus and its shadow, not a hull
        blob = raw[rows, cols]
        peak = int(np.argmax(blob))
        row, col = int(rows[peak]), int(cols[peak])
        local_mean = float(mean[row, col]) if np.isfinite(mean[row, col]) else 0.0
        local_std = float(sigma[row, col]) if np.isfinite(sigma[row, col]) else 0.01
        contrast = (float(raw[row, col]) - local_mean) / max(local_std, 1e-6)
        confidence = 0.5 + 0.45 * float(np.tanh(max(0.0, contrast - k) / (2 * k)))
        if len(pixels) == MIN_PIXELS:
            confidence -= 0.1  # two pixels: a buoy or a breaking wave is as likely as a boat
        target = {
            "row": row, "col": col, "pixels": int(len(pixels)),
            "length_m": round(max(extent, pixel_m), 1),
            "elongation": round(_elongation(pixels), 2),
            "nir": round(float(raw[row, col]), 4), "water_nir": round(local_mean, 4),
            "contrast": round(contrast, 1),
            "confidence": round(min(max(confidence, 0.3), 0.95), 2),
        }
        if swir is not None:
            swir_delta = _ring_contrast(swir, water, row, col)
            if swir_delta is not None:
                target["swir_delta"] = round(swir_delta, 4)
                if swir_delta < SWIR_MIN_DELTA:
                    continue  # bright in NIR only: turbidity front, sediment plume, foam — not a hull
        if thermal_k is not None:
            target.update(_thermal(thermal_k, water, row, col))
            if target.get("thermal_delta_k", 0.0) < COLD_CLOUD_DELTA_K:
                continue  # colder than the sea around it: a cloud top, not a hull
        targets.append(target)
    targets.sort(key=lambda t: t["contrast"], reverse=True)
    return targets


def _shadow_nearby(band: np.ndarray, water: np.ndarray, rows: np.ndarray, cols: np.ndarray, pixel_m: float) -> bool:
    """Is there a cloud-shadow-like dark patch near the blob, about as large as the blob (a cumulus)?"""
    radius = int(np.ceil(SHADOW_SEARCH_M / pixel_m))
    r0, r1 = max(0, int(rows.min()) - radius), min(band.shape[0], int(rows.max()) + radius + 1)
    c0, c1 = max(0, int(cols.min()) - radius), min(band.shape[1], int(cols.max()) + radius + 1)
    patch = band[r0:r1, c0:c1]
    sea = water[r0:r1, c0:c1] & np.isfinite(patch)
    if sea.sum() < 10:
        return False
    values = patch[sea]
    level = float(np.median(values))
    spread = 1.4826 * float(np.median(np.abs(values - level)))
    with np.errstate(invalid="ignore"):
        dark = sea & (patch < level - max(SHADOW_SPREAD_K * spread, SHADOW_MIN_DELTA))
    return int(dark.sum()) >= max(4, SHADOW_AREA_RATIO * len(rows))


def _small_patches(mask: np.ndarray, max_px: int) -> np.ndarray:
    """Pixels of ``mask`` belonging to connected patches of at most ``max_px`` pixels."""
    if not mask.any():
        return np.zeros(mask.shape, dtype=bool)
    from rasterio.features import sieve

    return mask & ~sieve(mask.astype(np.uint8), size=max(2, max_px + 1), connectivity=8).astype(bool)


def _fill_small_holes(water: np.ndarray, max_px: int) -> np.ndarray:
    """Water plus enclosed non-water patches of at most ``max_px`` pixels (vessels inside the sea).

    ``rasterio.features.sieve`` merges every patch smaller than the size into its
    surroundings (it also drops tiny water pockets inland, which is welcome).
    """
    from rasterio.features import sieve

    return sieve(water.astype(np.uint8), size=max(2, max_px + 1), connectivity=8).astype(bool)


def _ring_contrast(band: np.ndarray, water: np.ndarray, row: int, col: int) -> Optional[float]:
    """Peak of the 3×3 target cell minus the median of the surrounding water ring."""
    inner, outer = THERMAL_RING_PX
    r0, r1 = max(0, row - outer), min(band.shape[0], row + outer + 1)
    c0, c1 = max(0, col - outer), min(band.shape[1], col + outer + 1)
    rr, cc = np.mgrid[r0:r1, c0:c1]
    ring = (np.maximum(abs(rr - row), abs(cc - col)) >= inner) & water[r0:r1, c0:c1] & np.isfinite(band[r0:r1, c0:c1])
    cell = band[max(0, row - 1):row + 2, max(0, col - 1):col + 2]
    if not ring.any() or not np.isfinite(cell).any():
        return None
    return float(np.nanmax(cell)) - float(np.median(band[r0:r1, c0:c1][ring]))


def _thermal(bt: np.ndarray, water: np.ndarray, row: int, col: int) -> Dict[str, Any]:
    """Brightness temperature of the target vs. the water ring around it (K)."""
    inner, outer = THERMAL_RING_PX
    r0, r1 = max(0, row - outer), min(bt.shape[0], row + outer + 1)
    c0, c1 = max(0, col - outer), min(bt.shape[1], col + outer + 1)
    rr, cc = np.mgrid[r0:r1, c0:c1]
    ring = (np.maximum(abs(rr - row), abs(cc - col)) >= inner) & water[r0:r1, c0:c1] & np.isfinite(bt[r0:r1, c0:c1])
    target = bt[max(0, row - 1):row + 2, max(0, col - 1):col + 2]
    if not ring.any() or not np.isfinite(target).any():
        return {}
    water_bt = float(np.median(bt[r0:r1, c0:c1][ring]))
    peak_bt = float(np.nanmax(target))
    return {"bt_k": round(peak_bt, 2), "water_bt_k": round(water_bt, 2), "thermal_delta_k": round(peak_bt - water_bt, 2)}


def chip_png(rgb: np.ndarray, row: int, col: int, half: int = 32) -> str:
    """True-colour crop around a target, contrast-stretched, as a ``data:image/png`` URI."""
    from PIL import Image

    top, left = max(0, row - half), max(0, col - half)
    crop = np.asarray(rgb[top:row + half, left:col + half], dtype=np.float64)
    if crop.size == 0:
        return ""
    finite = crop[np.isfinite(crop)]
    low, high = (np.percentile(finite, 2), np.percentile(finite, 99.5)) if finite.size else (0.0, 1.0)
    scaled = np.clip((np.nan_to_num(crop, nan=low) - low) / max(high - low, 1e-6), 0, 1)
    image = Image.fromarray((scaled * 255).astype(np.uint8)).resize((crop.shape[1] * 3, crop.shape[0] * 3), Image.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
