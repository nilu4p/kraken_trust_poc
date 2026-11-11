"""VesselAPI: registry lookups, optional position feed, monthly call budget."""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List

import httpx
import pytest

from app.config import get_settings
from app.db import db
from app.soc import catalogue, rules
from app.sources import ais_store, checks, vesselapi

NOW = datetime.datetime.now(datetime.timezone.utc)

VESSEL = {"_meta": {}, "vessel": {
    "imo": 9123456, "mmsi": 710123456, "name": "PESCADOR I", "call_sign": "PP1", "country": "Brazil", "country_code": "BR",
    "vessel_type": "Fishing", "vessel_subtype": "Trawler", "year_built": 2004, "length": "28.5", "breadth": 7,
    "gross_tonnage": 180, "home_port": "Belem", "former_names": [{"name": "MAR AZUL", "year_until": 2015}, {"name": ""}],
}}

PAGE_1 = {"nextToken": "t2", "vessels": [
    {"mmsi": 710123456, "imo": 9123456, "vessel_name": "PESCADOR I", "latitude": 5.5, "longitude": -52.5, "sog": 3.2,
     "cog": 90, "heading": 511, "nav_status": 7, "timestamp": NOW.isoformat(), "suspected_glitch": False},
    {"mmsi": 765000001, "vessel_name": "GLITCH", "latitude": 5.0, "longitude": -52.0, "suspected_glitch": True},
]}
PAGE_2 = {"vessels": [{"mmsi": 765000002, "vessel_name": "", "latitude": 91, "longitude": 181}]}


def eez(_lon: float, _lat: float) -> str:
    return "eez"


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Mocked VesselAPI: records every call; answers by path."""
    calls: List[Dict[str, Any]] = []
    cfg = get_settings()
    monkeypatch.setattr(cfg, "vesselapi_api_key", "k")
    monkeypatch.setattr(cfg, "vesselapi_monthly_calls", 150)

    def fake_get(url: str, params: Dict[str, Any] = None, headers: Dict[str, str] = None, **_kw: Any) -> httpx.Response:
        calls.append({"url": url, "params": dict(params or {}), "headers": headers})
        request = httpx.Request("GET", url)
        if "/vessel/" in url:
            if url.endswith("/000000000"):
                return httpx.Response(404, request=request, json={"error": {"code": "not_found"}})
            return httpx.Response(200, request=request, json=VESSEL, headers={"X-RateLimit-Remaining": "120"})
        page = PAGE_2 if (params or {}).get("pagination.nextToken") == "t2" else PAGE_1
        return httpx.Response(200, request=request, json=page)

    monkeypatch.setattr(vesselapi.httpx, "get", fake_get)
    vesselapi._last_end.clear()
    return calls


def test_registry_lookup_is_parsed_cached_and_counted(api: List[Dict[str, Any]]) -> None:
    record = vesselapi.vessel_record("710123456")
    assert record["imo"] == "9123456" and record["flag"] == "Brazil" and record["length_m"] == 28.5
    assert record["former_names"] == [{"name": "MAR AZUL", "until": 2015}]
    assert api[0]["headers"]["Authorization"] == "Bearer k" and api[0]["params"] == {"filter.idType": "mmsi"}
    assert vesselapi.vessel_record("710123456") == record and len(api) == 1  # cached
    assert vesselapi.vessel_record("000000000") == {} and vesselapi.vessel_record("000000000") == {}  # miss cached too
    assert len(api) == 2
    state = vesselapi.usage()
    assert state["calls"] == 2 and state["remaining"] == 120 and state["left"] == 120
    assert vesselapi.vessel_record("not-an-mmsi") is None and len(api) == 2


def test_budget_exhausted_stops_calls(api: List[Dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "vesselapi_monthly_calls", 1)
    assert vesselapi.vessel_record("710123456") is not None
    assert vesselapi.vessel_record("710123457") is None and len(api) == 1
    monkeypatch.setattr(get_settings(), "vesselapi_monthly_calls", 0)  # 0 disables VesselAPI
    assert vesselapi.vessel_record("710123458") is None and len(api) == 1


def test_quota_error_reported_in_health(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "vesselapi_api_key", "k")
    monkeypatch.setattr(vesselapi.httpx, "get", lambda url, **_kw: httpx.Response(
        429, request=httpx.Request("GET", url), json={"error": {"code": "rate_limit_exceeded"}}))
    assert vesselapi.vessel_record("710123456") is None
    assert vesselapi.usage()["left"] == 0  # provider said the quota is gone
    result = checks.check_service("vesselapi")
    assert result["ok"] is False and "budget" in result["message"]


def test_position_feed_paginates_and_fuses(api: List[Dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "vesselapi_poll_minutes", 30)
    assert vesselapi.poll_once(eez) == 1  # glitch and out-of-range fixes dropped
    first, second = api[0]["params"], api[1]["params"]
    assert second["pagination.nextToken"] == "t2" and first["time.from"] == second["time.from"]
    assert first["filter.lonLeft"] == get_settings().eez_bbox[0] and first["time.to"].endswith("Z")
    [vessel] = ais_store.vessels(1)
    assert vessel["last_source"] == "vesselapi" and vessel["imo"] == "9123456"
    with db() as conn:
        assert conn.execute("SELECT heading FROM ais_positions WHERE mmsi = '710123456'").fetchone()[0] is None
    # Next poll starts right after the previous window (no gap, no overlap).
    previous_end = vesselapi._last_end["end"]
    vesselapi.poll_once(eez)
    start = datetime.datetime.fromisoformat(api[2]["params"]["time.from"].replace("Z", "+00:00"))
    assert start == previous_end + datetime.timedelta(milliseconds=1)


def test_position_feed_keeps_registry_reserve(api: List[Dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "vesselapi_monthly_calls", 10)
    with db() as conn:
        conn.execute("INSERT INTO rule_state (key, value, updated_at) VALUES (?, ?, ?)",
                     (vesselapi.USAGE_KEY, json.dumps({"month": NOW.strftime("%Y-%m"), "calls": 7, "remaining": None}),
                      NOW.isoformat()))
    with pytest.raises(vesselapi.VesselApiError, match="budget"):
        vesselapi.poll_once(eez)
    assert not api
    assert vesselapi.vessel_record("710123456") is not None  # registry may still use the reserve


def test_intel_pass_looks_up_foreign_fishing_vessels_only(api: List[Dict[str, Any]]) -> None:
    ais_store.store_static("710123456", name="PESCADOR I", ship_type=30)
    ais_store.store_static("636000001", name="CARGO", ship_type=70)
    ais_store.store_static("227000001", name="FRANCAIS", ship_type=30)
    for mmsi in ("710123456", "636000001", "227000001"):
        ais_store.store_positions([{"mmsi": mmsi, "ts": NOW.isoformat(), "lat": 5.5, "lon": -52.5}], zone_of=eez)
    rules.rule_intel(ais_store.vessels(1), get_settings())
    assert [c["url"].rsplit("/", 1)[1] for c in api] == ["710123456"]
    with db() as conn:
        row = dict(conn.execute("SELECT imo, call_sign, intel FROM ais_vessels WHERE mmsi = '710123456'").fetchone())
    assert row["imo"] == "9123456" and row["call_sign"] == "PP1"
    assert json.loads(row["intel"])["registry"]["vessel_subtype"] == "Trawler"


def test_profile_looks_up_on_demand(api: List[Dict[str, Any]], client_as: Any) -> None:
    ais_store.store_static("636000001", name="CARGO", ship_type=70)
    ais_store.store_positions([{"mmsi": "636000001", "ts": NOW.isoformat(), "lat": 5.5, "lon": -52.5}], zone_of=eez)
    viewer = client_as("viewer")
    profile = viewer.get("/api/v1/maritime/vessel/636000001").json()
    assert profile["intel"]["registry"]["name"] == "PESCADOR I"
    viewer.get("/api/v1/maritime/vessel/636000001")
    assert len(api) == 1  # stored in the vessel intelligence
    coverage = viewer.get("/api/v1/maritime/coverage").json()
    assert coverage["fusion"]["vesselapi"]["calls"] == 1 and coverage["ais"]["feeds"]["vesselapi"] is False


def test_catalogue_and_check(api: List[Dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    assert {s["id"]: s["status"] for s in catalogue.describe()}["vesselapi"] == "active"
    result = checks.check_service("vesselapi")
    assert result["ok"] is True and "120" in result["message"]
    monkeypatch.setattr(get_settings(), "vesselapi_api_key", "")
    assert {s["id"]: s["status"] for s in catalogue.describe()}["vesselapi"] == "needs_key"
