"""Performance & robustness: non-blocking reads, consistent backups, scheduler, payload size, precision."""

from __future__ import annotations

import datetime
import sqlite3
import tarfile
import threading
import time
from typing import Any, List

import pytest

from app.config import get_settings
from tests.conftest import sqlite_only
from app.core import maintenance
from app.db import db, read_db
from app.pipeline.risk import score_vessel
from app.soc import incidents, rules, scheduler
from app.sources import ais_store

NOW = datetime.datetime.now(datetime.timezone.utc)


@pytest.fixture(autouse=True)
def quiet_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.soc.notify.publish", lambda *_a, **_kw: True)


@sqlite_only
def test_reads_do_not_wait_for_writers() -> None:
    with db() as conn:
        conn.execute("SELECT 1")
    started, release = threading.Event(), threading.Event()

    def writer() -> None:
        with db() as conn:  # holds the write lock until released
            conn.execute("INSERT INTO rule_state (key, value, updated_at) VALUES ('x', '1', 'now')")
            started.set()
            release.wait(10)

    thread = threading.Thread(target=writer)
    thread.start()
    assert started.wait(5)
    try:
        begin = time.monotonic()
        with read_db() as conn:
            conn.execute("SELECT COUNT(*) FROM incidents").fetchone()
        assert time.monotonic() - begin < 0.5
        # A write-locking transaction would wait here for the writer (busy timeout).
        with pytest.raises(sqlite3.OperationalError):
            blocked = sqlite3.connect(str(get_settings().data_dir / "kraken.db"), timeout=0.2)
            blocked.execute("BEGIN IMMEDIATE")
    finally:
        release.set()
        thread.join()


def test_read_db_refuses_writes() -> None:
    with pytest.raises(Exception, match="(?i)read.?only|readonly"):
        with read_db() as conn:
            conn.execute("INSERT INTO rule_state (key, value, updated_at) VALUES ('y', '1', 'now')")


@sqlite_only
def test_backup_contains_a_consistent_sqlite_snapshot(tmp_path: Any) -> None:
    incidents.ingest_detection({"id": "SAR-001", "lat": 5.0, "lon": -52.0, "risk": {"score": 80}},
                               alert_id="a", dossier="", scene_id="S", seen_at=NOW.isoformat())
    path, _digest = maintenance.create_backup(label="test")
    with tarfile.open(path) as archive:
        names = archive.getnames()
        assert "data/kraken.db" in names and not any(n.endswith(("-wal", "-shm")) for n in names)
        archive.extract("data/kraken.db", tmp_path, filter="data")
    conn = sqlite3.connect(tmp_path / "data" / "kraken.db")
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert conn.execute("SELECT COUNT(*) FROM incidents").fetchone()[0] == 1


def test_auto_scan_skips_permanent_failures_and_abandons_transient_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline import orchestrator

    def outside_eez(_cfg: Any, scene_id: str, _actor: str) -> None:
        orchestrator._mark_processed(scene_id, "skipped:no_tile", get_settings())

    monkeypatch.setattr("app.pipeline.orchestrator.run_kraken_pipeline", outside_eez)
    scheduler.auto_scan("SCENE_OUTSIDE")
    with read_db() as conn:
        assert conn.execute("SELECT outcome FROM processed_scenes WHERE scene_id = 'SCENE_OUTSIDE'").fetchone()[0] == "skipped:no_tile"

    monkeypatch.setattr("app.pipeline.orchestrator.run_kraken_pipeline", lambda *_a: None)  # network failure: not marked
    for attempt in range(scheduler.MAX_SCENE_ATTEMPTS):
        with read_db() as conn:
            assert conn.execute("SELECT 1 FROM processed_scenes WHERE scene_id = 'SCENE_FLAKY'").fetchone() is None
        scheduler.auto_scan("SCENE_FLAKY")
    with read_db() as conn:
        assert conn.execute("SELECT outcome FROM processed_scenes WHERE scene_id = 'SCENE_FLAKY'").fetchone()[0] == "abandoned"


def test_ais_tracks_are_thinned_for_the_map(client_as: Any) -> None:
    points = [{"mmsi": "227000001", "ts": (NOW - datetime.timedelta(minutes=m)).isoformat(), "lat": 5.0 + m * 1e-3,
               "lon": -52.0, "sog": 5, "cog": 0} for m in range(360, 0, -1)]
    ais_store.store_positions(points, zone_of=lambda *_: "eez")
    feature = client_as("viewer").get("/api/v1/maritime/ais").json()["features"][0]
    assert 70 <= len(feature["properties"]["track"]) <= 75  # 6 h at one point every 5 min (+ last fix)


def test_intel_lookups_stop_when_the_time_budget_is_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = get_settings()
    monkeypatch.setattr(cfg, "gfw_token", "token")
    calls: List[str] = []
    monkeypatch.setattr("app.sources.gfw_4wings.vessel_identity", lambda mmsi, _cfg=None: calls.append(mmsi) or {})
    monkeypatch.setattr(rules, "INTEL_TIME_BUDGET_S", -1.0)
    ais_store.store_positions([{"mmsi": "351000001", "ts": NOW.isoformat(), "lat": 6.0, "lon": -52.0}],
                              zone_of=lambda *_: "eez")
    rules.rule_intel(ais_store.vessels(1), cfg)
    assert calls == []


def test_turbid_coastal_chlorophyll_and_tiny_blobs_do_not_score() -> None:
    target = {"lat": 4.69, "lon": -51.81, "confidence": 0.53, "length_m": 28.3, "pixels": 3,
              "ocean": {"productive_waters": True, "chlorophyll_mg_m3": 11.1, "depth_m": 6}}
    risk = score_vessel(target, zone="territorial", events=[], ais_source="aisstream.io (historique)")
    assert not any("Cible moyenne" in r or "Eaux productives" in r for r in risk["reasons"])
    assert any("non interprétée" in r for r in risk["reasons"])
    offshore = score_vessel({**target, "pixels": 12, "ocean": {**target["ocean"], "depth_m": 80}}, zone="eez", events=[],
                            ais_source="aisstream.io (historique)")
    assert any("Cible moyenne" in r for r in offshore["reasons"]) and any("Eaux productives" in r for r in offshore["reasons"])


def test_incident_title_does_not_claim_missing_ais_when_unknown() -> None:
    incident, _ = incidents.ingest_detection(
        {"id": "SAR-002", "lat": 4.69, "lon": -51.81, "zone": "territorial", "length_m": 28, "pixels": 3,
         "ais_source": "unavailable", "risk": {"score": 36}},
        alert_id="b", dossier="", scene_id="S2", seen_at=NOW.isoformat(),
    )
    assert incident["title"] == "Écho radar non identifié en mer territoriale"


def test_bathymetry_comes_from_a_cached_eez_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    from app.sources import erddap

    calls: List[str] = []
    rows = "\n".join(f"{lat},{lon},{-10 * (i + j)}" for i, lat in enumerate((5.0, 5.5)) for j, lon in enumerate((-52.0, -51.5)))

    def fake_get(url: str, **_kw: Any) -> httpx.Response:
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("GET", url), text=rows)

    monkeypatch.setattr(erddap.httpx, "get", fake_get)
    monkeypatch.setattr(erddap, "_grid", {})
    assert erddap.depth_m(5.47, -51.53) == -20.0  # nearest cell
    assert erddap.depth_m(5.0, -52.0) == 0.0
    assert len(calls) == 1 and "upwell" in calls[0]  # one download for the whole EEZ, fastest mirror first
    monkeypatch.setattr(erddap, "_grid", {})
    assert erddap.depth_m(5.5, -52.0) == -10.0 and len(calls) == 1  # reloaded from the disk cache
