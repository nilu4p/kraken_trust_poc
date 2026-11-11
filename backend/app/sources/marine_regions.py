"""Maritime boundaries from Marine Regions (VLIZ) WFS — free, no API key.

Layers for French Guiana: EEZ 200 NM (MRGID 8462), territorial sea 12 NM and
contiguous zone 24 NM. Results are cached on disk; the simplified EEZ in
``Settings.eez_polygon`` is used when the service and cache are unavailable.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.core.geo import point_in_geometry, simplify_geometry
from app.sources.http import cache_read, cache_read_stale, cache_write, get_json

logger = logging.getLogger(__name__)

WFS_URL = "https://geo.vliz.be/geoserver/MarineRegions/wfs"
TERRITORY = "French Guiana"

# layer -> (display name, colour, zone code)
LAYERS: Dict[str, Dict[str, str]] = {
    "eez": {"name": "ZEE Guyane (200 NM)", "color": "#00FFFF", "zone": "eez"},
    "eez_24nm": {"name": "Zone contiguë (24 NM)", "color": "#F5C542", "zone": "contiguous"},
    "eez_12nm": {"name": "Mer territoriale (12 NM)", "color": "#FF8A3D", "zone": "territorial"},
}


def _fetch_layer(layer: str, cfg: Settings) -> Optional[Dict[str, Any]]:
    """Return the raw (full resolution) geometry of one boundary layer."""
    key = f"{layer}:{TERRITORY}"
    cached = cache_read("marine_regions", key, cfg.marine_regions_ttl_hours * 3600, cfg)
    if cached:
        return cached

    data = get_json(
        WFS_URL,
        params={
            "service": "WFS",
            "version": "1.0.0",
            "request": "GetFeature",
            "typeName": f"MarineRegions:{layer}",
            "outputFormat": "application/json",
            "CQL_FILTER": f"territory1='{TERRITORY}'",
        },
        settings=cfg,
        source="marine_regions",
    )
    features = (data or {}).get("features") or []
    if features and features[0].get("geometry"):
        props = features[0].get("properties") or {}
        payload = {
            "geometry": features[0]["geometry"],
            "mrgid": props.get("mrgid"),
            "geoname": props.get("geoname"),
            "area_km2": props.get("area_km2"),
        }
        cache_write("marine_regions", key, payload, cfg)
        return payload

    stale = cache_read_stale("marine_regions", key, cfg)
    if stale:
        logger.warning("Marine Regions unreachable — using stale cache for %s", layer)
    return stale


def eez_geometry(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Full-resolution EEZ geometry (falls back to the config polygon offline)."""
    cfg = settings or get_settings()
    layer = _fetch_layer("eez", cfg)
    if layer:
        return layer["geometry"]
    return {"type": "Polygon", "coordinates": [cfg.eez_polygon]}


def zone_geometries(settings: Optional[Settings] = None) -> Dict[str, Optional[Dict[str, Any]]]:
    """{zone code: geometry} for eez / contiguous / territorial (None if unknown)."""
    cfg = settings or get_settings()
    result: Dict[str, Optional[Dict[str, Any]]] = {}
    for layer, meta in LAYERS.items():
        data = _fetch_layer(layer, cfg)
        # ~100 m tolerance: precise enough for zoning, ~20x fewer vertices for SAR tiling.
        result[meta["zone"]] = simplify_geometry(data["geometry"], 0.001) if data else None
    if result.get("eez") is None:
        result["eez"] = {"type": "Polygon", "coordinates": [cfg.eez_polygon]}
    return result


def boundaries_feature_collection(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Simplified boundaries for the map (EEZ always present, fallback if offline)."""
    cfg = settings or get_settings()
    features: List[Dict[str, Any]] = []
    for layer, meta in LAYERS.items():
        data = _fetch_layer(layer, cfg)
        if data is None and layer != "eez":
            continue
        source = "marineregions.org" if data else "fallback"
        geometry = (
            simplify_geometry(data["geometry"], 0.002)
            if data
            else {"type": "Polygon", "coordinates": [cfg.eez_polygon]}
        )
        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "name": meta["name"],
                    "zone": meta["zone"],
                    "color": meta["color"],
                    "mrgid": (data or {}).get("mrgid"),
                    "area_km2": (data or {}).get("area_km2"),
                    "source": source,
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}


def classify_zone(lon: float, lat: float, zones: Dict[str, Optional[Dict[str, Any]]]) -> Optional[str]:
    """Most restrictive zone containing the point: territorial > contiguous > eez."""
    for zone in ("territorial", "contiguous", "eez"):
        if point_in_geometry(lon, lat, zones.get(zone)):
            return zone
    return None
