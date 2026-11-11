"""SOC incident queue and workflow."""

from __future__ import annotations

from typing import Any, Dict, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import analyst, viewer
from app.config import get_settings
from app.soc import incidents as svc
from app.soc import notify

router = APIRouter(prefix="/incidents", tags=["incidents"])


class TransitionBody(BaseModel):
    status: str
    note: str = Field("", max_length=2000)
    resolution: Optional[str] = None


class AssignBody(BaseModel):
    assignee_id: Optional[int] = None


class CommentBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class EscalateBody(BaseModel):
    target: str
    reference: str = Field("", max_length=120)
    note: str = Field("", max_length=2000)


class ActionBody(BaseModel):
    done: bool


def _call(fn: Any, *args: Any, **kwargs: Any) -> Dict[str, Any]:
    try:
        return {"incident": fn(*args, **kwargs)}
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("")
def list_incidents(
    status: Optional[str] = Query(None, max_length=20),
    severity: Optional[Literal["P1", "P2", "P3", "P4"]] = None,
    scope: Literal["all", "open", "mine", "unassigned"] = "open",
    q: Optional[str] = Query(None, max_length=64),
    sort: Literal["recent", "oldest", "priority"] = "recent",
    user: Dict[str, Any] = Depends(viewer),
) -> Dict[str, Any]:
    """Chronological by opening date: newest first (default), ``oldest`` first, or by ``priority``."""
    items = svc.list_incidents(
        status=status,
        severity=severity,
        open_only=scope in ("open", "mine", "unassigned") and not status,
        assignee_id=user["id"] if scope == "mine" else None,
        search=q,
        sort=sort,
    )
    if scope == "unassigned":
        items = [i for i in items if not i["assignee_id"]]
    return {"incidents": items}


@router.get("/meta")
def meta(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    cfg = get_settings()
    return {
        "statuses": svc.STATUSES,
        "resolutions": svc.RESOLUTIONS,
        "triage_close_resolutions": sorted(svc.TRIAGE_CLOSE_RESOLUTIONS),
        "escalation_targets": svc.ESCALATION_TARGETS,
        "transitions": [{"from": a, "to": b, "role": r} for (a, b), r in svc.TRANSITIONS.items()],
        # Minutes per severity, for the deadline explanations of the console.
        "sla": {"triage_minutes": cfg.sla_triage_minutes, "resolve_minutes": cfg.sla_resolve_minutes},
    }


@router.get("/kpis")
def kpis(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    return svc.kpis()


@router.get("/{incident_id}")
def get_incident(incident_id: int, _user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    incident = svc.get_incident(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident introuvable")
    return {"incident": incident}


@router.post("/{incident_id}/transition")
def transition(incident_id: int, body: TransitionBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    before = svc.get_incident(incident_id)
    result = _call(svc.transition, incident_id, body.status, user, note=body.note, resolution=body.resolution)
    incident = result["incident"]
    kind = {"response": "response", "closed": "closed"}.get(body.status)
    if before and before["status"] == "closed" and body.status != "closed":
        kind = "reopened"
    if kind:
        notify.notify_workflow(
            incident, kind, user["username"],
            {"note": body.note, "resolution": body.resolution, "resolution_label": incident.get("resolution_label")},
        )
    return result


@router.post("/{incident_id}/assign")
def assign(incident_id: int, body: AssignBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    return _call(svc.assign, incident_id, body.assignee_id, user)


@router.post("/{incident_id}/comment")
def comment(incident_id: int, body: CommentBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    return _call(svc.comment, incident_id, body.text, user)


@router.post("/{incident_id}/escalate")
def escalate(incident_id: int, body: EscalateBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    result = _call(svc.escalate, incident_id, body.target, body.reference, body.note, user)
    notify.notify_workflow(
        result["incident"], "escalation", user["username"],
        {"label": svc.ESCALATION_TARGETS[body.target], "reference": body.reference, "note": body.note},
    )
    return result


@router.post("/{incident_id}/actions/{key}")
def action(incident_id: int, key: str, body: ActionBody, user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    return _call(svc.set_action, incident_id, key[:40], body.done, user)
