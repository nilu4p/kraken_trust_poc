"""Single reference instant: live and replay share one code path, and nothing after T leaks into T."""

from __future__ import annotations

import datetime
from typing import Any, Dict

import httpx
import numpy as np
import pytest
from fastapi import HTTPException

from app.config import get_settings
from app.core import timeframe
from app.core.c2pa import generate_and_seal_alert
from app.core.pki import ensure_pki
from app.db import db
from app.pipeline.orchestrator import _open_incidents
from app.soc import incidents
from app.sources import ais_store, cmems, gfw

NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def ago(hours: float) -> datetime.datetime:
    return NOW - datetime.timedelta(hours=hours)


def eez(_lon: float, _lat: float) -> str:
    return "eez"


def _fixes() -> None:
    ais_store.store_static("227000001", name="ALPHA", ship_type=30)
    ais_store.store_positions([
        {"mmsi": "227000001", "ts": ago(5).isoformat(), "lat": 5.0, "lon": -52.0, "sog": 4},
        {"mmsi": "227000001", "ts": ago(3).isoformat(), "lat": 5.1, "lon": -52.1, "sog": 4},
        {"mmsi": "227000001", "ts": ago(0.5).isoformat(), "lat": 5.9, "lon": -52.9, "sog": 4},
        {"mmsi": "710000002", "ts": ago(0.2).isoformat(), "lat": 6.0, "lon": -51.0, "sog": 2},
    ], zone_of=eez)


def test_resolve_live_replay_and_bounds() -> None:
    assert timeframe.resolve(None) is None and timeframe.resolve("") is None
    assert timeframe.resolve((NOW + datetime.timedelta(hours=2)).isoformat()) is None  # future = live
    assert timeframe.resolve(ago(3).isoformat()) == ago(3)
    assert timeframe.resolve(ago(3).strftime("%Y-%m-%dT%H:%M:%S")) == ago(3)  # naive = UTC
    with pytest.raises(HTTPException):
        timeframe.resolve("hier")
    with pytest.raises(HTTPException):
        timeframe.resolve((NOW - datetime.timedelta(days=get_settings().ais_retention_days + 1)).isoformat())


def test_ais_picture_at_uses_nothing_after_the_instant() -> None:
    _fixes()
    [vessel] = ais_store.picture_at(ago(2), hours=6, zone_of=eez)
    assert vessel["mmsi"] == "227000001" and vessel["last_lat"] == 5.1 and vessel["name"] == "ALPHA"
    assert vessel["last_zone"] == "eez"
    live = {v["mmsi"]: v for v in ais_store.picture_at(None, hours=2)}
    assert set(live) == {"227000001", "710000002"} and live["227000001"]["last_lat"] == 5.9
    track = ais_store.tracks(since_hours=6, until=ago(2))["227000001"]
    assert [p["lat"] for p in track] == [5.0, 5.1]


def test_ais_endpoint_replay(client_as: Any) -> None:
    _fixes()
    viewer = client_as("viewer")
    replay = viewer.get("/api/v1/maritime/ais", params={"at": ago(2).isoformat(), "hours": 6}).json()
    assert replay["time"]["mode"] == "replay"
    [feature] = replay["features"]
    assert feature["geometry"]["coordinates"] == [-52.1, 5.1] and len(feature["properties"]["track"]) == 2
    live = viewer.get("/api/v1/maritime/ais").json()
    assert live["time"]["mode"] == "live" and len(live["features"]) == 2
    assert viewer.get("/api/v1/maritime/ais", params={"at": "n'importe quoi"}).status_code == 422
    profile = viewer.get("/api/v1/maritime/vessel/227000001", params={"at": ago(2).isoformat()}).json()
    assert profile["vessel"]["last_lat"] == 5.1 and [p["lat"] for p in profile["track"]] == [5.0, 5.1]
    assert profile["intel_note"]
    assert viewer.get("/api/v1/maritime/vessel/710000002", params={"at": ago(2).isoformat()}).status_code == 404


def _incident() -> Dict[str, Any]:
    cfg = get_settings()
    ensure_pki(cfg)
    vessel = {"id": "SAR-001", "lat": 5.5, "lon": -52.5, "confidence": 0.9, "zone": "eez",
              "risk": {"score": 80, "level": "critical", "reasons": []},
              "quality": {"score": 0.8, "level": "reliable", "flags": []},
              "chip": np.random.default_rng(1).random((96, 96), dtype=np.float32)}
    path = generate_and_seal_alert([vessel], stac_source="S1A_T", settings=cfg)
    _open_incidents([vessel], path, ago(12).isoformat(), "S1A_T", cfg)
    [incident] = incidents.list_incidents()
    return incident


def test_incident_state_is_replayed_from_its_event_log(make_user: Any) -> None:
    incident = _incident()
    analyst = make_user("ana", "analyst")
    incidents.transition(incident["id"], "triage", analyst)
    with db() as conn:  # place the history in the past: created 10 h ago, triaged 5 h ago
        conn.execute("UPDATE incidents SET created_at = ? WHERE id = ?", (ago(10).isoformat(), incident["id"]))
        conn.execute("UPDATE incident_events SET at = ? WHERE incident_id = ? AND kind != 'status'",
                     (ago(10).isoformat(), incident["id"]))
        conn.execute("UPDATE incident_events SET at = ? WHERE incident_id = ? AND kind = 'status'",
                     (ago(5).isoformat(), incident["id"]))
    assert incident["id"] not in incidents.state_at(ago(11))
    assert incidents.state_at(ago(7))[incident["id"]]["status"] == "new"
    assert incidents.state_at(ago(3))[incident["id"]]["status"] == "triage"


def test_alert_feed_only_shows_what_was_known(client_as: Any) -> None:
    _incident()
    viewer = client_as("viewer")
    radar = lambda data: [f for f in data["features"] if f["properties"].get("type") == "dark_vessel"]  # noqa: E731
    assert len(radar(viewer.get("/api/v1/alerts").json())) == 1
    # Sealed just now: one hour ago the platform did not know this target yet.
    before = viewer.get("/api/v1/alerts", params={"at": ago(1).isoformat()}).json()
    assert radar(before) == [] and before["time"]["mode"] == "replay"


def test_gfw_events_after_the_instant_are_dropped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gfw_token", "t")
    entries = [
        {"id": "a", "start": ago(30).isoformat(), "position": {"lat": 5, "lon": -52}, "vessel": {}},
        {"id": "b", "start": ago(1).isoformat(), "position": {"lat": 5, "lon": -52}, "vessel": {}},
    ]
    monkeypatch.setattr(gfw, "post_json", lambda *_a, **_kw: {"entries": entries})
    live = gfw.fetch_events()
    replay = gfw.fetch_events(end=ago(2))
    assert {f["properties"]["event_id"] for f in live["features"]} == {"a", "b"}
    assert {f["properties"]["event_id"] for f in replay["features"]} == {"a"}
    assert sum(replay["counts"].values()) == len(replay["features"])


def test_ocean_is_read_at_the_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_get(url: str, params: Dict[str, Any] = None, **_kw: Any) -> httpx.Response:
        calls.append(dict(params or {}))
        value = 2.0 if "TIME" not in (params or {}) else 0.9
        return httpx.Response(200, request=httpx.Request("GET", url),
                              json={"features": [{"properties": {"value": value, "units": "mg m-3"}}]})

    monkeypatch.setattr(cmems.httpx, "get", fake_get)
    past = datetime.datetime(2026, 10, 15, 14, 20, tzinfo=datetime.timezone.utc)
    assert cmems.ocean_point(5.5, -52.5, at=past)["chlorophyll_mg_m3"] == 0.9
    assert {c.get("TIME") for c in calls if "CHL" in c["LAYER"]} == {"2026-10-15T00:00:00Z"}
    assert {c.get("TIME") for c in calls if "velocity" in c["LAYER"]} == {"2026-10-15T12:00:00Z"}
    assert cmems.ocean_point(5.5, -52.5)["chlorophyll_mg_m3"] == 2.0  # latest, separate cache entry
    assert "&TIME=2026-10-15T00:00:00Z" in cmems.tile_layers(past)["sst"]["url"]
    assert "TIME" not in cmems.tile_layers()["sst"]["url"]


def test_timeline_buckets(client_as: Any) -> None:
    _fixes()
    _incident()
    viewer = client_as("viewer")
    data = viewer.get("/api/v1/timeline", params={"start": ago(14).isoformat()}).json()  # radar pass 12 h ago
    assert data["bucket"] == "hour"
    assert sum(b["vessels"] for b in data["buckets"]) >= 3 and sum(b["incidents_opened"] for b in data["buckets"]) == 1
    assert sum(b["radar"] for b in data["buckets"]) == 1
    assert data["history"]["retention_days"] == get_settings().ais_retention_days
    week = viewer.get("/api/v1/timeline", params={"start": (NOW - datetime.timedelta(days=30)).isoformat()}).json()
    assert week["bucket"] == "day"
    assert viewer.get("/api/v1/timeline", params={"start": "x"}).status_code == 422


def test_cached_dossiers_do_not_leak_replay_state(client_as: Any) -> None:
    """The parsed dossier is cached: a replay's annotations must not stick to the live feed."""
    _incident()
    viewer = client_as("viewer")
    radar = lambda data: [f for f in data["features"] if f["properties"].get("type") == "dark_vessel"]  # noqa: E731
    live_before = radar(viewer.get("/api/v1/alerts").json())
    assert live_before[0]["properties"]["incident_status"] == "new"
    assert radar(viewer.get("/api/v1/alerts", params={"at": ago(1).isoformat()}).json()) == []
    live_after = radar(viewer.get("/api/v1/alerts").json())
    assert live_after == live_before


def test_timeline_cache_serves_stable_periods_and_is_invalidated(client_as: Any) -> None:
    viewer = client_as("viewer")
    params = {"start": (NOW - datetime.timedelta(days=3)).isoformat(), "bucket": "hour"}
    first = viewer.get("/api/v1/timeline", params=params).json()
    second = viewer.get("/api/v1/timeline", params=params).json()
    assert first["cached_buckets"] == 0 and second["cached_buckets"] > 60  # hours older than 6 h now cached
    assert first["buckets"] == second["buckets"]
    # A late VMS position two days ago must show up despite the cache.
    two_days = NOW - datetime.timedelta(days=2)
    ais_store.store_positions([{"mmsi": "VMS-YYV1", "ts": two_days.isoformat(), "lat": 5.5, "lon": -52.5, "source": "vms"}],
                              zone_of=eez)
    third = viewer.get("/api/v1/timeline", params=params).json()
    hour = two_days.strftime("%Y-%m-%dT%H")
    assert next(b for b in third["buckets"] if b["t"] == hour)["vms"] == 1
