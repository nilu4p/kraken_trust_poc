"""Custom sources: declare, test and display any API / tile service from the dashboard."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import admin, request_ip, viewer
from app.core import audit
from app.core.identity import has_role
from app.sources import custom

router = APIRouter(prefix="/custom-sources", tags=["custom-sources"])

SourceId = Path(..., pattern=custom.ID_PATTERN)


class SourceBody(BaseModel):
    definition: custom.SourceDefinition
    # None = keep the stored secret · "" = remove it · value = replace it.
    secret: Optional[str] = Field(None, max_length=4096)


class TestBody(SourceBody):
    # Imagery date for tile / WMS sources using {date}.
    date: str = Field("", max_length=10)


def _not_found(source_id: str) -> HTTPException:
    return HTTPException(status_code=404, detail=f"Source inconnue : {source_id}")


@router.get("")
def list_sources(user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Administrators: full definitions (never the secret). Others: the enabled map layers only."""
    if has_role(user, "admin"):
        return {"sources": custom.list_sources(), "layers": custom.map_layers()}
    return {"layers": custom.map_layers()}


@router.post("")
def create_source(body: SourceBody, request: Request, user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    try:
        source = custom.save_source(body.definition, body.secret)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record("custom_source.create", actor=user["username"], target=source["id"], ip=request_ip(request),
                 details={"kind": source["kind"], "secret": source["has_secret"]})
    return {"source": source}


@router.put("/{source_id}")
def update_source(body: SourceBody, request: Request, source_id: str = SourceId,
                  user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    try:
        source = custom.save_source(body.definition, body.secret, source_id=source_id)
    except KeyError as exc:
        raise _not_found(source_id) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record("custom_source.update", actor=user["username"], target=source_id, ip=request_ip(request),
                 details={"enabled": source["enabled"], "secret_changed": body.secret is not None})
    return {"source": source}


@router.delete("/{source_id}")
def delete_source(request: Request, source_id: str = SourceId, user: Dict[str, Any] = Depends(admin)) -> Dict[str, str]:
    try:
        custom.delete_source(source_id)
    except KeyError as exc:
        raise _not_found(source_id) from exc
    audit.record("custom_source.delete", actor=user["username"], target=source_id, ip=request_ip(request))
    return {"status": "deleted"}


@router.post("/test")
def test_candidate(body: TestBody, user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    """Run an unsaved definition once. Without a typed secret, the stored one (same id) is used."""
    secret = body.secret
    if secret is None and body.definition.id:
        try:
            secret = custom.get_source(body.definition.id)[1]
        except KeyError:
            secret = ""
    result = custom.test_source(body.definition.model_dump(), (secret or "").strip(), date=body.date)
    audit.record("custom_source.test", actor=user["username"], target=body.definition.id or body.definition.name,
                 outcome="success" if result["ok"] else "failure", details={"stage": result["stage"]})
    return result


@router.post("/{source_id}/test")
def test_saved(source_id: str = SourceId, date: str = Query("", max_length=10), user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    try:
        definition, secret = custom.get_source(source_id)
    except KeyError as exc:
        raise _not_found(source_id) from exc
    result = custom.test_source(definition, secret, date=date)
    audit.record("custom_source.test", actor=user["username"], target=source_id,
                 outcome="success" if result["ok"] else "failure", details={"stage": result["stage"]})
    return result


@router.get("/{source_id}/data")
def source_data(source_id: str = SourceId, _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """GeoJSON of an enabled object source (cached, last good copy if the source is down)."""
    try:
        definition, _secret = custom.get_source(source_id)
        if not definition.get("enabled"):
            raise KeyError(source_id)
        return custom.cached_features(source_id)
    except KeyError as exc:
        raise _not_found(source_id) from exc
    except custom.CustomSourceError as exc:
        raise HTTPException(status_code=502, detail=f"Source indisponible ({exc.stage}) : {exc}") from exc


@router.get("/{source_id}/tiles/{z}/{x}/{y}")
def source_tile(
    source_id: str = SourceId,
    z: int = Path(..., ge=0, le=22),
    x: int = Path(..., ge=0),
    y: int = Path(..., ge=0),
    date: str = Query("", max_length=10),
    _user: Dict[str, Any] = Depends(viewer),
) -> Response:
    """Tile proxied by the backend: the source's secret never reaches the browser."""
    try:
        body, content_type = custom.cached_tile(source_id, z, x, y, date)
    except KeyError as exc:
        raise _not_found(source_id) from exc
    except custom.CustomSourceError as exc:
        raise HTTPException(status_code=502, detail=f"Tuile indisponible ({exc.stage}) : {exc}") from exc
    return Response(body, media_type=content_type, headers={"Cache-Control": "private, max-age=600"})
