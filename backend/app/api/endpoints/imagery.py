"""Satellite imagery served by the platform: Sentinel-2 10 m true colour, synchronised with the instant."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.api.deps import viewer
from app.core import timeframe
from app.sources import sentinel2

router = APIRouter(prefix="/imagery", tags=["imagery"])


@router.get("/sentinel2/scenes")
def sentinel2_scenes(
    at: Optional[str] = timeframe.AT_QUERY,
    days: int = Query(10, ge=1, le=30),
    max_cloud: float = Query(60, ge=0, le=100),
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Latest Sentinel-2 scene per tile acquired in the ``days`` before the reference instant."""
    when = timeframe.resolve(at)
    return {**sentinel2.scenes(when, days=days, max_cloud=max_cloud), "time": timeframe.describe(when)}


@router.get("/sentinel2/tiles/{item_id}/{z}/{x}/{y}.png")
async def sentinel2_tile(
    item_id: str = Path(..., max_length=40, pattern=r"^S2[ABC]_[0-9]{2}[A-Z]{3}_[0-9]{8}_[0-9]+_L2A$"),
    z: int = Path(..., ge=sentinel2.MIN_ZOOM, le=sentinel2.MAX_ZOOM),
    x: int = Path(..., ge=0),
    y: int = Path(..., ge=0),
    _user: Dict[str, Any] = Depends(viewer),
) -> Response:
    """One Web-Mercator map tile of a scene (PNG, transparent outside the image)."""
    try:
        data = await run_in_threadpool(sentinel2.render_tile, item_id, z, x, y)
    except sentinel2.Sentinel2Error as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(content=data, media_type="image/png", headers={"Cache-Control": "private, max-age=86400"})
