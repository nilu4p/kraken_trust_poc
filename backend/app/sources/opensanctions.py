"""Vessel screening against sanctions and watchlists — OpenSanctions match API.

OpenSanctions consolidates OFAC, EU, UN, UK and many other lists, including
sanctioned vessels (IMO / MMSI / name / flag). A free API key is granted for
public-interest work; commercial use requires a licence.

``POST https://api.opensanctions.org/match/default`` with
``Authorization: ApiKey <key>``; results above ``sanctions_min_score`` are
reported. Results are cached 7 days per vessel identity (misses too).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT, cache_read, cache_write

logger = logging.getLogger(__name__)

API = "https://api.opensanctions.org/match/default"
TTL = 7 * 24 * 3600


def _first(props: Dict[str, Any], name: str) -> Optional[str]:
    values = props.get(name) or []
    return str(values[0]) if values and not isinstance(values[0], dict) else None


def screen_vessel(
    *, name: str = "", imo: Any = None, mmsi: Any = None, flag: str = "", settings: Optional[Settings] = None
) -> Optional[List[Dict[str, Any]]]:
    """Matches above the threshold (possibly empty), or None when unavailable / not configured."""
    cfg = settings or get_settings()
    if not cfg.opensanctions_api_key:
        return None
    properties: Dict[str, List[str]] = {}
    if name and name.strip() and name.strip().upper() not in ("AIS", "UNKNOWN"):
        properties["name"] = [name.strip()]
    if imo and str(imo).isdigit() and len(str(imo)) == 7:
        properties["imoNumber"] = [str(imo)]
    if mmsi and str(mmsi).isdigit() and len(str(mmsi)) == 9:
        properties["mmsi"] = [str(mmsi)]
    if not properties:
        return []
    if flag:
        properties["flag"] = [flag]
    key = repr(sorted(properties.items()))
    cached = cache_read("opensanctions", key, TTL, cfg)
    if cached is not None:
        return [m for m in cached if m["score"] >= cfg.sanctions_min_score]
    try:
        response = httpx.post(
            API,
            json={"queries": {"q": {"schema": "Vessel", "properties": properties}}},
            headers={"Authorization": f"ApiKey {cfg.opensanctions_api_key}", "User-Agent": USER_AGENT},
            timeout=cfg.http_timeout,
        )
    except Exception as exc:
        health.record("opensanctions", False, type(exc).__name__)
        return None
    if response.status_code >= 400:
        health.record("opensanctions", False, f"HTTP {response.status_code}")
        return None
    health.record("opensanctions", True)
    results = ((response.json().get("responses") or {}).get("q") or {}).get("results") or []
    matches = []
    for r in results:
        props = r.get("properties") or {}
        matches.append({
            "id": r.get("id"),
            "caption": r.get("caption"),
            "score": round(float(r.get("score") or 0), 3),
            "datasets": r.get("datasets") or [],
            "topics": props.get("topics") or [],
            "imo": _first(props, "imoNumber"),
            "mmsi": _first(props, "mmsi"),
            "flag": _first(props, "flag"),
            "url": f"https://www.opensanctions.org/entities/{r.get('id')}/" if r.get("id") else None,
        })
    cache_write("opensanctions", key, matches, cfg)
    return [m for m in matches if m["score"] >= cfg.sanctions_min_score]
