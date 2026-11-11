"""Rate-limit aware Copernicus STAC search (retry on 429/5xx + short disk cache).

The CDSE STAC API enforces a WAF rate limit; every catalogue query in the
project goes through :func:`stac_search` so dashboard refreshes and scans do
not trip it, and a stale cached answer is served when the API refuses.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT, cache_read, cache_read_stale, cache_write

logger = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


def stac_search(
    body: Dict[str, Any],
    *,
    settings: Optional[Settings] = None,
    ttl_seconds: float = 300.0,
    retries: int = 3,
) -> Optional[List[Dict[str, Any]]]:
    """POST /search and return raw item dicts, or None if unreachable and uncached."""
    cfg = settings or get_settings()
    key = json.dumps(body, sort_keys=True)
    cached = cache_read("stac", key, ttl_seconds, cfg)
    if cached is not None:
        return cached

    url = cfg.stac_url.rstrip("/") + "/search"
    delay = 2.0
    for attempt in range(retries):
        try:
            response = httpx.post(url, json=body, headers={"User-Agent": USER_AGENT}, timeout=cfg.http_timeout)
            if response.status_code in RETRY_STATUSES and attempt < retries - 1:
                wait = float(response.headers.get("Retry-After") or delay)
                logger.warning("STAC HTTP %s — retry in %.0fs", response.status_code, wait)
                time.sleep(min(wait, 20.0))
                delay *= 2
                continue
            if response.status_code >= 400:
                logger.warning("STAC search HTTP %s: %s", response.status_code, response.text[:200])
                health.record("copernicus_stac", False, f"HTTP {response.status_code}")
                break
            features = response.json().get("features") or []
            health.record("copernicus_stac", True)
            cache_write("stac", key, features, cfg)
            return features
        except Exception as exc:
            logger.warning("STAC search failed (attempt %s): %s", attempt + 1, exc)
            health.record("copernicus_stac", False, type(exc).__name__)
            time.sleep(delay)
            delay *= 2

    stale = cache_read_stale("stac", key, cfg)
    if stale is not None:
        logger.warning("STAC unavailable — serving stale cached result")
    return stale
