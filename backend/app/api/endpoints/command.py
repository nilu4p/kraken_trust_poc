"""Command support: decision aid per incident, intervention means, reports to the chain of command."""

from __future__ import annotations

import datetime
from typing import Any, Dict, Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import analyst, supervisor, viewer
from app.config import get_settings
from app.core.identity import has_role
from app.soc import assets, decision, reports
from app.soc import incidents as incidents_svc

router = APIRouter(tags=["command"])


class Asset(BaseModel):
    name: str = Field(..., max_length=80)
    kind: str = Field(..., max_length=20)
    base_name: str = Field("", max_length=80)
    base_lat: float
    base_lon: float
    speed_kn: float
    endurance_nm: float
    readiness_min: int = 60
    can_board: bool = True
    status: str = Field("available", max_length=20)
    notes: str = Field("", max_length=500)


class AssetUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    kind: Optional[str] = Field(None, max_length=20)
    base_name: Optional[str] = Field(None, max_length=80)
    base_lat: Optional[float] = None
    base_lon: Optional[float] = None
    speed_kn: Optional[float] = None
    endurance_nm: Optional[float] = None
    readiness_min: Optional[int] = None
    can_board: Optional[bool] = None
    status: Optional[str] = Field(None, max_length=20)
    notes: Optional[str] = Field(None, max_length=500)


class ReportBody(BaseModel):
    assessment: str = Field("", max_length=8000)
    proposals: str = Field("", max_length=8000)
    addressees: str = Field("", max_length=500)
    classification: Optional[str] = Field(None, max_length=40)
    issue: bool = False


class SitrepBody(ReportBody):
    start: Optional[datetime.datetime] = None
    end: Optional[datetime.datetime] = None


# --------------------------------------------------------------------------- decision support


@router.get("/incidents/{incident_id}/decision")
def incident_decision(incident_id: int, _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Target now, interception by each means, courses of action and the recommended one (nothing stored)."""
    incident = incidents_svc.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident introuvable")
    return decision.assess(incident)


# --------------------------------------------------------------------------- intervention means


@router.get("/assets")
def list_assets(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    return {"assets": assets.list_assets(), "kinds": assets.KINDS, "statuses": assets.STATUSES}


@router.post("/assets", status_code=201)
def create_asset(body: Asset, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    try:
        return assets.create(body.model_dump(), user["username"])
    except assets.AssetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/assets/{asset_id}")
def update_asset(asset_id: int, body: AssetUpdate, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    try:
        return assets.update(asset_id, body.model_dump(exclude_none=True), user["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Moyen introuvable") from exc
    except assets.AssetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: int, user: Dict[str, Any] = Depends(supervisor)) -> None:
    try:
        assets.delete(asset_id, user["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Moyen introuvable") from exc


# --------------------------------------------------------------------------- reports


def _report_call(fn: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except reports.ReportError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/reports")
def list_reports(
    kind: Optional[Literal["incident", "sitrep"]] = None,
    incident_id: Optional[int] = None,
    _user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    default = get_settings().report_classification.strip().upper()
    return {"reports": reports.list_reports(kind=kind, incident_id=incident_id),
            "classifications": sorted({*reports.CLASSIFICATIONS, default}), "default_classification": default}


@router.post("/reports/incident/{incident_id}")
def incident_report(incident_id: int, body: ReportBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    """Preview (``issue: false``) or issue the signed report of an incident."""
    return _report_call(reports.draft_incident, incident_id, user, assessment=body.assessment, proposals=body.proposals,
                        addressees=body.addressees, classification=body.classification, issue=body.issue)


@router.post("/reports/sitrep")
def sitrep(body: SitrepBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    """Preview a SITREP (analyst) or issue it (supervisor: it engages the centre)."""
    if body.issue and not has_role(user, "supervisor"):
        raise HTTPException(status_code=403, detail="L'émission d'un SITREP est réservée aux superviseurs")
    for value in (body.start, body.end):
        if value is not None and value.tzinfo is None:
            raise HTTPException(status_code=422, detail="Dates avec fuseau horaire attendues (ISO 8601, ex. 2026-10-22T06:00:00Z)")
    return _report_call(reports.draft_sitrep, user, start=body.start, end=body.end, assessment=body.assessment,
                        proposals=body.proposals, addressees=body.addressees, classification=body.classification,
                        issue=body.issue)


@router.get("/reports/{report_id}")
def get_report(report_id: int, _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    report = reports.get_report(report_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Rapport introuvable")
    report.pop("certificate", None)
    return {"report": report}


@router.post("/reports/{report_id}/verify")
def verify_report(report_id: int, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    return _report_call(reports.verify, report_id, user)


@router.get("/reports/{report_id}/export")
def export_report(report_id: int, user: Dict[str, Any] = Depends(analyst)) -> Response:
    """The signed message as plain text, followed by its seal (hash, signature, certificate)."""
    try:
        name, text = reports.export_text(report_id, user)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        text.encode("utf-8"), media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
                 "Cache-Control": "no-store"},
    )
