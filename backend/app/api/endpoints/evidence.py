"""Evidence vault endpoints (authenticated, every access audited)."""

from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.api.deps import analyst, request_ip, viewer
from app.core import audit
from app.soc import evidence as svc

router = APIRouter(prefix="/evidence", tags=["evidence"])


@router.get("")
def list_dossiers(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    return {"dossiers": svc.list_dossiers()}


@router.get("/files/{dossier}/{name}")
def get_file(
    dossier: str, name: str, request: Request, preview: bool = False, user: Dict[str, Any] = Depends(viewer)
) -> FileResponse:
    try:
        path = svc.file_path(dossier, name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # Chain of custody = deliberate consultations. Thumbnails and list / drawer previews
    # (``?preview=1``) are displayed constantly and would drown it: they are not logged.
    if not name.startswith("chip_") and not preview:
        audit.record("evidence.view", actor=user["username"], target=f"evidence:{dossier}", ip=request_ip(request), details={"file": name})
    return FileResponse(path, media_type=svc.media_type(path), headers={"Cache-Control": "private, max-age=300"})


@router.post("/{dossier}/verify")
def verify(dossier: str, request: Request, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    try:
        return svc.verify(dossier, user, request_ip(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{dossier}/export")
def export(dossier: str, request: Request, user: Dict[str, Any] = Depends(analyst)) -> Response:
    try:
        data = svc.export_pack(dossier, user, request_ip(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{dossier}_preuves.zip"', "Cache-Control": "no-store"},
    )
