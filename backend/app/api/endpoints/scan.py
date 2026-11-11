"""On-demand Sentinel-1 scans."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from app.api.deps import analyst, viewer
from app.config import get_settings
from app.core import audit
from app.pipeline.orchestrator import run_kraken_pipeline
from app.pipeline.scan_state import snapshot

router = APIRouter(tags=["scan"])


@router.post("/scan")
def trigger_scan(
    background_tasks: BackgroundTasks,
    scene_id: Optional[str] = Query(None, max_length=200, pattern=r"^[A-Za-z0-9_\-]+$"),
    user: Dict[str, Any] = Depends(analyst),
) -> Dict[str, Any]:
    """Launch the SAR pipeline in the background (latest scene if ``scene_id`` is omitted)."""
    if snapshot().get("status") in ("running", "queued"):
        raise HTTPException(status_code=409, detail="Un scan est déjà en cours")
    audit.record("scan.start", actor=user["username"], target=f"scene:{scene_id or 'latest'}")
    if get_settings().background_mode == "workers":
        from app.tasks import enqueue_scan

        enqueue_scan(scene_id, user["username"])  # dedicated scan worker: the API stays responsive
        return {"status": "success", "message": "Scan Sentinel-1 mis en file.", "scene_id": scene_id}
    background_tasks.add_task(run_kraken_pipeline, None, scene_id, user["username"])
    return {"status": "success", "message": "Scan Sentinel-1 lancé.", "scene_id": scene_id}


@router.get("/scan/status")
def scan_status(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    return snapshot()
