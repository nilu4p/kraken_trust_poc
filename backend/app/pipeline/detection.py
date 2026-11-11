"""SAR ship detection (CFAR + blob extraction) and time-aware AIS correlation."""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from app.config import Settings, get_settings
from app.core.geo import haversine_km, point_in_geometry
from app.sources import health, xview3

logger = logging.getLogger(__name__)

CHIP_HALF_SIZE = 48
MAX_BRIGHT_PIXELS = 50_000


def _box_sum(values: np.ndarray, size: int) -> np.ndarray:
    """Sum over a centred size×size window (zero padded) using an integral image."""
    half = size // 2
    padded = np.pad(values, ((half + 1, half), (half + 1, half)), mode="constant")
    integral = padded.cumsum(axis=0).cumsum(axis=1)
    return (
        integral[size:, size:]
        - integral[:-size, size:]
        - integral[size:, :-size]
        + integral[:-size, :-size]
    )


def cfar_stats(
    data: np.ndarray,
    background: int = 41,
    guard: int = 11,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Local cell-averaging CFAR statistics.

    For each pixel, mean / std of the clutter in a ``background`` square minus a
    ``guard`` square (so the ship itself does not bias its own threshold).
    No-data pixels (0 / NaN, e.g. GRD borders) are excluded. Returns
    ``(values, mean, std)`` with ``std`` = NaN where too few clutter samples.
    """
    values = np.asarray(data, dtype=np.float64)
    valid = (np.isfinite(values) & (values > 0)).astype(np.float64)
    values = np.where(valid > 0, values, 0.0)
    n = _box_sum(valid, background) - _box_sum(valid, guard)
    s1 = _box_sum(values, background) - _box_sum(values, guard)
    s2 = _box_sum(values * values, background) - _box_sum(values * values, guard)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean = s1 / n
        var = np.maximum(s2 / n - mean * mean, 0.0)
    min_samples = 0.25 * (background * background - guard * guard)
    std = np.where(n >= min_samples, np.sqrt(var), np.nan)
    return values, mean, std


def cfar_mask(data: np.ndarray, k: float = 5.0, background: int = 41, guard: int = 11) -> np.ndarray:
    """Two-parameter CA-CFAR detection mask (pixel brighter than local mean + k·std).

    Adapts to heterogeneous sea clutter (wind streaks, currents, swath edges)
    instead of one global threshold. Replace with a PyTorch inference call
    without changing callers.
    """
    values, mean, std = cfar_stats(data, background, guard)
    if values.size == 0:
        return np.zeros(values.shape, dtype=bool)
    # Homogeneous clutter (std≈0) still needs a margin above the mean.
    sigma = np.where(np.isfinite(std), np.maximum(std, 0.05 * np.abs(mean)), np.inf)
    with np.errstate(invalid="ignore"):
        return (values > 0) & (values > mean + k * sigma)


def label_blobs(mask: np.ndarray) -> List[np.ndarray]:
    """8-connected components of a boolean mask as arrays of (row, col) pixels."""
    rows, cols = np.nonzero(mask)
    if rows.size == 0:
        return []
    if rows.size > MAX_BRIGHT_PIXELS:
        logger.warning("CFAR mask too dense (%s px) — likely land/clutter, skipping window", rows.size)
        return []
    remaining = set(zip(rows.tolist(), cols.tolist()))
    blobs: List[np.ndarray] = []
    while remaining:
        seed = remaining.pop()
        stack = [seed]
        pixels = [seed]
        while stack:
            r, c = stack.pop()
            for dr in (-1, 0, 1):
                for dc in (-1, 0, 1):
                    neighbour = (r + dr, c + dc)
                    if neighbour in remaining:
                        remaining.remove(neighbour)
                        stack.append(neighbour)
                        pixels.append(neighbour)
        blobs.append(np.asarray(pixels, dtype=np.int64))
    return blobs


# Sentinel-1 IW GRDH: 10 m pixels but a resolution of ~20 m (range) × 22 m (azimuth). Every echo is
# spread over about one resolution cell, so a blob is ~20 m longer than the hull that produced it.
SAR_BLUR_M = 20.0


def _major_extent_px(pixels: np.ndarray) -> float:
    """Extent of a blob along its main axis, in pixels (a ship at 45° is not √2 too long)."""
    if len(pixels) < 2:
        return 1.0
    centred = pixels.astype(np.float64) - pixels.mean(axis=0)
    _values, vectors = np.linalg.eigh(np.cov(centred.T))
    projection = centred @ vectors[:, -1]  # eigenvector of the largest eigenvalue
    return float(projection.max() - projection.min() + 1.0)


def hull_length_m(extent_px: float, pixel_m: float) -> float:
    """Hull length from an echo extent: extent minus the radar blur, never below one pixel."""
    return max(pixel_m, extent_px * pixel_m - SAR_BLUR_M)


def _elongation(pixels: np.ndarray) -> float:
    """Major/minor axis ratio of a blob (ships are elongated, speckle is compact)."""
    if len(pixels) < 3:
        return 1.0
    cov = np.cov(pixels.T.astype(np.float64))
    eigen = np.sort(np.linalg.eigvalsh(cov))
    return float(np.sqrt(max(eigen[1], 1e-9) / max(eigen[0], 0.25)))


def _ai_opinion(data: np.ndarray, targets: List[Dict[str, Any]], cfg: Settings, stats: Dict[str, int]) -> Optional[str]:
    """AI second opinion on the CFAR targets of one window (in place). Returns an error message, or None.

    Active mode only: very confident AI boxes that match no CFAR target become candidates
    flagged ``ai_only`` (their SNR is measured like any other target).
    """
    from app.pipeline import ai_detector

    try:
        boxes = ai_detector.detect(data, cfg)
    except ai_detector.AiUnavailable as exc:
        logger.warning("AI detector unavailable: %s", exc)
        return str(exc)
    except Exception as exc:  # never break a scan for the second opinion
        logger.exception("AI detector failed")
        return f"{type(exc).__name__}"
    stats["boxes"] += len(boxes)
    unmatched = ai_detector.annotate(targets, boxes)
    if cfg.ai_detector_mode == 2:
        strong = [b for b in unmatched if b["confidence"] >= cfg.ai_only_min_confidence]
        if strong:
            values, mean, std = cfar_stats(data)
        for box in strong:
            row = int(min(max(round(box["row"]), 0), data.shape[0] - 1))
            col = int(min(max(round(box["col"]), 0), data.shape[1] - 1))
            local_std = max(float(std[row, col]) if np.isfinite(std[row, col]) else 0.0, 0.05 * abs(float(mean[row, col])), 1e-6)
            stats["ai_only"] += 1
            targets.append({
                "row": row, "col": col, "pixels": None,
                "length_m": round(hull_length_m(max(box["h"], box["w"]), cfg.pixel_spacing_m), 1),
                "elongation": round(max(box["h"], box["w"]) / max(min(box["h"], box["w"]), 1.0), 2),
                "snr": round((float(values[row, col]) - float(mean[row, col])) / local_std, 1),
                "confidence": round(box["confidence"], 2),
                "ai_confidence": box["confidence"],
                "ai_only": True,
            })
    return None


def _xview3_opinion(detections: List[Dict[str, Any]], cfg: Settings, stats: Dict[str, Any]) -> None:
    """Information-only xView3 opinion on the final targets (never changes them, never blocks the scan)."""
    from app.pipeline import scan_state

    try:
        scored = xview3.score(detections, cfg)
        error = None if scored or not stats.get("no_copol") else "bande co-polarisée (VV) absente : avis impossible"
    except xview3.Xview3Unavailable as exc:
        logger.warning("xView3 opinion unavailable: %s", exc)
        scored, error = 0, str(exc)
    except Exception as exc:  # the opinion must never break a scan
        logger.exception("xView3 opinion failed")
        scored, error = 0, type(exc).__name__
    for det in detections:
        det.pop("_xview3_chip", None)
    scan_state.add_stats(xview3_scored=scored, xview3_error=error)


def blobs_to_targets(
    data: np.ndarray,
    mask: np.ndarray,
    cfg: Settings,
) -> List[Dict[str, Any]]:
    """Turn CFAR blobs into ship candidates (pixel space) with size, shape and local SNR."""
    values, mean, std = cfar_stats(data)
    targets: List[Dict[str, Any]] = []
    for pixels in label_blobs(mask):
        if not cfg.blob_min_pixels <= len(pixels) <= cfg.blob_max_pixels:
            continue
        blob_values = values[pixels[:, 0], pixels[:, 1]]
        peak_idx = int(np.argmax(blob_values))
        row, col = int(pixels[peak_idx, 0]), int(pixels[peak_idx, 1])
        length_m = hull_length_m(_major_extent_px(pixels), cfg.pixel_spacing_m)
        local_std = float(std[row, col]) if np.isfinite(std[row, col]) else 0.0
        local_std = max(local_std, 0.05 * abs(float(mean[row, col])), 1e-6)
        snr = (float(values[row, col]) - float(mean[row, col])) / local_std
        # Map SNR above the CFAR threshold to a 0.5 .. 0.99 confidence.
        confidence = 0.5 + 0.49 * float(np.tanh(max(0.0, snr - cfg.cfar_k) / (2 * cfg.cfar_k)))
        targets.append(
            {
                "row": row,
                "col": col,
                "pixels": int(len(pixels)),
                "length_m": round(length_m, 1),
                "elongation": round(_elongation(pixels), 2),
                "snr": round(snr, 1),
                "confidence": round(confidence, 2),
                "peak": float(values[row, col]),
            }
        )
    targets.sort(key=lambda t: t["snr"], reverse=True)
    return targets


def dual_pol_confirmed(co_mask: np.ndarray, row: int, col: int, radius: int = 2) -> bool:
    """True if the co-polarised CFAR mask also fires within ``radius`` px of a target."""
    top, left = max(0, row - radius), max(0, col - radius)
    return bool(co_mask[top : row + radius + 1, left : col + radius + 1].any())


def _chip(data: np.ndarray, row: int, col: int) -> np.ndarray:
    top = max(0, row - CHIP_HALF_SIZE)
    left = max(0, col - CHIP_HALF_SIZE)
    return np.array(data[top : row + CHIP_HALF_SIZE, left : col + CHIP_HALF_SIZE], dtype=np.float32)


def _rasterio_env(cfg: Settings) -> Any:
    import boto3
    import rasterio
    from rasterio.session import AWSSession

    if cfg.access_key and cfg.secret_key:
        session = boto3.Session(
            aws_access_key_id=cfg.access_key,
            aws_secret_access_key=cfg.secret_key,
            region_name="default",
        )
        # GDAL expects a bare host for AWS_S3_ENDPOINT (no scheme).
        return rasterio.Env(
            AWSSession(session, endpoint_url=cfg.s3_endpoint_host),
            AWS_VIRTUAL_HOSTING="FALSE",
            AWS_HTTPS="YES",
            GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        )
    logger.warning("S3 credentials missing — attempting unsigned request")
    return rasterio.Env(AWS_NO_SIGN_REQUEST="YES")


def _open_optional(rasterio: Any, url: Optional[str], reference: Any) -> Any:
    """Open the confirmation band if it shares the reference grid, else None."""
    if not url:
        return None
    try:
        other = rasterio.open(url)
    except Exception as exc:
        logger.warning("Second polarisation unavailable (%s) — single-pol detection", exc)
        return None
    if (other.width, other.height) != (reference.width, reference.height):
        logger.warning("Polarisation grids differ — single-pol detection")
        other.close()
        return None
    return other


def _pixel_to_lonlat_fn(src: Any) -> Any:
    """Pixel -> (lon, lat). Sentinel-1 GRD is in radar geometry with ~200 ground control points.

    A thin-plate spline through the GCPs follows the scene geometry to a few metres
    (median 2.5 m between GCPs on a real IW scene), where a single affine fit over the
    250 km swath was off by 68 m median and up to 260 m — enough to put a coastal
    echo on the wrong side of the shore or to miss an AIS match.
    """
    from rasterio.transform import GCPTransformer, from_gcps

    gcps, _crs = src.gcps
    if not gcps:
        transform = src.transform
        return lambda col, row: transform @ (col + 0.5, row + 0.5)
    try:
        transformer = GCPTransformer(gcps, tps=True)

        def to_lonlat(col: float, row: float) -> Tuple[float, float]:
            xs, ys = transformer.xy([row], [col], offset="center")
            return float(xs[0]), float(ys[0])

        to_lonlat(0, 0)
        return to_lonlat
    except Exception as exc:  # GDAL without TPS support: affine fallback
        logger.warning("Thin-plate spline georeferencing unavailable (%s) — affine GCP fit", exc)
        transform = from_gcps(gcps)
        return lambda col, row: transform @ (col + 0.5, row + 0.5)


# --------------------------------------------------------------------------- land masking

LAND_SMOOTH_PX = 15        # 150 m: speckle averaged before judging brightness
LAND_BLOCK_PX = 41         # 410 m: land = mostly bright over this block (a ship never is)
LAND_MARGIN_DB = 6.0       # bright = 6 dB above the tile's sea level (10th percentile)
LAND_NEAR_COAST_M = 3000   # the radar mask is only trusted within 3 km of a mapped coast
# Thresholds checked on a full real scene (Guiana, 2026-10-19): every echo they discard
# was the shore fringe, an islet, a rock, a jetty or a channel buoy.
SHORE_REJECT_M = 80        # within 80 m of the radar shoreline: the mangrove / beach fringe itself
OSM_SHORE_REJECT_M = 50    # on, or within 50 m of, the mapped coastline: islet, rock, seawall
STRUCTURE_REJECT_M = 80    # within 80 m of a mapped pier, breakwater, buoy, beacon or platform
SHORE_SEARCH_PX = 100      # radar shore distance measured up to 1 km


def radar_land_mask(data: np.ndarray, smooth: int = LAND_SMOOTH_PX, block: int = LAND_BLOCK_PX,
                    margin_db: float = LAND_MARGIN_DB) -> np.ndarray:
    """Land as the radar sees it: large areas much brighter than the sea (forest, towns, mangrove).

    In cross-polarisation the sea is very dark and land very bright (volume scattering).
    Speckle is first averaged in dB over ``smooth`` px, then a pixel is land when most of
    the ``block`` around it is ``margin_db`` above the sea level — a ship (even 400 m
    long) covers far too little of a 410 m block. Follows the real shoreline of the day,
    including the migrating Guiana mud banks and mangrove.
    """
    values = np.asarray(data, dtype=np.float64)
    valid = np.isfinite(values) & (values > 0)
    if valid.sum() < 0.1 * values.size:
        return np.zeros(values.shape, dtype=bool)
    db = np.where(valid, 20.0 * np.log10(np.where(valid, values, 1.0)), 0.0)
    count = _box_sum(valid.astype(np.float64), smooth)
    with np.errstate(invalid="ignore", divide="ignore"):
        mean_db = _box_sum(db, smooth) / count
    good = valid & (count >= 0.5 * smooth * smooth)
    if not good.any():
        return np.zeros(values.shape, dtype=bool)
    sea = float(np.percentile(mean_db[good], 10))
    bright = good & (mean_db > sea + margin_db)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = _box_sum(bright.astype(np.float64), block) / _box_sum(good.astype(np.float64), block)
    return np.nan_to_num(share) >= 0.5


def _near_coast_grid(to_lonlat: Any, col_off: int, row_off: int, w: int, h: int, cfg: Settings,
                     samples: int = 9) -> Optional[np.ndarray]:
    """Tile-sized boolean: within ``LAND_NEAR_COAST_M`` of the mapped coast (None: no coastline data)."""
    from app.sources import coastline

    coast = coastline.load(cfg)
    if coast is None:
        return None
    rows = np.linspace(0, h - 1, samples).round().astype(int)
    cols = np.linspace(0, w - 1, samples).round().astype(int)
    near = np.zeros((samples, samples), dtype=bool)
    for i, r in enumerate(rows):
        for j, c in enumerate(cols):
            lon, lat = to_lonlat(col_off + c, row_off + r)
            found = coast.locate(lon, lat)
            near[i, j] = found is not None and (found["on_land"] or found["distance_m"] <= LAND_NEAR_COAST_M)
    if not near.any():
        return np.zeros((h, w), dtype=bool)
    # Nearest sample for every pixel (samples are ~1.3 km apart, well inside the 3 km margin).
    ri = np.clip(np.rint(np.arange(h) * (samples - 1) / max(h - 1, 1)).astype(int), 0, samples - 1)
    ci = np.clip(np.rint(np.arange(w) * (samples - 1) / max(w - 1, 1)).astype(int), 0, samples - 1)
    return near[np.ix_(ri, ci)]


def shore_distance_px(land: np.ndarray, row: int, col: int, radius: int = SHORE_SEARCH_PX) -> Optional[float]:
    """Distance (px) from a pixel to the nearest radar land pixel, None beyond ``radius``."""
    top, left = max(0, row - radius), max(0, col - radius)
    crop = land[top:row + radius + 1, left:col + radius + 1]
    ys, xs = np.nonzero(crop)
    if len(ys) == 0:
        return None
    distance = float(np.hypot(ys + top - row, xs + left - col).min())
    return distance if distance <= radius else None


def coastal_context(target: Dict[str, Any], land: Optional[np.ndarray], cfg: Settings) -> Dict[str, Any]:
    """Where a target stands relative to the shore, from the radar image and the OSM coastline."""
    from app.sources import coastline

    radar_on_land = bool(land is not None and land[target["row"], target["col"]])
    radar_px = shore_distance_px(land, target["row"], target["col"]) if land is not None else None
    osm = coastline.locate(target["lon"], target["lat"], cfg)
    structure = coastline.nearest_structure(target["lon"], target["lat"], 300.0, cfg)
    distances = [d for d in (
        0.0 if radar_on_land else (radar_px * cfg.pixel_spacing_m if radar_px is not None else None),
        (0.0 if osm["on_land"] else float(osm["distance_m"])) if osm else None,
    ) if d is not None]
    return {
        "radar_on_land": radar_on_land,
        "radar_shore_m": round(radar_px * cfg.pixel_spacing_m) if radar_px is not None else None,
        "radar_checked": land is not None,
        "osm_on_land": osm["on_land"] if osm else None,
        "osm_shore_m": osm["distance_m"] if osm else None,
        "osm_known": coastline.load(cfg) is not None,
        "structure": structure,
        # Shortest distance to the shore according to either source (None: > 1 km / open sea).
        "shore_m": round(min(distances)) if distances else None,
    }


def on_land_reason(coast: Dict[str, Any]) -> Optional[str]:
    """Why an echo is the coast or a fixed structure (then it is not a candidate), or None."""
    if coast.get("radar_on_land"):
        return "radar_land"
    if coast.get("radar_shore_m") is not None and coast["radar_shore_m"] <= SHORE_REJECT_M:
        return "shoreline"
    if coast.get("osm_on_land") or (coast.get("osm_shore_m") is not None and coast["osm_shore_m"] <= OSM_SHORE_REJECT_M):
        return "osm_land"
    if coast.get("structure") and coast["structure"]["distance_m"] <= STRUCTURE_REJECT_M:
        return "structure"
    return None


def _select_windows(
    width: int,
    height: int,
    to_lonlat: Any,
    eez: Optional[Dict[str, Any]],
    cfg: Settings,
) -> List[Tuple[int, int, int, int]]:
    """Tiles (col_off, row_off, w, h) whose centre lies in the EEZ, evenly spread.

    At most ``sar_max_windows`` tiles (0 = every EEZ tile of the scene): the share
    of the EEZ part of the scene actually analysed is reported with the scan.
    """
    size = cfg.sar_window_size
    tiles: List[Tuple[int, int, int, int]] = []
    for row_off in range(0, height, size):
        for col_off in range(0, width, size):
            w = min(size, width - col_off)
            h = min(size, height - row_off)
            lon, lat = to_lonlat(col_off + w / 2, row_off + h / 2)
            if eez is None or point_in_geometry(lon, lat, eez):
                tiles.append((col_off, row_off, w, h))
    if not cfg.sar_max_windows or len(tiles) <= cfg.sar_max_windows:
        return tiles
    step = len(tiles) / cfg.sar_max_windows
    return [tiles[int(i * step)] for i in range(cfg.sar_max_windows)]


class SarReadError(RuntimeError):
    """SAR pixels could not be read; ``code`` tells why (shown to operators)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def classify_s3_error(exc: BaseException) -> SarReadError:
    """Map a GDAL / boto / network exception to an actionable operator message."""
    text = str(exc)
    lowered = text.lower()
    if "signaturedoesnotmatch" in lowered:
        return SarReadError("bad_secret", "SECRET_KEY Copernicus refusée (SignatureDoesNotMatch)")
    if "invalidaccesskeyid" in lowered or "awsinvalidcredentials" in lowered:
        return SarReadError("bad_access_key", "ACCESS_KEY Copernicus inconnue ou expirée")
    if "accessdenied" in lowered or "403" in lowered or "forbidden" in lowered:
        return SarReadError("forbidden", "Accès S3 refusé (403) : clés expirées ou quota Copernicus atteint")
    if "ssl" in lowered or "certificate" in lowered:
        return SarReadError("tls", f"Erreur TLS vers Copernicus S3 (certificats) : {text[:160]}")
    if any(k in lowered for k in ("resolve", "timed out", "timeout", "connection", "network is unreachable")):
        return SarReadError("network", f"Copernicus S3 injoignable (réseau / DNS) : {text[:160]}")
    if "404" in lowered or "not found" in lowered or "does not exist" in lowered:
        return SarReadError("not_found", "Fichier SAR introuvable sur S3 (produit retiré ou chemin STAC invalide)")
    if isinstance(exc, ImportError):
        return SarReadError("rasterio", f"rasterio/GDAL non chargeable sur le serveur : {text[:160]}")
    return SarReadError("read_error", f"{type(exc).__name__}: {text[:200]}")


def detect_ships_with_window(
    s3_url: str,
    settings: Optional[Settings] = None,
    eez: Optional[Dict[str, Any]] = None,
    confirm_url: Optional[str] = None,
    source: str = "copernicus_s3",
) -> Tuple[List[Dict[str, Any]], Optional[np.ndarray]]:
    """Stream EEZ tiles of a Sentinel-1 GRD from Copernicus S3 and extract ships.

    ``s3_url`` is the detection band (cross-pol VH/HV preferred: ships stand
    out, sea clutter is weak). ``confirm_url`` is the co-pol band (VV/HH) of the
    same product, read on the same grid to confirm each target.

    Each detection carries a ``chip`` (ndarray around the target) used by the
    C2PA sealer to crop the real backscatter. Raises :class:`SarReadError` when
    the pixels cannot be read (with the precise cause). The second element is
    the first scanned window.
    """
    cfg = settings or get_settings()
    logger.info("Starting SAR detection on %s", s3_url)

    try:
        import rasterio
        from rasterio.windows import Window
    except Exception as exc:
        raise SarReadError("rasterio", f"rasterio indisponible : {exc}") from exc

    detections: List[Dict[str, Any]] = []
    first_window: Optional[np.ndarray] = None
    ai_error: Optional[str] = None
    ai_stats = {"boxes": 0, "ai_only": 0}
    xview3_stats: Dict[str, Any] = {}
    land_stats: Dict[str, Any] = {"px": 0, "land_px": 0, "rejected": {}}
    try:
        with _rasterio_env(cfg):
            with rasterio.open(s3_url) as src:
                confirm_src = _open_optional(rasterio, confirm_url, src)
                to_lonlat = _pixel_to_lonlat_fn(src)
                eez_tiles = _select_windows(src.width, src.height, to_lonlat, eez, cfg.model_copy(update={"sar_max_windows": 0}))
                windows = _select_windows(src.width, src.height, to_lonlat, eez, cfg)
                if not windows:
                    raise SarReadError("no_tile", "La scène ne recouvre pas la ZEE : choisissez une autre scène")
                coverage = round(100.0 * len(windows) / len(eez_tiles), 1)
                logger.info("Scanning %s of %s SAR tile(s) inside the EEZ (%.1f %%)", len(windows), len(eez_tiles), coverage)
                from app.pipeline import scan_state

                scan_state.add_stats(sar_tiles=len(windows), sar_tiles_in_eez=len(eez_tiles), sar_coverage_pct=coverage)
                for col_off, row_off, w, h in windows:
                    data = src.read(1, window=Window(col_off, row_off, w, h)).astype(np.float32)
                    if first_window is None:
                        first_window = data
                    # Land masking (standard in SAR ship detection): land pixels leave the CFAR
                    # entirely — no echo on land, and no bright coast inflating the sea statistics.
                    land = None
                    near_coast = _near_coast_grid(to_lonlat, col_off, row_off, w, h, cfg)
                    if near_coast is not None and near_coast.any():
                        land = radar_land_mask(data) & near_coast
                        land_stats["land_px"] += int(land.sum())
                    land_stats["px"] += int(data.size)
                    sea_data = np.where(land, 0.0, data).astype(np.float32) if land is not None else data
                    mask = cfar_mask(sea_data, k=cfg.cfar_k)
                    co_mask, co = None, None
                    if confirm_src is not None:
                        try:
                            co = confirm_src.read(1, window=Window(col_off, row_off, w, h)).astype(np.float32)
                            co_sea = np.where(land, 0.0, co).astype(np.float32) if land is not None else co
                            co_mask = cfar_mask(co_sea, k=max(3.0, cfg.cfar_k - 1.5))
                        except Exception as exc:
                            logger.warning("Co-pol confirmation read failed: %s", exc)
                    window_targets = blobs_to_targets(sea_data, mask, cfg)
                    if cfg.ai_detector_mode >= 1 and ai_error is None:
                        ai_error = _ai_opinion(data, window_targets, cfg, ai_stats)
                    kept: List[Dict[str, Any]] = []
                    for target in window_targets:
                        lon, lat = to_lonlat(col_off + target["col"], row_off + target["row"])
                        if eez is not None and not point_in_geometry(lon, lat, eez):
                            continue  # land, river mouth or outside the EEZ
                        target.update({"lat": float(lat), "lon": float(lon)})
                        coast = coastal_context(target, land, cfg)
                        reason = on_land_reason(coast)
                        if reason:
                            land_stats["rejected"][reason] = land_stats["rejected"].get(reason, 0) + 1
                            continue  # the coast itself (rock, islet, quay, mangrove edge), not a vessel at sea
                        target["coast"] = coast
                        abs_col = col_off + target["col"]
                        target["abs_col"], target["abs_row"] = abs_col, row_off + target["row"]
                        target.update(
                            {
                                "lat": float(lat),
                                "lon": float(lon),
                                "type": "radar_blip",
                                "chip": _chip(data, target["row"], target["col"]),
                                "dual_pol": (
                                    dual_pol_confirmed(co_mask, target["row"], target["col"])
                                    if co_mask is not None
                                    else None
                                ),
                                # Near/far range edges are noisy (noise floor, ambiguities).
                                "swath_edge": not (0.03 <= abs_col / max(src.width, 1) <= 0.97),
                            }
                        )
                        kept.append(target)
                    if xview3.enabled(cfg):
                        # Crops for the information-only xView3 opinion (needs both polarisations).
                        if co is None:
                            xview3_stats["no_copol"] = True
                        xview3.attach_crops(kept, data, co, cfg)
                        detections.extend(kept)
                        # Bound memory: only the targets that can still make the top list keep their crops.
                        detections.sort(key=lambda d: d["snr"], reverse=True)
                        for dropped in detections[cfg.max_detections:]:
                            dropped.pop("_xview3_chip", None)
                    else:
                        detections.extend(kept)
                if confirm_src is not None:
                    confirm_src.close()
    except SarReadError as exc:
        logger.error("SAR detection aborted: %s", exc)
        raise
    except Exception as exc:
        error = classify_s3_error(exc)
        logger.error("S3/rasterio access failed [%s]: %s", error.code, exc)
        health.record(source, False, error.code)
        raise error from exc
    health.record(source, True)
    from app.pipeline import scan_state

    rejected_on_land = sum(land_stats["rejected"].values())
    scan_state.add_stats(land_masked_pct=round(100.0 * land_stats["land_px"] / max(land_stats["px"], 1), 1),
                         coast_rejected=rejected_on_land, coast_rejected_by=land_stats["rejected"])
    if rejected_on_land:
        logger.info("%s echo(es) on the coast discarded: %s", rejected_on_land, land_stats["rejected"])

    # Ghosts of bright ships (azimuth ambiguities, sidelobes): checked on the whole scene,
    # the parent echo is often in another tile.
    from app.pipeline import sar_artifacts

    artefacts = sar_artifacts.reject_artifacts(detections, cfg.pixel_spacing_m)
    scan_state.add_stats(artifact_rejected=sum(artefacts.values()), artifact_rejected_by=artefacts)
    if artefacts:
        logger.info("%s SAR artefact(s) discarded: %s", sum(artefacts.values()), artefacts)

    if cfg.ai_detector_mode >= 1:
        from app.pipeline import scan_state

        scan_state.add_stats(ai_mode=cfg.ai_detector_mode, ai_boxes=ai_stats["boxes"], ai_only=ai_stats["ai_only"],
                             ai_error=ai_error)
    detections.sort(key=lambda d: d["snr"], reverse=True)
    detections = detections[: cfg.max_detections]
    for index, det in enumerate(detections):
        det["id"] = f"SAR-{index + 1:03d}"
    if xview3.enabled(cfg):
        _xview3_opinion(detections, cfg, xview3_stats)
    logger.info("SAR detection: %s ship candidate(s)", len(detections))
    return detections, first_window


def detect_ships_in_sar(
    s3_url: str,
    settings: Optional[Settings] = None,
) -> List[Dict[str, Any]]:
    """Detections only (see :func:`detect_ships_with_window`); [] when unreadable."""
    try:
        detections, _window = detect_ships_with_window(s3_url, settings)
    except SarReadError:
        return []
    return detections


def probe_sar_access(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Open the latest Sentinel-1 COG header exactly like a scan does (GDAL path)."""
    from app.pipeline.ingestion import fetch_sentinel1_data, resolve_polarisation_hrefs

    cfg = settings or get_settings()
    item = fetch_sentinel1_data(cfg)
    if item is None:
        return {"ok": False, "code": "stac", "message": "Catalogue STAC Copernicus injoignable"}
    bands = resolve_polarisation_hrefs(item)
    href = bands.get("cross") or bands.get("co")
    try:
        import rasterio
    except Exception as exc:  # missing system library in the image, not a credential problem
        return {"ok": False, "code": "rasterio", "message": f"rasterio/GDAL non chargeable sur le serveur : {exc}"}
    try:
        # Bypass GDAL's /vsicurl/ cache: otherwise a file opened earlier with valid keys is
        # served from cache and wrong credentials would wrongly pass the test.
        with _rasterio_env(cfg), rasterio.Env(CPL_VSIL_CURL_NON_CACHED="/vsis3/", VSI_CACHE="FALSE"):
            with rasterio.open(href) as src:
                src.read(1, window=((0, 16), (0, 16)))
    except Exception as exc:
        error = classify_s3_error(exc)
        return {"ok": False, "code": error.code, "message": str(error)}
    return {"ok": True, "code": "ok", "message": f"Lecture SAR OK ({item.id[:32]}…)"}


def _mock_ais() -> List[Dict[str, Any]]:
    return [{"mmsi": "227543000", "lat": 5.05, "lon": -52.10, "name": "PECHEUR_GUYANE_1", "source": "mock"}]


COLLECTION_MIN_COVERAGE = 0.5
COLLECTION_WINDOW = datetime.timedelta(minutes=30)


def _ais_listening_at(scene_time: Any, cfg: Settings) -> Optional[float]:
    """Share of the minutes around the pass during which an AIS feed was listening (None = not logged)."""
    from app.sources import feed_log

    instant = _parse_time(scene_time)
    if instant is None:
        return None
    try:
        # Never count minutes that have not happened yet (recent pass).
        end = min(instant + COLLECTION_WINDOW, datetime.datetime.now(datetime.timezone.utc))
        return feed_log.coverage(instant - COLLECTION_WINDOW, max(end, instant), settings=cfg)
    except Exception as exc:
        logger.warning("Collection log unreadable: %s", exc)
        return None


def load_ais_tracks(settings: Optional[Settings] = None, scene_time: Any = None) -> Tuple[List[Dict[str, Any]], str]:
    """AIS picture at the SAR acquisition time. Returns (tracks, source).

    1. Stored vessel history (aisstream.io, AISHub, VMS imports): fix nearest
       to the pass, dead-reckoned to the acquisition instant — the reliable path.
    2. aisstream.io live snapshot, ONLY when the scene is recent enough
       (acquired less than ``ais_correlation_window_h`` ago): today's AIS says
       nothing about a pass of yesterday.
    3. Nothing: ``([], "unavailable")`` — every echo stays "without AIS" but the
       risk score is down-weighted and the source is shown as unknown.
    Whatever the history, if the collection log shows the AIS feeds were down for
    most of the ±30 min around the pass, the source is "unavailable": an echo is
    only "without AIS" when someone was listening.
       Without any AIS key at all, in development only, a simulated track keeps
       demos working ("mock"). Never in production: a fictitious vessel could be
       matched to a real echo and hide it from the dark-vessel list.
    """
    from app.sources import ais_store
    from app.sources.aisstream import fetch_live_ais

    cfg = settings or get_settings()
    if scene_time is not None:
        try:
            history = ais_store.positions_at(scene_time, cfg.ais_correlation_window_h, cfg)
        except Exception as exc:
            logger.warning("AIS history unavailable: %s", exc)
            history = []
        listening = _ais_listening_at(scene_time, cfg)
        if listening is not None and listening < COLLECTION_MIN_COVERAGE:
            # The AIS feeds were (mostly) down around the pass: the fixes found still explain the
            # echoes they match, but the absence of AIS for the others is not demonstrated.
            logger.warning("AIS collection covered %.0f %% of the minutes around the pass: absence of AIS not proven",
                           listening * 100)
            return history, "unavailable"
        if history:
            feeds = sorted({ais_store.SOURCE_LABELS.get(t.get("feed"), t.get("feed")) for t in history})
            logger.info("AIS correlation on %s stored track(s) at the SAR pass time (%s)", len(history), ", ".join(feeds))
            return history, f"{' + '.join(feeds)} (historique)"
    if not (cfg.aisstream_api_key or cfg.aishub_username or (cfg.vesselapi_api_key and cfg.vesselapi_poll_minutes)):
        if cfg.environment == "production":
            logger.warning("No AIS feed configured: every echo is reported without AIS (set AISSTREAM_API_KEY)")
            return [], "unavailable"
        logger.warning("Using mocked AIS tracks (development only — set AISSTREAM_API_KEY for live AIS)")
        return _mock_ais(), "mock"
    scene_dt = _parse_time(scene_time) if scene_time is not None else None
    age_h = (datetime.datetime.now(datetime.timezone.utc) - scene_dt).total_seconds() / 3600 if scene_dt else 0.0
    if age_h <= cfg.ais_correlation_window_h:
        live = fetch_live_ais(cfg)
        if live:
            return live, "aisstream.io"
    logger.warning("No AIS picture at the pass time (scene %.1f h old, no stored history)", age_h)
    return [], "unavailable"


_TIME_RE = re.compile(
    r"(?P<date>\d{4}-\d{2}-\d{2})[ T](?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?\s*(?P<tz>Z|[+-]\d{2}:?\d{2})?"
)


def _parse_time(value: Any) -> Optional[datetime.datetime]:
    """Parse ISO 8601 or aisstream ``2026-10-20 14:03:11.123456789 +0000 UTC`` (UTC default)."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    match = _TIME_RE.match(str(value or "").strip())
    if not match:
        return None
    frac = (match.group("frac") or "0")[:6]
    tz = match.group("tz") or "+00:00"
    tz = "+00:00" if tz == "Z" else (tz if ":" in tz else f"{tz[:3]}:{tz[3:]}")
    try:
        return datetime.datetime.fromisoformat(f"{match.group('date')}T{match.group('time')}.{frac}{tz}")
    except ValueError:
        return None


def match_radius_km(
    ais: Dict[str, Any],
    scene_time: Optional[datetime.datetime],
    cfg: Settings,
) -> float:
    """Base radius + distance the ship may have sailed between AIS fix and SAR pass.

    Fixes already dead-reckoned to the pass (stored history, ``fix_age_h``) only add the
    projection error — a quarter of the distance sailed (course changes while fishing),
    the same model as the corroboration tracks; raw fixes add the whole distance.
    """
    sog = ais.get("sog_knots")
    if ais.get("fix_age_h") is not None:
        drift = DEAD_RECKONING_ERROR * float(sog or 0) * 1.852 * float(ais["fix_age_h"])
        return min(cfg.ais_max_match_km, cfg.ais_match_km + drift)
    ais_time = _parse_time(ais.get("time_utc"))
    if scene_time is None or ais_time is None or sog is None:
        return cfg.ais_match_km
    hours = abs((ais_time - scene_time).total_seconds()) / 3600.0
    drift = float(sog) * 1.852 * hours
    return min(cfg.ais_max_match_km, cfg.ais_match_km + drift)


DEAD_RECKONING_ERROR = 0.25  # share of the distance sailed that a straight-line projection may miss
SIZE_RATIO = 2.5       # radar echo at least 2.5× longer than the declared vessel…
SIZE_MIN_GAP_M = 50.0  # …and at least 50 m longer: beyond the coarse 10 m-pixel measurement error


def size_mismatch(nav: Dict[str, Any], ais: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """An echo far larger than the AIS vessel matched to it: that AIS does not explain it.

    Either another (dark) vessel lies right next to the declared one, or the AIS
    identity is false. Only measurable echoes count (≥ 6 pixels), and only when the
    declared length is known (AIS dimensions or VesselAPI registry).
    """
    from app.pipeline.risk import MIN_PIXELS_FOR_SIZE

    radar, declared = nav.get("length_m"), ais.get("length_m")
    pixels = nav.get("pixels")
    if not radar or not declared or (pixels is not None and pixels < MIN_PIXELS_FOR_SIZE):
        return None
    if radar >= SIZE_RATIO * declared and radar - declared >= SIZE_MIN_GAP_M:
        return {"mmsi": ais.get("mmsi"), "name": ais.get("name"), "declared_m": round(float(declared)),
                "radar_m": round(float(radar))}
    return None


def correlate_with_ais(
    detections_radar: Sequence[Dict[str, Any]],
    settings: Optional[Settings] = None,
    ais_tracks: Optional[List[Dict[str, Any]]] = None,
    scene_time: Any = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Cross-correlate SAR blips with AIS. Returns (dark_vessels, ais_tracks).

    Each AIS track can explain at most one radar target (closest first), and the
    matching radius grows with the time gap between the AIS fix and the SAR pass.
    """
    cfg = settings or get_settings()
    if not detections_radar:
        logger.warning("No radar detections — AIS correlation skipped")
        return [], ais_tracks or []

    source = "provided"
    if ais_tracks is None:
        ais_tracks, source = load_ais_tracks(cfg)
    logger.info("AIS correlation against %s track(s) (%s)", len(ais_tracks), source)
    scene_dt = _parse_time(scene_time)

    pairs: List[Tuple[float, int, int]] = []
    for i, nav in enumerate(detections_radar):
        for j, ais in enumerate(ais_tracks):
            dist = haversine_km(nav["lat"], nav["lon"], ais["lat"], ais["lon"])
            if dist <= match_radius_km(ais, scene_dt, cfg):
                pairs.append((dist, i, j))
    pairs.sort()
    matched_det: Dict[int, Tuple[int, float]] = {}
    used_ais: set = set()
    for dist, i, j in pairs:
        if i in matched_det or j in used_ais:
            continue
        matched_det[i] = (j, dist)
        used_ais.add(j)

    dark: List[Dict[str, Any]] = []
    for i, nav in enumerate(detections_radar):
        if i in matched_det:
            j, dist = matched_det[i]
            mismatch = size_mismatch(nav, ais_tracks[j])
            if mismatch:
                logger.warning("Echo of ~%s m matched to AIS %s declared %s m: kept as suspect",
                               mismatch["radar_m"], mismatch["mmsi"], mismatch["declared_m"])
                dark.append({**nav, "ais_source": source, "size_mismatch": {**mismatch, "distance_km": round(dist, 2)}})
                continue
            logger.info(
                "AIS-confirmed vessel at [%.4f, %.4f] (MMSI %s, %.2f km)",
                nav["lat"],
                nav["lon"],
                ais_tracks[j].get("mmsi"),
                dist,
            )
            continue
        logger.warning(
            "DARK VESSEL at [%.4f, %.4f] (confidence=%s)", nav["lat"], nav["lon"], nav.get("confidence")
        )
        dark.append({**nav, "ais_source": source})
    return dark, ais_tracks
