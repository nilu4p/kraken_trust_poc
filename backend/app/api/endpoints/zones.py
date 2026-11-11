"""Surveillance zones drawn on the map (named polygons, usable in custom rules)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import supervisor, viewer
from app.soc import zones

router = APIRouter(prefix="/zones", tags=["zones"])


class Zone(BaseModel):
    name: str = Field(..., max_length=80)
    description: str = Field("", max_length=1000)
    color: str = Field("#22d3ee", max_length=7)
    geometry: Dict[str, Any]


class ZoneUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=1000)
    color: Optional[str] = Field(None, max_length=7)
    geometry: Optional[Dict[str, Any]] = None


@router.get("")
def list_zones(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Zones as a GeoJSON FeatureCollection, with the enabled rules that use each one."""
    return {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": z["geometry"],
         "properties": {**{k: v for k, v in z.items() if k != "geometry"}, "used_by": zones.used_by(z["id"])}}
        for z in zones.list_zones()]}


@router.post("", status_code=201)
def create_zone(body: Zone, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    try:
        return zones.create(body.model_dump(), user["username"])
    except zones.ZoneError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/{zone_id}")
def update_zone(zone_id: int, body: ZoneUpdate, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    try:
        return zones.update(zone_id, body.model_dump(exclude_none=True), user["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Zone introuvable") from exc
    except zones.ZoneError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{zone_id}", status_code=204)
def delete_zone(zone_id: int, user: Dict[str, Any] = Depends(supervisor)) -> None:
    try:
        zones.delete(zone_id, user["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Zone introuvable") from exc
    except zones.ZoneError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
