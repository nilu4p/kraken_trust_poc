"""Microsoft Planetary Computer — keyless mirror of the Sentinel-1 GRD products.

The scan normally reads the radar pixels from Copernicus S3 (keys required).
Planetary Computer republishes the same GRD products as Cloud-Optimised GeoTIFF
(same pixels, same grid, same ground control points) behind a free, anonymous
SAS token. KRAKEN uses it:

- when no Copernicus S3 keys are configured (the radar scan works out of the box);
- when the S3 read of a scene fails for a non-permanent reason (expired keys,
  quota, network): the **same scene** is read from the mirror instead.

The scene is still chosen in the Copernicus catalogue; the mirror is matched by
product identifier (platform, mode, dates, orbit, datatake), so the evidence
names one acquisition whatever the reading path. ``sar_source`` forces a path:
``auto`` (default), ``copernicus`` or ``planetary``.
"""

from __future__ import annotations

import datetime
import logging
import threading
from typing import Any, Dict, Optional

import httpx

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT

logger = logging.getLogger(__name__)

STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
TOKEN_URL = "https://planetarycomputer.microsoft.com/api/sas/v1/token/sentinel-1-grd"
_token: Dict[str, Any] = {}
_lock = threading.Lock()


def product_key(scene_id: str) -> str:
    """``S1A_IW_GRDH_1SDV_<start>_<stop>_<orbit>_<datatake>`` — identical in both catalogues
    (Copernicus may append a product suffix such as ``_A1B2`` or ``_COG``)."""
    return "_".join(str(scene_id).split("_")[:8]).upper()


def _sas_token(cfg: Settings) -> str:
    with _lock:
        expiry = _token.get("expiry")
        if expiry and expiry - datetime.timedelta(minutes=10) > datetime.datetime.now(datetime.timezone.utc):
            return _token["token"]
        response = httpx.get(TOKEN_URL, headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
        response.raise_for_status()
        body = response.json()
        _token["token"] = body["token"]
        _token["expiry"] = datetime.datetime.fromisoformat(body["msft:expiry"].replace("Z", "+00:00"))
        return _token["token"]


def sign(href: str, cfg: Settings) -> str:
    return f"{href}{'&' if '?' in href else '?'}{_sas_token(cfg)}"


_collection_tokens: Dict[str, Dict[str, Any]] = {}


def sign_collection(href: str, collection: str, cfg: Settings) -> str:
    """Sign an asset of another public collection (e.g. ``landsat-c2-l2``) with its anonymous SAS token."""
    with _lock:
        cached = _collection_tokens.get(collection) or {}
        expiry = cached.get("expiry")
        if not expiry or expiry - datetime.timedelta(minutes=10) <= datetime.datetime.now(datetime.timezone.utc):
            response = httpx.get(f"https://planetarycomputer.microsoft.com/api/sas/v1/token/{collection}",
                                 headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
            response.raise_for_status()
            body = response.json()
            cached = {"token": body["token"],
                      "expiry": datetime.datetime.fromisoformat(body["msft:expiry"].replace("Z", "+00:00"))}
            _collection_tokens[collection] = cached
    return f"{href}{'&' if '?' in href else '?'}{cached['token']}"


def find(scene_id: str, scene_time: Optional[str], settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    """The mirror item of a Copernicus scene, or None (not mirrored yet, or mirror unreachable)."""
    cfg = settings or get_settings()
    from app.core.timeframe import parse_instant

    moment = parse_instant(scene_time)
    if moment is None:
        return None
    window = f"{(moment - datetime.timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%SZ')}/" \
             f"{(moment + datetime.timedelta(minutes=2)).strftime('%Y-%m-%dT%H:%M:%SZ')}"
    try:
        response = httpx.post(STAC_URL, json={"collections": ["sentinel-1-grd"], "bbox": list(cfg.eez_bbox),
                                              "datetime": window, "limit": 20},
                              headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
        response.raise_for_status()
        features = response.json().get("features") or []
    except (httpx.HTTPError, ValueError) as exc:
        health.record("planetary_computer", False, type(exc).__name__)
        logger.warning("Planetary Computer search failed: %s", exc)
        return None
    health.record("planetary_computer", True)
    wanted = product_key(scene_id)
    return next((f for f in features if product_key(f.get("id", "")) == wanted), None)


def bands_for(scene_id: str, scene_time: Optional[str], settings: Optional[Settings] = None) -> Optional[Dict[str, str]]:
    """Signed {"co": VV/HH, "cross": VH/HV} COG URLs of the scene on the mirror, or None."""
    cfg = settings or get_settings()
    item = find(scene_id, scene_time, cfg)
    if item is None:
        return None
    assets = item.get("assets") or {}
    try:
        bands: Dict[str, str] = {}
        for role, names in (("co", ("vv", "hh")), ("cross", ("vh", "hv"))):
            for name in names:
                if (assets.get(name) or {}).get("href"):
                    bands[role] = sign(assets[name]["href"], cfg)
                    break
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        health.record("planetary_computer", False, f"jeton SAS : {type(exc).__name__}")
        logger.warning("Planetary Computer token unavailable: %s", exc)
        return None
    return bands or None
