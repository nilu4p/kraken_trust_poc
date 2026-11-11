"""Marine protected areas from the World Database on Protected Areas (UNEP-WCMC / IUCN), keyless.

Source: the public WDPA ArcGIS service of UNEP-WCMC (same data as Protected
Planet). Only sites with a marine part are kept (realm Marine, or Coastal
with a reported marine area): around French Guiana the Grand Connétable
national nature reserve, l'Amana, and the neighbouring Surinamese and
Brazilian coastal reserves (Galibi, Wia-Wia, Cabo Orange…).

Used by the ``mpa_fishing`` rule (slow fishing vessel inside an MPA) and by
the risk score of dark targets.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.core.geo import bbox_of, point_in_geometry
from app.sources.http import cache_read, cache_read_stale, cache_write, get_json

logger = logging.getLogger(__name__)

SERVICE = (
    "https://data-gis.unep-wcmc.org/server/rest/services/ProtectedSites/"
    "The_World_Database_of_Protected_Areas/MapServer/1/query"
)
FIELDS = "site_id,name,desig,desig_eng,iucn_cat,realm,no_take,rep_m_area,status,status_yr,iso3"
TTL = 30 * 24 * 3600


def _marine(props: Dict[str, Any]) -> bool:
    realm = (props.get("realm") or "").lower()
    return realm == "marine" or (realm == "coastal" and float(props.get("rep_m_area") or 0) > 0)


def protected_areas(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Marine protected areas intersecting the EEZ bounding box (GeoJSON, cached 30 days)."""
    cfg = settings or get_settings()
    west, south, east, north = cfg.eez_bbox
    key = f"{west},{south},{east},{north}"
    cached = cache_read("wdpa", key, TTL, cfg)
    if cached is None:
        payload = get_json(
            SERVICE,
            params={
                "geometry": f"{west},{south},{east},{north}", "geometryType": "esriGeometryEnvelope", "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects", "outFields": FIELDS, "returnGeometry": "true",
                "outSR": "4326", "maxAllowableOffset": "0.002", "f": "geojson",
            },
            settings=cfg,
            source="wdpa",
        )
        if payload and isinstance(payload.get("features"), list):
            cached = [f for f in payload["features"] if f.get("geometry") and _marine(f.get("properties") or {})]
            cache_write("wdpa", key, cached, cfg)
        else:
            cached = cache_read_stale("wdpa", key, cfg)
    features = []
    for f in cached or []:
        p = f.get("properties") or {}
        features.append({
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": {
                "type": "mpa",
                "site_id": p.get("site_id"),
                "name": str(p.get("name") or "").replace("\\'", "'"),
                "designation": p.get("desig") or p.get("desig_eng"),
                "iucn": p.get("iucn_cat"),
                "no_take": p.get("no_take"),
                "marine_km2": round(float(p.get("rep_m_area") or 0), 1),
                "status": p.get("status"),
                "country": p.get("iso3"),
            },
        })
    return {"type": "FeatureCollection", "features": features, "available": cached is not None,
            "source": "WDPA (UNEP-WCMC / IUCN)"}


class MpaIndex:
    """Fast point-in-MPA lookup (bbox pre-filter)."""

    def __init__(self, collection: Optional[Dict[str, Any]] = None, settings: Optional[Settings] = None) -> None:
        collection = collection if collection is not None else protected_areas(settings)
        self.items: List[Any] = []
        for f in collection.get("features") or []:
            box = bbox_of(f["geometry"])
            if box:
                self.items.append((box, f))

    def find(self, lat: float, lon: float) -> Optional[Dict[str, Any]]:
        for (west, south, east, north), feature in self.items:
            if west <= lon <= east and south <= lat <= north and point_in_geometry(lon, lat, feature["geometry"]):
                return feature["properties"]
        return None
