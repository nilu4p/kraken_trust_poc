"""Tactical feed (dark vessels, AIS, scanned footprints) and maritime boundaries."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import viewer
from app.core import timeframe
from app.pipeline.aggregation import get_all_kraken_geojson
from app.sources.marine_regions import boundaries_feature_collection

router = APIRouter(tags=["situation"])


@router.get("/alerts")
def fetch_alerts(
    days: int = Query(30, ge=0, le=3650, description="Radar targets of the N days before the instant (0 = all); open incidents always shown"),
    at: Optional[str] = timeframe.AT_QUERY,
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Dark vessels (with C2PA status and linked incident as of the instant), AIS tracks, S1 footprints."""
    when = timeframe.resolve(at)
    return {**get_all_kraken_geojson(max_age_days=days or None, at=when), "time": timeframe.describe(when)}


@router.get("/zee")
def fetch_zee_polygon(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """French Guiana EEZ, contiguous zone and territorial sea (Marine Regions, offline fallback)."""
    return boundaries_feature_collection()
