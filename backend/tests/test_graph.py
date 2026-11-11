"""Entity graph over time: links with times and evidence, nothing after the instant."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import numpy as np
import pytest

from app.config import get_settings
from app.core.c2pa import generate_and_seal_alert
from app.core.pki import ensure_pki
from app.pipeline.orchestrator import _open_incidents
from app.soc import graph, rules, watchlist
from app.sources import ais_store

NOW = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)


def ago(minutes: float) -> datetime.datetime:
    return NOW - datetime.timedelta(minutes=minutes)


def eez(_lon: float, _lat: float) -> str:
    return "eez"


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.soc.notify.publish", lambda *_a, **_kw: True)


def _rendezvous() -> None:
    """Brazilian fisher A and Liberian cargo B side by side for 70 min; Guyanese C far away."""
    ais_store.store_static("710000001", name="PESCADOR A", ship_type=30)
    ais_store.store_static("636000002", name="CARGO B", ship_type=70)
    ais_store.store_static("745000003", name="LOIN C", ship_type=30)
    fixes = []
    for minutes in (80, 70, 60, 50, 40, 30, 20, 10):
        fixes += [
            {"mmsi": "710000001", "ts": ago(minutes).isoformat(), "lat": 6.0, "lon": -52.0, "sog": 0.5, "cog": 0},
            {"mmsi": "636000002", "ts": ago(minutes).isoformat(), "lat": 6.002, "lon": -52.001, "sog": 0.4, "cog": 0},
            {"mmsi": "745000003", "ts": ago(minutes).isoformat(), "lat": 7.5, "lon": -51.0, "sog": 8, "cog": 0},
        ]
    ais_store.store_positions(fixes, zone_of=eez)


def _ids(data: Dict[str, Any], kind: str) -> List[str]:
    return sorted(n["id"] for n in data["nodes"] if n["type"] == kind)


def _edge(data: Dict[str, Any], a: str, b: str, kind: str) -> Dict[str, Any]:
    return next(e for e in data["edges"] if {e["source"], e["target"]} == {a, b} and e["type"] == kind)


def test_vessel_neighbourhood_with_times_and_evidence() -> None:
    _rendezvous()
    rules.run_rules()  # foreign fishing on A + encounter A/B
    data = graph.build(["vessel:710000001"], days=1)
    assert "vessel:636000002" in _ids(data, "vessel") and "vessel:745000003" not in _ids(data, "vessel")
    near = _edge(data, "vessel:710000001", "vessel:636000002", "proximity")
    assert near["minutes"] >= 40 and near["closest_m"] < 300 and near["first"] < near["last"]
    assert "à moins de 1 km" in near["evidence"][0]
    kinds = {n["kind"] for n in data["nodes"] if n["type"] == "incident"}
    assert kinds == {"foreign_fishing", "encounter"}
    focus = next(n for n in data["nodes"] if n["id"] == "vessel:710000001")
    assert focus["focus"] and focus["flag"] == "Brésil" and focus["fishing"]


def test_encounter_incident_links_both_vessels() -> None:
    _rendezvous()
    rules.run_rules()
    encounter = next(n for n in graph.build(["vessel:710000001"], days=1)["nodes"] if n.get("kind") == "encounter")
    data = graph.build([encounter["id"]], days=1)
    assert {"vessel:710000001", "vessel:636000002"} <= set(_ids(data, "vessel"))
    # The cargo (not the incident's main vessel) still leads back to the encounter.
    assert encounter["id"] in _ids(graph.build(["vessel:636000002"], days=1), "incident")


def test_replay_shows_nothing_after_the_instant() -> None:
    _rendezvous()
    rules.run_rules()
    before = graph.build(["vessel:710000001"], end=ago(75), days=1)
    assert _ids(before, "incident") == []  # incidents opened just now did not exist yet
    assert not [e for e in before["edges"] if e["type"] == "proximity"]  # one shared 10-min slot: not yet a link
    assert before["window"]["end"] == ago(75).isoformat()
    later = graph.build(["vessel:710000001"], end=ago(55), days=1)
    assert [e for e in later["edges"] if e["type"] == "proximity"]  # three shared slots by then


def test_radar_detection_neighbours_and_watchlist() -> None:
    _rendezvous()
    watchlist.add_entry({"mmsi": "636000002", "reason": "ICCAT IUU"}, actor="t")
    cfg = get_settings()
    ensure_pki(cfg)
    echo = {"id": "SAR-001", "lat": 6.03, "lon": -52.0, "confidence": 0.9, "zone": "eez",
            "risk": {"score": 80, "level": "critical", "reasons": []}, "quality": {"score": 0.8, "flags": []},
            "chip": np.random.default_rng(1).random((96, 96), dtype=np.float32)}
    path = generate_and_seal_alert([echo], stac_source="S1A_G", settings=cfg)
    _open_incidents([echo], path, ago(30).isoformat(), "S1A_G", cfg)
    detection = f"detection:{path.parent.name.rsplit('_', 1)[-1]}:SAR-001"
    data = graph.build([detection], days=1)
    neighbours = {e["target"] if e["source"] == detection else e["source"]: e
                  for e in data["edges"] if e["type"] == "radar_neighbour"}
    assert set(neighbours) == {"vessel:710000001", "vessel:636000002"}  # C is 170 km away
    assert neighbours["vessel:710000001"]["distance_km"] < 5
    assert _ids(data, "incident")
    deep = graph.build([detection], days=1, depth=2)
    assert _ids(deep, "watch") and _edge(deep, "vessel:636000002", _ids(deep, "watch")[0], "listed")


def test_search_and_api(client_as: Any) -> None:
    _rendezvous()
    rules.run_rules()
    found = graph.search("pescador")
    assert found[0]["id"] == "vessel:710000001" and all(r["type"] in ("vessel", "incident") for r in found)
    assert any(r["type"] == "incident" for r in graph.search("KRK-"))
    viewer = client_as("viewer")
    data = viewer.get("/api/v1/graph", params={"focus": "vessel:710000001", "days": 1}).json()
    assert data["time"]["mode"] == "live" and data["nodes"]
    assert viewer.get("/api/v1/graph", params={"focus": "n'importe quoi"}).status_code == 422
    assert viewer.get("/api/v1/graph/search", params={"q": "CARGO"}).json()["results"][0]["id"] == "vessel:636000002"
