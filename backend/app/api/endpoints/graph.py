"""Entity graph over time (link analysis): neighbourhood of vessels, incidents and radar detections."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import viewer
from app.core import timeframe
from app.soc import graph

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get("")
def entity_graph(
    focus: List[str] = Query(..., max_length=10, description="vessel:<mmsi>, incident:<id> or detection:<alert>:<target>"),
    at: Optional[str] = timeframe.AT_QUERY,
    days: float = Query(7, ge=0.25, le=90),
    depth: int = Query(1, ge=1, le=2),
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Links of the focus entities within the ``days`` before the reference instant, each with times and evidence."""
    if any(len(f) > 80 or ":" not in f for f in focus):
        raise HTTPException(status_code=422, detail="Entité de départ invalide")
    when = timeframe.resolve(at)
    return {**graph.build(focus, end=timeframe.instant(when), days=days, depth=depth), "time": timeframe.describe(when)}


@router.get("/search")
def entity_search(q: str = Query(..., min_length=2, max_length=60), _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Vessels (name, MMSI, IMO, call sign) and incidents (reference, title) matching the text."""
    return {"results": graph.search(q)}
