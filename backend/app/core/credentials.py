"""API keys editable from the dashboard — encrypted at rest, write-only through the API.

Values saved from the web UI are stored Fernet-encrypted (master key, see
``vault``) in ``keys/api_credentials.enc`` (0600) and override the ``.env``
values on the live Settings object, so a new key applies without restart.
A legacy plaintext ``api_credentials.json`` is migrated then deleted.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import Settings, get_settings
from app.core import vault

logger = logging.getLogger(__name__)

STORE_NAME = "api_credentials.enc"
LEGACY_STORE_NAME = "api_credentials.json"
_legacy_warned = False

# credential id -> Settings field / env var / UI metadata
CREDENTIALS: Dict[str, Dict[str, str]] = {
    "access_key": {
        "env": "ACCESS_KEY",
        "service": "copernicus_s3",
        "label": "Copernicus S3 — Access key",
        "signup": "https://eodata-s3keysmanager.dataspace.copernicus.eu/",
    },
    "secret_key": {
        "env": "SECRET_KEY",
        "service": "copernicus_s3",
        "label": "Copernicus S3 — Secret key",
        "signup": "https://eodata-s3keysmanager.dataspace.copernicus.eu/",
    },
    "gfw_token": {
        "env": "GFW_TOKEN",
        "service": "global_fishing_watch",
        "label": "Global Fishing Watch — Token",
        "signup": "https://globalfishingwatch.org/our-apis/tokens",
    },
    "aisstream_api_key": {
        "env": "AISSTREAM_API_KEY",
        "service": "aisstream",
        "label": "aisstream.io — API key",
        "signup": "https://aisstream.io/apikeys",
    },
    "aishub_username": {
        "env": "AISHUB_USERNAME",
        "service": "aishub",
        "label": "AISHub — Nom d'utilisateur (2e flux AIS)",
        "signup": "https://www.aishub.net/join-us",
    },
    "opensanctions_api_key": {
        "env": "OPENSANCTIONS_API_KEY",
        "service": "opensanctions",
        "label": "OpenSanctions — API key",
        "signup": "https://www.opensanctions.org/api/",
    },
    "vesselapi_api_key": {
        "env": "VESSELAPI_API_KEY",
        "service": "vesselapi",
        "label": "VesselAPI — API key (registre navires)",
        "signup": "https://vesselapi.com/pricing",
    },
    "alert_webhook_url": {
        "env": "ALERT_WEBHOOK_URL",
        "service": "webhook",
        "label": "Webhook d'alerte (Slack / Mattermost / Teams)",
        "signup": "https://api.slack.com/messaging/webhooks",
    },
}

MAX_VALUE_LENGTH = 4096

_lock = threading.Lock()
# Values coming from .env / environment, captured before any web override.
_env_values: Dict[str, str] = {}


def _store_path(cfg: Settings) -> Path:
    return cfg.keys_dir / STORE_NAME


def _read_store(cfg: Settings) -> Dict[str, str]:
    _migrate_legacy(cfg)
    path = _store_path(cfg)
    if not path.is_file():
        return {}
    try:
        data = json.loads(vault.decrypt(path.read_bytes(), cfg))
        return {k: str(v) for k, v in data.items() if k in CREDENTIALS and v}
    except Exception as exc:
        logger.error("Credential store unreadable (%s) — check MASTER_KEY", type(exc).__name__)
        return {}


def _write_store(cfg: Settings, values: Dict[str, str]) -> None:
    vault.write_private(_store_path(cfg), vault.encrypt(json.dumps(values).encode(), cfg))


def _migrate_legacy(cfg: Settings) -> None:
    legacy = cfg.keys_dir / LEGACY_STORE_NAME
    if not legacy.is_file():
        return
    try:
        values = {k: str(v) for k, v in json.loads(legacy.read_text(encoding="utf-8")).items() if k in CREDENTIALS and v}
        if not _store_path(cfg).is_file():
            _write_store(cfg, values)
        legacy.unlink()
        logger.warning("Plaintext credential store migrated to encrypted %s", STORE_NAME)
    except PermissionError:
        global _legacy_warned
        if not _legacy_warned:
            _legacy_warned = True
            logger.error(
                "Legacy plaintext %s is not readable by this process (created by an older root container). "
                "Delete it: sudo rm keys/%s — the encrypted store is used meanwhile.",
                LEGACY_STORE_NAME, LEGACY_STORE_NAME,
            )
    except Exception as exc:
        logger.error("Legacy credential migration failed: %s", type(exc).__name__)


def apply_stored_credentials(settings: Optional[Settings] = None) -> None:
    """Overlay web-saved credentials on the live Settings (call at startup)."""
    cfg = settings or get_settings()
    with _lock:
        for field in CREDENTIALS:
            _env_values.setdefault(field, getattr(cfg, field, "") or "")
        stored = _read_store(cfg)
        for field, value in stored.items():
            setattr(cfg, field, value)
    logger.info("Credentials loaded (%s overridden from the dashboard)", len(stored))


def _mask(value: str) -> str:
    if not value:
        return ""
    return f"••••{value[-4:]}" if len(value) > 8 else "••••"


def describe(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Status of every credential — configured, origin and a masked hint only."""
    cfg = settings or get_settings()
    stored = _read_store(cfg)
    result: Dict[str, Any] = {}
    for field, meta in CREDENTIALS.items():
        value = getattr(cfg, field, "") or ""
        origin = "dashboard" if field in stored else ("env" if value else None)
        result[field] = {**meta, "configured": bool(value), "origin": origin, "hint": _mask(value)}
    return result


def update(changes: Dict[str, Optional[str]], settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Set (non-empty string) or remove (None / "") dashboard overrides.

    Removing an override restores the ``.env`` value, if any.
    """
    cfg = settings or get_settings()
    unknown = set(changes) - set(CREDENTIALS)
    if unknown:
        raise ValueError(f"Clés inconnues : {', '.join(sorted(unknown))}")
    with _lock:
        for field in CREDENTIALS:
            _env_values.setdefault(field, getattr(cfg, field, "") or "")
        stored = _read_store(cfg)
        for field, raw in changes.items():
            value = (raw or "").strip()
            if len(value) > MAX_VALUE_LENGTH or any(ch in value for ch in "\r\n\t "):
                raise ValueError(f"Valeur invalide pour {CREDENTIALS[field]['env']}")
            if value:
                stored[field] = value
                setattr(cfg, field, value)
            else:
                stored.pop(field, None)
                setattr(cfg, field, _env_values.get(field, ""))
        _write_store(cfg, stored)
    logger.info("Credentials updated from dashboard: %s", ", ".join(CREDENTIALS[f]["env"] for f in changes))
    return describe(cfg)
