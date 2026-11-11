"""Detection rules: built-in rules explained (parameters, sources, 30-day record) and custom rules."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import analyst, request_ip, supervisor, viewer
from app.core import audit, timeframe
from app.soc import custom_rules, rule_catalog

router = APIRouter(prefix="/rules", tags=["rules"])


class CustomRule(BaseModel):
    name: str = Field(..., max_length=80)
    description: str = Field("", max_length=1000)
    enabled: bool = True
    definition: Dict[str, Any]


class CustomRuleUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=80)
    description: Optional[str] = Field(None, max_length=1000)
    enabled: Optional[bool] = None
    definition: Optional[Dict[str, Any]] = None


class Preview(BaseModel):
    definition: Dict[str, Any]
    at: Optional[str] = Field(None, max_length=40)


@router.get("")
def list_rules(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Every rule: built-in (explained, parameters, sources, state, 30-day record) and custom ones."""
    return {"builtin": rule_catalog.describe(), "custom": custom_rules.list_rules(), "builder": custom_rules.catalogue()}


@router.put("/builtin/{rule_id}")
def toggle_builtin(rule_id: str, request: Request, enabled: bool = Body(..., embed=True),
                   user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    """Switch a built-in rule on or off (the radar rule cannot be switched off)."""
    try:
        rule_catalog.set_enabled(rule_id, enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    audit.record("rules.toggle", actor=user["username"], target=f"rule:{rule_id}", ip=request_ip(request),
                 details={"enabled": enabled})
    return {"rule": rule_id, "enabled": enabled}


@router.post("/preview")
def preview(body: Preview, _user: Dict[str, Any] = Depends(analyst)) -> Dict[str, Any]:
    """Test a rule without saving it: which vessels it catches now (or at a past instant), and why."""
    when = timeframe.resolve(body.at)
    try:
        return custom_rules.preview(body.definition, at=when)
    except custom_rules.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/custom", status_code=201)
def create_rule(body: CustomRule, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    try:
        return custom_rules.create(body.model_dump(), user["username"])
    except custom_rules.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.put("/custom/{rule_id}")
def update_rule(rule_id: int, body: CustomRuleUpdate, user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    try:
        return custom_rules.update(rule_id, body.model_dump(exclude_none=True), user["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Règle introuvable") from exc
    except custom_rules.RuleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/custom/{rule_id}", status_code=204)
def delete_rule(rule_id: int, user: Dict[str, Any] = Depends(supervisor)) -> None:
    try:
        custom_rules.delete(rule_id, user["username"])
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Règle introuvable") from exc
