"""Vessel watchlist (Combined IUU list import, manual entries)."""

from __future__ import annotations

from typing import Any, Dict, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.api.deps import admin, analyst, viewer
from app.soc import watchlist as svc

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class EntryBody(BaseModel):
    name: str = Field("", max_length=200)
    mmsi: str = Field("", max_length=20)
    imo: str = Field("", max_length=20)
    flag: str = Field("", max_length=60)
    reason: str = Field("", max_length=300)


@router.get("")
def entries(q: str = Query("", max_length=64), _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    return svc.list_entries(q)


@router.post("/import")
async def import_file(
    request: Request,
    source: Literal["iuu_combined", "national", "other"] = "iuu_combined",
    user: Dict[str, Any] = Depends(admin),
) -> Dict[str, Any]:
    """Raw XLS / XLSX / CSV body (Content-Type: application/octet-stream), max 10 MB."""
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="Fichier vide")
    try:
        report = svc.parse_report(data)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=422, detail="Fichier illisible (XLS, XLSX ou CSV attendu)") from exc
    count = svc.import_entries(report["entries"], source=source, actor=user["username"])
    return {
        "imported": count,
        "source": source,
        "format": report["format"],
        "sheet": report["sheet"],
        "layout": report["layout"],
        "skipped_delisted": report["skipped_delisted"],
        "with_mmsi": sum(1 for e in report["entries"] if e.get("mmsi")),
        "with_imo": sum(1 for e in report["entries"] if e.get("imo")),
        "with_call_sign": sum(1 for e in report["entries"] if e.get("call_sign")),
    }


@router.post("")
def add(body: EntryBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    try:
        return {"entry": svc.add_entry(body.model_dump(), actor=user["username"])}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/{entry_id}")
def remove(entry_id: int, user: Dict[str, Any] = Depends(admin)) -> Dict[str, str]:
    try:
        svc.remove_entry(entry_id, actor=user["username"])
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}
