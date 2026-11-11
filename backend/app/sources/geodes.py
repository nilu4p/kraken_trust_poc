"""CNES GEODES catalogue (STAC, public search + public quicklooks).

CNES distributes through GEODES the PEPS mirror of Sentinel-1 / Sentinel-2,
THEIA higher-level products (Sentinel-2 L2A reflectance, water quality) and
the SPOT World Heritage archive. Searching and quicklooks are free and
keyless; product download requires a (free) CNES account.

Used for: extra Sentinel-1 / Sentinel-2 coverage around a target (with real
quicklook images), and as a catalogue fallback when Copernicus is saturated.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT, cache_read, cache_write

logger = logging.getLogger(__name__)

SEARCH_URL = "https://geodes-portal.cnes.fr/api/stac/search"
COLLECTIONS = {
    "PEPS_S1_L1": "Sentinel-1 (CNES PEPS)",
    "PEPS_S2_L1C": "Sentinel-2 L1C (CNES PEPS)",
    "THEIA_REFLECTANCE_SENTINEL2_L2A": "Sentinel-2 L2A (CNES THEIA)",
    "THEIA_WATERQUAL_SENTINEL2_L2B": "Qualité de l'eau S2 (CNES THEIA)",
}


def search(
    lat: float,
    lon: float,
    start: str,
    end: str,
    *,
    limit: int = 20,
    settings: Optional[Settings] = None,
) -> Optional[List[Dict[str, Any]]]:
    """Raw GEODES items covering a ~10 km box around the point, acquired in [start, end]."""
    cfg = settings or get_settings()
    bbox = [round(lon - 0.05, 3), round(lat - 0.05, 3), round(lon + 0.05, 3), round(lat + 0.05, 3)]
    key = f"{bbox}|{start}|{end}|{limit}"
    cached = cache_read("geodes", key, 3600, cfg)
    if cached is not None:
        return cached
    items: List[Dict[str, Any]] = []
    failures = 0
    # GEODES returns nothing for multi-collection searches: query each collection.
    for collection in COLLECTIONS:
        body = {
            "collections": [collection],
            # GEODES ignores point ``intersects``: a small bbox is used instead.
            "bbox": bbox,
            "datetime": f"{start}T00:00:00Z/{end}T00:00:00Z",
            "limit": limit,
        }
        try:
            response = httpx.post(SEARCH_URL, json=body, headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
            if response.status_code >= 400:
                failures += 1
                health.record("cnes_geodes", False, f"HTTP {response.status_code}")
                continue
            items.extend(response.json().get("features") or [])
            health.record("cnes_geodes", True)
        except Exception as exc:
            failures += 1
            health.record("cnes_geodes", False, type(exc).__name__)
    if failures == len(COLLECTIONS):
        return None
    # Acquisition window is enforced on start_datetime (``datetime`` is the ingestion date).
    items = [i for i in items if start <= str((i.get("properties") or {}).get("start_datetime", ""))[:10] <= end]
    cache_write("geodes", key, items, cfg)
    return items
