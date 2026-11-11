"""Vessels authorised to fish in the EEZ (licences) — excluded from foreign-fishing alerts."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import admin, supervisor, viewer
from app.soc import authorized as svc

router = APIRouter(prefix="/authorized", tags=["authorized"])


class AuthorizedEntry(BaseModel):
    name: str = Field("", max_length=200)
    mmsi: str = Field("", max_length=20)
    imo: str = Field("", max_length=20)
    call_sign: str = Field("", max_length=20)
    flag: str = Field("", max_length=60)
    licence: str = Field("", max_length=120)
    valid_until: Optional[str] = Field(None, max_length=10)
    note: str = Field("", max_length=300)


@router.get("")
def entries(q: str = Query("", max_length=64), _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Authorised vessels with their licence and validity (expired ones flagged)."""
    return svc.list_entries(q)


@router.post("")
def add(entry: AuthorizedEntry, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    """Add one licensed vessel (MMSI, IMO or call sign required)."""
    try:
        return svc.add_entry(entry.model_dump(), actor=user["username"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{entry_id}")
def remove(entry_id: int, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, str]:
    """Remove a licensed vessel: it is again subject to the foreign-fishing rule."""
    try:
        svc.remove_entry(entry_id, actor=user["username"])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "ok"}


@router.post("/import")
async def import_file(request: Request, replace: bool = True, user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    """CSV / XLS / XLSX licence list (raw body), max 10 MB; ``replace`` swaps the whole list."""
    try:
        return svc.import_file(await request.body(), actor=user["username"], replace=replace)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
