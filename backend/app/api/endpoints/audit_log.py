"""Audit trail consultation and chain verification (supervisor+)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import supervisor
from app.core import audit

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("")
def entries(
    target: Optional[str] = Query(None, max_length=120),
    actor: Optional[str] = Query(None, max_length=64),
    action: Optional[str] = Query(None, max_length=40),
    before_id: Optional[int] = None,
    limit: int = Query(200, ge=1, le=1000),
    _user: Dict[str, Any] = Depends(supervisor),
) -> Dict[str, Any]:
    return {"entries": audit.search(target=target, actor=actor, action_prefix=action, before_id=before_id, limit=limit)}


@router.get("/verify")
def verify(_user: Dict[str, Any] = Depends(supervisor)) -> Dict[str, Any]:
    return audit.verify_chain()
