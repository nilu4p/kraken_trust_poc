"""Administration: API keys (write-only, encrypted), runtime settings, source health."""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import admin, request_ip, viewer
from app.config import get_settings
from app.core import audit, credentials
from app.db import db
from app.soc import runtime
from app.soc import notify
from app.soc.notify import WebhookRejected, validate_webhook_url
from app.sources import health
from app.sources.checks import CHECKS, check_service

router = APIRouter(tags=["system"])

_MAX = credentials.MAX_VALUE_LENGTH


class KeysUpdate(BaseModel):
    """Only present fields change; an empty string removes the dashboard value."""

    access_key: Optional[str] = Field(None, max_length=_MAX)
    secret_key: Optional[str] = Field(None, max_length=_MAX)
    gfw_token: Optional[str] = Field(None, max_length=_MAX)
    aisstream_api_key: Optional[str] = Field(None, max_length=_MAX)
    aishub_username: Optional[str] = Field(None, max_length=_MAX)
    opensanctions_api_key: Optional[str] = Field(None, max_length=_MAX)
    vesselapi_api_key: Optional[str] = Field(None, max_length=_MAX)
    alert_webhook_url: Optional[str] = Field(None, max_length=_MAX)


@router.get("/settings/keys")
def get_keys(_user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    """Credential status (configured / origin / masked hint) — never the values."""
    return {"keys": credentials.describe()}


@router.put("/settings/keys")
def put_keys(body: KeysUpdate, request: Request, user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="Aucune clé fournie")
    if changes.get("alert_webhook_url"):
        try:
            validate_webhook_url(changes["alert_webhook_url"])
        except WebhookRejected as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        keys = credentials.update(changes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Écriture du coffre impossible") from exc
    changed = sorted(credentials.CREDENTIALS[k]["env"] for k in changes)
    audit.record(
        "system.keys", actor=user["username"], target="credentials", ip=request_ip(request), details={"changed": changed}
    )
    notify.notify_security(
        "Clés d'API modifiées", level="info",
        details={"Clés": ", ".join(changed), "Par": user["username"], "Origine": request_ip(request)},
    )
    return {"keys": keys}


@router.post("/settings/keys/test/{service}")
def test_keys(service: str, body: Optional[KeysUpdate] = None, user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    """Validate a service's keys live (unsaved candidate values in the body take precedence)."""
    if service == "webhook":
        from app.soc.notify import CHANNELS, event_attachment

        results = {
            channel: notify.publish(
                channel,
                f"✅ Test du canal #{channel}",
                attachment=event_attachment(f"✅ Test du canal #{channel}", "success",
                                            [{"title": "Rôle du canal", "value": description, "short": False}]),
                blocking=True,
            )
            for channel, description in CHANNELS.items()
        }
        ok = all(results.values())
        failed = [c for c, r in results.items() if not r]
        return {"service": service, "ok": ok,
                "message": f"Test envoyé sur les {len(results)} canaux" if ok else f"Échec ou webhook non configuré ({', '.join(failed)})"}
    if service not in CHECKS:
        raise HTTPException(status_code=404, detail=f"Service inconnu : {service}")
    candidate = {k: v.strip() for k, v in (body.model_dump(exclude_none=True) if body else {}).items() if v and v.strip()}
    cfg = get_settings().model_copy(update=candidate) if candidate else get_settings()
    result = check_service(service, cfg)
    # Say which values were tested: typed (unsaved) values vs. the stored ones.
    result["tested"] = "saisie" if candidate else "enregistrées"
    audit.record(
        "system.keys_test", actor=user["username"], target=service,
        outcome="success" if result["ok"] else "failure", details={"tested": result["tested"]},
    )
    return {"service": service, **result}


@router.get("/system/settings")
def get_runtime(_user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    return {"settings": runtime.describe()}


@router.put("/system/settings")
def put_runtime(body: Dict[str, Any], user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    try:
        return {"settings": runtime.update(body, user["username"])}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/system/health")
def system_health(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    cfg = get_settings()
    with db() as conn:
        processed = conn.execute("SELECT COUNT(*), MAX(processed_at) FROM processed_scenes").fetchone()
    return {
        "sources": health.snapshot(),
        "surveillance": {
            "auto_scan_interval_minutes": cfg.auto_scan_interval_minutes,
            "scenes_processed": processed[0],
            "last_processed_at": processed[1],
        },
        "configured": {
            "copernicus_s3": bool(cfg.access_key and cfg.secret_key),
            "global_fishing_watch": bool(cfg.gfw_token),
            "aisstream": bool(cfg.aisstream_api_key),
            "aishub": bool(cfg.aishub_username),
            "opensanctions": bool(cfg.opensanctions_api_key),
            "vesselapi": bool(cfg.vesselapi_api_key),
            "webhook": bool(cfg.alert_webhook_url),
        },
    }


@router.get("/system/supervision")
def technical_supervision(_user: Dict[str, Any] = Depends(admin)) -> Dict[str, Any]:
    """Technical supervision board: sources, AIS collection, scans, rules tick, API latency, database, disk."""
    from app.soc import supervision

    return supervision.snapshot()


@router.get("/system/ai")
def ai_detector_status(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """AI opinions on radar targets: YOLO detector and xView3 (information only), each with its
    measured agreement with the operators' verdicts."""
    from app.pipeline import ai_detector
    from app.pipeline.aggregation import get_all_kraken_geojson
    from app.sources import xview3

    features = [f for f in get_all_kraken_geojson().get("features", []) if (f.get("properties") or {}).get("type") == "dark_vessel"]
    return {"status": ai_detector.status(), "evaluation": ai_detector.evaluation(features),
            "xview3": {"status": xview3.status(), "evaluation": ai_detector.evaluation(features, xview3.objectness_of)}}


@router.get("/system/catalogue")
def catalogue(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Every data source (integrated or pluggable), its access model and status."""
    from app.soc.catalogue import describe

    return {"sources": describe()}


@router.get("/sources")
def sources_status(_user: Dict[str, Any] = Depends(viewer)) -> Dict[str, Any]:
    """Which data sources are configured (no network calls)."""
    cfg = get_settings()
    return {
        "copernicus_stac": {"configured": True, "key_required": False},
        "copernicus_s3": {"configured": bool(cfg.access_key and cfg.secret_key), "key_required": True},
        "planetary_computer": {"configured": cfg.sar_source != "copernicus", "key_required": False},
        "xview3": {"configured": bool(cfg.xview3_url), "key_required": False},
        "copernicus_ccm_optical": {"configured": True, "key_required": False},
        "nasa_gibs": {"configured": True, "key_required": False},
        "marine_regions": {"configured": True, "key_required": False},
        "open_meteo": {"configured": True, "key_required": False},
        "global_fishing_watch": {"configured": bool(cfg.gfw_token), "key_required": True},
        "aisstream": {"configured": bool(cfg.aisstream_api_key), "key_required": True},
        "aishub": {"configured": bool(cfg.aishub_username), "key_required": True},
        "opensanctions": {"configured": bool(cfg.opensanctions_api_key), "key_required": True},
        "vesselapi": {"configured": bool(cfg.vesselapi_api_key), "key_required": True},
        "copernicus_marine": {"configured": cfg.cmems_enabled, "key_required": False},
        "wdpa": {"configured": True, "key_required": False},
    }
