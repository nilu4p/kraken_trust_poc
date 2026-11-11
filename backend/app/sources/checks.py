"""Live validation of user-supplied API keys (one cheap request per service)."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _result(ok: bool, message: str) -> Dict[str, Any]:
    return {"ok": ok, "message": message}


def _boto_error(exc: Exception) -> str:
    """Exact S3 error code (InvalidAccessKeyId, SignatureDoesNotMatch, AccessDenied, ...)."""
    response = getattr(exc, "response", None) or {}
    code = (response.get("Error") or {}).get("Code")
    status = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    return f"{code or type(exc).__name__}{f' (HTTP {status})' if status else ''}"


def check_copernicus_s3(cfg: Settings) -> Dict[str, Any]:
    """Test exactly what a scan does (GDAL read of a real Sentinel-1 COG), then explain failures."""
    if not (cfg.access_key and cfg.secret_key):
        return _result(False, "ACCESS_KEY et SECRET_KEY requis")
    from app.pipeline.detection import probe_sar_access

    probe = probe_sar_access(cfg)
    if probe["ok"]:
        return _result(True, f"Clés valides — {probe['message']}")
    if probe["code"] in ("stac", "rasterio", "network", "tls"):
        return _result(False, probe["message"])
    # GDAL refused: ask S3 directly for the precise error code.
    try:
        import boto3
        from botocore.config import Config

        boto3.client(
            "s3",
            endpoint_url=cfg.s3_endpoint,
            aws_access_key_id=cfg.access_key,
            aws_secret_access_key=cfg.secret_key,
            region_name="default",
            config=Config(connect_timeout=cfg.http_timeout, read_timeout=cfg.http_timeout, retries={"max_attempts": 1}),
        ).list_objects_v2(Bucket="eodata", Prefix="Sentinel-1/", MaxKeys=1)
    except Exception as exc:
        code = _boto_error(exc)
        hints = {
            "SignatureDoesNotMatch": "SECRET_KEY incorrecte",
            "InvalidAccessKeyId": "ACCESS_KEY inconnue ou expirée — régénérez une paire",
            "AccessDenied": "accès refusé (clés révoquées ou quota Copernicus atteint)",
        }
        hint = next((h for k, h in hints.items() if k in code), "voir le code d'erreur")
        return _result(False, f"Copernicus S3 refuse ces clés : {code} — {hint}")
    return _result(False, f"Clés acceptées par S3 mais lecture SAR impossible : {probe['message']}")


def check_gfw(cfg: Settings) -> Dict[str, Any]:
    if not cfg.gfw_token:
        return _result(False, "GFW_TOKEN non renseigné")
    try:
        response = httpx.get(
            "https://gateway.api.globalfishingwatch.org/v3/events",
            params={"datasets[0]": "public-global-gaps-events:latest", "limit": 1, "offset": 0},
            headers={"Authorization": f"Bearer {cfg.gfw_token}"},
            timeout=cfg.http_timeout,
        )
    except Exception as exc:
        return _result(False, f"API GFW injoignable : {exc}")
    if response.status_code in (401, 403):
        return _result(False, "Token GFW invalide")
    if response.status_code >= 400:
        return _result(False, f"API GFW : HTTP {response.status_code}")
    return _result(True, "Token Global Fishing Watch validé")


async def _probe_aisstream(cfg: Settings, wait_seconds: float) -> Dict[str, Any]:
    import websockets

    from app.sources.aisstream import STREAM_URL

    west, south, east, north = cfg.eez_bbox
    async with websockets.connect(STREAM_URL, open_timeout=cfg.http_timeout) as ws:
        await ws.send(
            json.dumps(
                {
                    "APIKey": cfg.aisstream_api_key,
                    "BoundingBoxes": [[[south, west], [north, east]]],
                    "FilterMessageTypes": ["PositionReport"],
                }
            )
        )
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=wait_seconds)
        except asyncio.TimeoutError:
            return _result(True, f"Clé acceptée (aucun navire entendu en {wait_seconds:.0f} s)")
        message = json.loads(raw)
        if isinstance(message, dict) and message.get("error"):
            return _result(False, f"aisstream.io : {message['error']}")
        return _result(True, "Clé aisstream.io validée (flux AIS reçu)")


def check_aisstream(cfg: Settings, wait_seconds: float = 6.0) -> Dict[str, Any]:
    if not cfg.aisstream_api_key:
        return _result(False, "AISSTREAM_API_KEY non renseignée")
    try:
        return asyncio.run(_probe_aisstream(cfg, wait_seconds))
    except ImportError:
        return _result(False, "Module websockets absent (pip install websockets)")
    except Exception as exc:
        # aisstream closes the socket on an invalid key.
        return _result(False, f"Connexion refusée — clé invalide ? ({str(exc)[:120]})")


def check_aishub(cfg: Settings) -> Dict[str, Any]:
    if not cfg.aishub_username:
        return _result(False, "AISHUB_USERNAME non renseigné")
    from app.sources.aishub import AisHubError, fetch

    try:
        positions, _statics = fetch(cfg)
    except AisHubError as exc:
        return _result(False, f"AISHub : {exc}")
    except Exception as exc:
        return _result(False, f"AISHub injoignable : {type(exc).__name__}")
    return _result(True, f"Compte AISHub validé ({len(positions)} navire(s) sur la zone)")


def check_opensanctions(cfg: Settings) -> Dict[str, Any]:
    if not cfg.opensanctions_api_key:
        return _result(False, "OPENSANCTIONS_API_KEY non renseignée")
    try:
        response = httpx.post(
            "https://api.opensanctions.org/match/default",
            json={"queries": {"t": {"schema": "Vessel", "properties": {"name": ["TEST VESSEL"]}}}},
            headers={"Authorization": f"ApiKey {cfg.opensanctions_api_key}"},
            timeout=cfg.http_timeout,
        )
    except Exception as exc:
        return _result(False, f"API OpenSanctions injoignable : {type(exc).__name__}")
    if response.status_code in (401, 403):
        return _result(False, "Clé OpenSanctions refusée")
    if response.status_code == 429:
        return _result(False, "Quota OpenSanctions atteint")
    if response.status_code >= 400:
        return _result(False, f"API OpenSanctions : HTTP {response.status_code}")
    return _result(True, "Clé OpenSanctions validée")


def check_vesselapi(cfg: Settings) -> Dict[str, Any]:
    """One registry lookup (a well-known MMSI): costs one call of the monthly quota."""
    if not cfg.vesselapi_api_key:
        return _result(False, "VESSELAPI_API_KEY non renseignée")
    from app.sources.vesselapi import VesselApiError, _get, usage

    try:
        _get("/vessel/227000000", {"filter.idType": "mmsi"}, cfg)
    except VesselApiError as exc:
        return _result(False, f"VesselAPI : {exc}")
    state = usage(cfg)
    quota = f"{state['remaining']} appel(s) restant(s) chez le fournisseur" if state.get("remaining") is not None \
        else f"{state['left']} appel(s) restant(s) sur le budget de {state['budget']}"
    return _result(True, f"Clé VesselAPI validée — {quota} ce mois-ci")


CHECKS = {
    "copernicus_s3": check_copernicus_s3,
    "global_fishing_watch": check_gfw,
    "aisstream": check_aisstream,
    "aishub": check_aishub,
    "opensanctions": check_opensanctions,
    "vesselapi": check_vesselapi,
}


def check_service(service: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Run the live check for one service; never raises."""
    check = CHECKS.get(service)
    if check is None:
        return _result(False, f"Service inconnu : {service}")
    try:
        return check(settings or get_settings())
    except Exception as exc:
        logger.exception("Credential check %s crashed", service)
        return _result(False, f"Erreur inattendue : {exc}")
