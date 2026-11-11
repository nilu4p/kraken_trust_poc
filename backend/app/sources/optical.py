"""Optical imagery available over a target: Pléiades / Pléiades Neo / SPOT / WorldView (CCM) + Sentinel-2.

Uses the public Copernicus STAC catalogue (no key):
- ``ccm-optical``: Copernicus Contributing Missions, very-high-resolution
  commercial imagery (Pléiades 0.5 m, Pléiades Neo 0.3 m, SPOT 1.5 m, ...).
  Searching is free; downloading requires an eligible CDSE/CCM account.
- ``sentinel-2-l2a``: 10 m optical, free download with a CDSE account.

An optical acquisition close in time to the SAR pass can visually confirm or
dismiss a radar target (and identify the vessel type).
"""

from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.sources.stac import stac_search

logger = logging.getLogger(__name__)

VHR_LABELS = {
    "pleiades": "Pléiades",
    "pleiades-neo": "Pléiades Neo",
    "spot": "SPOT",
    "worldview": "WorldView",
    "geoeye": "GeoEye",
    "kompsat": "KOMPSAT",
    "vision": "Vision-1",
    "planetscope": "PlanetScope",
    "superview": "SuperView",
    "triplesat": "TripleSat",
    "geosat": "GEOSAT",
    # Commercial very-high-resolution SAR (ccm-sar): sees through clouds and at night.
    "terrasar": "TerraSAR-X",
    "tandem": "TanDEM-X",
    "cosmo": "COSMO-SkyMed",
    "paz": "PAZ",
    "iceye": "ICEYE",
    "radarsat": "RADARSAT",
    "capella": "Capella",
    "saocom": "SAOCOM",
}

ACCESS_NOTES = {
    "ccm-optical": "Copernicus Contributing Missions : téléchargement réservé aux utilisateurs éligibles (services Copernicus, autorités publiques)",
    "sentinel-2-l2a": "Sentinel-2 : téléchargement libre avec un compte Copernicus Data Space",
    "ccm-sar": "Radar commercial (CCM) : accès réservé aux utilisateurs Copernicus éligibles — tout temps, jour et nuit",
    "cnes-geodes": "CNES GEODES : aperçu libre, téléchargement avec un compte CNES gratuit",
}


def _constellation(platform: str) -> str:
    platform = (platform or "").lower()
    if platform.startswith("pleiades-neo"):
        return "pleiades-neo"
    for key in VHR_LABELS:
        if platform.startswith(key):
            return key
    if platform.startswith("sentinel-2"):
        return "sentinel-2"
    return platform or "unknown"


def _parse_dt(value: Optional[str]) -> Optional[datetime.datetime]:
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def to_feature(item: Dict[str, Any], collection: str, reference: Optional[datetime.datetime]) -> Dict[str, Any]:
    """Normalise a STAC item into an imagery footprint feature."""
    props = item.get("properties") or {}
    assets = item.get("assets") or {}
    # CCM items expose the acquisition in start_datetime; ``datetime`` may be the ingestion date.
    acquired = props.get("start_datetime") or props.get("datetime")
    acquired_dt = _parse_dt(acquired)
    constellation = _constellation(str(props.get("platform", "")))
    thumbnail = next(
        (a.get("href") for a in assets.values() if "thumbnail" in (a.get("roles") or []) or "overview" in (a.get("roles") or [])),
        None,
    )
    product = assets.get("product") or assets.get("Product") or {}
    delta_h = None
    if acquired_dt and reference:
        delta_h = round((acquired_dt - reference).total_seconds() / 3600.0, 1)
    return {
        "type": "Feature",
        "geometry": item.get("geometry"),
        "properties": {
            "type": "optical_image",
            "image_id": item.get("id"),
            "collection": collection,
            "platform": props.get("platform"),
            "product_type": props.get("product:type"),
            "constellation": constellation,
            "label": VHR_LABELS.get(constellation, "Sentinel-2" if constellation == "sentinel-2" else constellation),
            "gsd_m": props.get("gsd") or (10.0 if constellation == "sentinel-2" else None),
            "cloud_cover": props.get("eo:cloud_cover"),
            "acquired": acquired,
            "delta_hours_vs_sar": delta_h,
            "thumbnail": thumbnail,
            "product_url": product.get("href"),
            "access": ACCESS_NOTES.get(collection),
        },
    }


def geodes_feature(item: Dict[str, Any], reference: Optional[datetime.datetime]) -> Dict[str, Any]:
    """Normalise a CNES GEODES item (PEPS / THEIA) with its public quicklook."""
    from app.sources.geodes import COLLECTIONS

    props = item.get("properties") or {}
    assets = item.get("assets") or {}
    quicklook = next((a.get("href") for a in assets.values() if "overview" in (a.get("roles") or [])), None)
    data = next((a.get("href") for a in assets.values() if "data" in (a.get("roles") or [])), None)
    acquired = props.get("start_datetime") or props.get("datetime")
    acquired_dt = _parse_dt(acquired)
    collection = props.get("dataset") or item.get("collection") or ""
    sar = collection.startswith("PEPS_S1")
    return {
        "type": "Feature",
        "geometry": item.get("geometry"),
        "properties": {
            "type": "optical_image",
            "image_id": item.get("id"),
            "collection": "cnes-geodes",
            "sensor": "sar" if sar else "optical",
            "platform": props.get("platform"),
            "product_type": props.get("product:type"),
            "constellation": "sentinel-1" if sar else "sentinel-2",
            "label": COLLECTIONS.get(collection, collection),
            "gsd_m": 10.0,
            "cloud_cover": props.get("eo:cloud_cover"),
            "acquired": acquired,
            "delta_hours_vs_sar": round((acquired_dt - reference).total_seconds() / 3600.0, 1) if acquired_dt and reference else None,
            "thumbnail": quicklook,
            "product_url": data,
            "access": ACCESS_NOTES["cnes-geodes"],
        },
    }


def search_optical(
    lat: float,
    lon: float,
    *,
    around: Optional[str] = None,
    days: int = 3,
    max_cloud: float = 60.0,
    settings: Optional[Settings] = None,
) -> Dict[str, Any]:
    """Optical acquisitions covering a point within ±``days`` of ``around`` (default: now)."""
    cfg = settings or get_settings()
    reference = _parse_dt(around) or datetime.datetime.now(datetime.timezone.utc)
    start = (reference - datetime.timedelta(days=days)).date().isoformat()
    end = (reference + datetime.timedelta(days=days + 1)).date().isoformat()
    point = {"type": "Point", "coordinates": [round(lon, 3), round(lat, 3)]}

    features: List[Dict[str, Any]] = []
    unavailable: List[str] = []
    sources = (("ccm-optical", 50), ("ccm-sar", 30), ("sentinel-2-l2a", 20))
    for collection, limit in sources:
        body = {
            "collections": [collection],
            "intersects": point,
            "datetime": f"{start}T00:00:00Z/{end}T00:00:00Z",
            "limit": limit,
        }
        items = stac_search(body, settings=cfg, ttl_seconds=3600)
        if items is None:
            unavailable.append(collection)
            continue
        for item in items:
            feature = to_feature(item, collection, reference)
            cloud = feature["properties"]["cloud_cover"]
            if cloud is not None and float(cloud) > max_cloud:
                continue
            feature["properties"]["sensor"] = "sar" if collection == "ccm-sar" else "optical"
            features.append(feature)

    from app.sources.geodes import search as geodes_search

    geodes_items = geodes_search(lat, lon, start, end, settings=cfg)
    if geodes_items is None:
        unavailable.append("cnes-geodes")
    else:
        for item in geodes_items:
            feature = geodes_feature(item, reference)
            cloud = feature["properties"]["cloud_cover"]
            if cloud is not None and float(cloud) > max_cloud:
                continue
            features.append(feature)

    def _rank(f: Dict[str, Any]) -> tuple:
        p = f["properties"]
        return (abs(p["delta_hours_vs_sar"]) if p["delta_hours_vs_sar"] is not None else 1e9, p["gsd_m"] or 99)

    features.sort(key=_rank)
    return {
        "type": "FeatureCollection",
        "features": features,
        "available": len(unavailable) < len(sources) + 1,
        "unavailable": unavailable,
        "window": {"start": start, "end": end},
        "vhr_count": sum(1 for f in features if f["properties"]["collection"] in ("ccm-optical", "ccm-sar")),
        "quicklook_count": sum(1 for f in features if f["properties"].get("thumbnail")),
    }
