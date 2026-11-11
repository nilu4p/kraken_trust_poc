"""Copernicus STAC lookup for Sentinel-1 GRD over the French Guiana EEZ."""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.sources.stac import stac_search

logger = logging.getLogger(__name__)


def list_collections(settings: Optional[Settings] = None) -> List[str]:
    """Return the collection IDs exposed by the Copernicus STAC API ([] on failure)."""
    cfg = settings or get_settings()
    try:
        from pystac_client import Client

        return [collection.id for collection in Client.open(cfg.stac_url).get_collections()]
    except Exception as exc:
        logger.error("Unable to list STAC collections: %s", exc)
        return []


def _search(cfg: Settings, limit: int, ids: Optional[List[str]] = None,
            before: Optional[datetime.datetime] = None) -> List[Any]:
    """Sentinel-1 GRD items (pystac.Item) through the cached, rate-limit aware client.

    ``before``: acquisitions of the ``stac_lookback_days`` up to that instant (replay).
    """
    import pystac

    body: Dict[str, Any] = {"collections": ["sentinel-1-grd"], "limit": limit}
    if ids:
        body["ids"] = ids
    else:
        # Day-aligned open interval keeps the cache key stable between refreshes.
        last_day = before.date() if before else datetime.date.today()
        start = last_day - datetime.timedelta(days=cfg.stac_lookback_days)
        end = before.strftime("%Y-%m-%dT%H:%M:%SZ") if before else ".."
        body.update(
            bbox=list(cfg.eez_bbox),
            datetime=f"{start.isoformat()}T00:00:00Z/{end}",
            sortby=[{"field": "properties.datetime", "direction": "desc"}],
        )
    features = stac_search(body, settings=cfg, ttl_seconds=600 if ids or before else 300)
    if features is None:
        raise RuntimeError("Copernicus STAC indisponible ou requête refusée (voir le journal STAC ci-dessus)")
    return [pystac.Item.from_dict(feature) for feature in features[:limit]]


def fetch_sentinel1_data(settings: Optional[Settings] = None, scene_id: Optional[str] = None) -> Optional[Any]:
    """Return the requested (or most recent) Sentinel-1 GRD STAC item over the EEZ, or None."""
    cfg = settings or get_settings()
    logger.info("Connecting to Copernicus STAC %s", cfg.stac_url)
    try:
        items = _search(cfg, 1, [scene_id] if scene_id else None)
        if not items:
            logger.warning("No Sentinel-1 GRD scene found (scene_id=%s, bbox=%s)", scene_id, cfg.eez_bbox)
            return None
        item = items[0]
        logger.info("Sentinel-1 scene selected: %s (%s)", item.id, item.datetime)
        return item
    except Exception as exc:
        logger.error("STAC query failed: %s", exc)
        return None


def scene_feature(item: Any) -> Dict[str, Any]:
    """GeoJSON footprint + key acquisition metadata of a STAC item."""
    props = getattr(item, "properties", {}) or {}
    assets = getattr(item, "assets", {}) or {}
    thumbnail = assets.get("thumbnail")
    return {
        "type": "Feature",
        "geometry": getattr(item, "geometry", None),
        "properties": {
            "type": "s1_scene",
            "scene_id": item.id,
            "datetime": item.datetime.isoformat() if getattr(item, "datetime", None) else props.get("datetime"),
            "mode": props.get("sar:instrument_mode"),
            "polarizations": props.get("sar:polarizations"),
            "orbit_state": props.get("sat:orbit_state"),
            "relative_orbit": props.get("sat:relative_orbit"),
            "platform": props.get("platform"),
            "thumbnail": getattr(thumbnail, "href", None),
        },
    }


def search_recent_scenes(settings: Optional[Settings] = None, limit: int = 20,
                         before: Optional[datetime.datetime] = None) -> Dict[str, Any]:
    """Sentinel-1 GRD acquisitions over the EEZ up to ``before`` (now when None), public STAC."""
    cfg = settings or get_settings()
    try:
        items = _search(cfg, limit, before=before)
    except Exception as exc:
        logger.error("STAC scene listing failed: %s", exc)
        return {"type": "FeatureCollection", "features": [], "available": False}
    return {
        "type": "FeatureCollection",
        "features": [scene_feature(item) for item in items],
        "available": True,
        "lookback_days": cfg.stac_lookback_days,
    }


def _to_s3(href: str) -> str:
    if href.startswith("https://eodata"):
        return href.replace("https://eodata.dataspace.copernicus.eu", "s3://eodata")
    return href


def resolve_polarisation_hrefs(item: Any) -> Dict[str, str]:
    """{"co": VV/HH href, "cross": VH/HV href} for dual-pol detection (missing keys omitted)."""
    assets = getattr(item, "assets", {}) or {}
    result: Dict[str, str] = {}
    for role, names in (("co", ("vv", "hh")), ("cross", ("vh", "hv"))):
        for name in names:
            if name in assets and getattr(assets[name], "href", None):
                result[role] = _to_s3(assets[name].href)
                break
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for collection_id in list_collections():
        print(collection_id)
