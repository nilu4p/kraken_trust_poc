"""Shared HTTP + JSON disk cache helpers for open-data sources."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

from app import __version__
from app.config import Settings, get_settings
from app.sources import health

logger = logging.getLogger(__name__)

USER_AGENT = f"KRAKEN-Trust/{__version__} (Space4Yana EEZ monitoring)"


def _cache_path(cfg: Settings, namespace: str, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return cfg.cache_dir / namespace / f"{digest}.json"


def cache_read(namespace: str, key: str, ttl_seconds: float, settings: Optional[Settings] = None) -> Optional[Any]:
    """Return a cached JSON payload younger than ``ttl_seconds`` (or None)."""
    path = _cache_path(settings or get_settings(), namespace, key)
    try:
        if path.is_file() and time.time() - path.stat().st_mtime < ttl_seconds:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Unreadable cache %s: %s", path, exc)
    return None


def cache_read_stale(namespace: str, key: str, settings: Optional[Settings] = None) -> Optional[Any]:
    """Return a cached payload regardless of age (last-resort offline fallback)."""
    return cache_read(namespace, key, float("inf"), settings)


def atomic_write(path: Path, data: bytes) -> None:
    """Write ``data`` through a temporary file then rename it: readers (other requests, the workers)
    never see a half-written file, and a crash mid-write never leaves a truncated cache entry."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)


def cache_write(namespace: str, key: str, payload: Any, settings: Optional[Settings] = None) -> None:
    path = _cache_path(settings or get_settings(), namespace, key)
    try:
        atomic_write(path, json.dumps(payload).encode("utf-8"))
    except Exception as exc:
        logger.warning("Unable to write cache %s: %s", path, exc)


def get_json(
    url: str,
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    settings: Optional[Settings] = None,
    source: str = "",
) -> Optional[Any]:
    """GET a JSON document; returns None on any network / HTTP / decoding error."""
    cfg = settings or get_settings()
    try:
        response = httpx.get(
            url,
            params=params,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
            timeout=cfg.http_timeout,
            follow_redirects=True,
        )
        if response.status_code >= 400:
            logger.warning("GET %s -> HTTP %s", url, response.status_code)
            if source:
                health.record(source, False, f"HTTP {response.status_code}")
            return None
        data = response.json()
        if source:
            health.record(source, True)
        return data
    except Exception as exc:
        logger.warning("GET %s failed: %s", url, exc)
        if source:
            health.record(source, False, type(exc).__name__)
        return None


def post_json(
    url: str,
    body: Dict[str, Any],
    *,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
    settings: Optional[Settings] = None,
    source: str = "",
) -> Optional[Any]:
    """POST a JSON body; returns the decoded JSON or None on any failure."""
    cfg = settings or get_settings()
    try:
        response = httpx.post(
            url,
            json=body,
            params=params,
            headers={"User-Agent": USER_AGENT, **(headers or {})},
            timeout=cfg.http_timeout,
        )
        if response.status_code >= 400:
            logger.warning("POST %s -> HTTP %s: %s", url, response.status_code, response.text[:200])
            if source:
                health.record(source, False, f"HTTP {response.status_code}")
            return None
        data = response.json()
        if source:
            health.record(source, True)
        return data
    except Exception as exc:
        logger.warning("POST %s failed: %s", url, exc)
        if source:
            health.record(source, False, type(exc).__name__)
        return None
