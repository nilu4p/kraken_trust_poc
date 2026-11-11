"""Official French regulatory fishing limits from SHOM (data.shom.fr WFS, open licence, no key).

For French Guiana: the 100-nautical-mile fishing limit (Code rural et de la
pêche maritime, art. R951-14) and the 3-mile coastal fishing limit. Lines are
cached 30 days; distances from a target to each limit feed the point dossier
and the risk explanation.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.core.geo import point_segment_km
from app.sources.http import cache_read, cache_read_stale, cache_write, get_json

logger = logging.getLogger(__name__)

WFS = "https://services.data.shom.fr/INSPIRE/wfs"
LAYERS = {
    "fishing_100nm": ("LIMITES_PECHE_BDD_WFS:limite_100milles_peche_outre_mer_wgs84_epsg4326", "Limite de pêche des 100 milles (R951-14)"),
    "fishing_3nm": ("LIMITES_PECHE_BDD_WFS:limite_3milles_peche_wgs84_epsg4326", "Limite des 3 milles (pêche)"),
}


def _layer(key: str, cfg: Settings) -> List[Dict[str, Any]]:
    type_name, _label = LAYERS[key]
    cached = cache_read("shom", key, 30 * 24 * 3600, cfg)
    if cached is not None:
        return cached
    data = get_json(
        WFS,
        params={
            "service": "WFS",
            "version": "2.0.0",
            "request": "GetFeature",
            "typeNames": type_name,
            "outputFormat": "application/json",
        },
        settings=cfg,
        source="shom",
    )
    features = [
        f for f in (data or {}).get("features") or []
        if "guyane" in str((f.get("properties") or {}).get("territory", "")).lower()
        or "guyane" in str((f.get("properties") or {}).get("descriptio", "")).lower()
    ]
    if data is not None:
        cache_write("shom", key, features, cfg)
        return features
    return cache_read_stale("shom", key, cfg) or []


def fishing_limits(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """GeoJSON lines of the Guiana fishing limits (empty collection if SHOM is unreachable)."""
    cfg = settings or get_settings()
    features = []
    for key, (_type, label) in LAYERS.items():
        for feature in _layer(key, cfg):
            props = feature.get("properties") or {}
            features.append(
                {
                    "type": "Feature",
                    "geometry": feature.get("geometry"),
                    "properties": {
                        "limit": key,
                        "name": label,
                        "reference": props.get("reference"),
                        "source": "SHOM / DPMA",
                    },
                }
            )
    return {"type": "FeatureCollection", "features": features}


def _lines(geometry: Optional[Dict[str, Any]]) -> List[List[List[float]]]:
    if not geometry:
        return []
    if geometry.get("type") == "LineString":
        return [geometry["coordinates"]]
    if geometry.get("type") == "MultiLineString":
        return list(geometry["coordinates"])
    return []


def distances_km(lat: float, lon: float, settings: Optional[Settings] = None) -> Dict[str, Optional[float]]:
    """Distance (km) from a point to each SHOM fishing limit line."""
    cfg = settings or get_settings()
    result: Dict[str, Optional[float]] = {}
    for key in LAYERS:
        best: Optional[float] = None
        for feature in _layer(key, cfg):
            for line in _lines(feature.get("geometry")):
                for a, b in zip(line, line[1:]):
                    d = point_segment_km(lat, lon, a, b)
                    best = d if best is None or d < best else best
        result[key] = round(best, 1) if best is not None else None
    return result
