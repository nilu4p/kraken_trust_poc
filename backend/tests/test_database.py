"""Database engines: SQLite -> PostgreSQL migration, portable dumps, PostgreSQL specifics."""

from __future__ import annotations

import datetime
import threading
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings, get_settings
from app.core import audit, dbtransfer
from app.db import Row, db, init_db, read_db, to_postgres
from app.soc import incidents
from tests.conftest import postgres_only

NOW = datetime.datetime.now(datetime.timezone.utc)


def test_sql_adaptation_for_postgres() -> None:
    assert to_postgres("SELECT * FROM t WHERE a = ? AND b LIKE ?") == "SELECT * FROM t WHERE a = %s AND b ILIKE %s"
    assert to_postgres("SELECT '?' , x FROM t WHERE y = ?") == "SELECT '?' , x FROM t WHERE y = %s"
    assert to_postgres("SELECT 100 % 7") == "SELECT 100 %% 7"
    row = Row(["id", "name"], [1, "a"])
    assert row[0] == 1 and row["name"] == "a" and dict(row) == {"id": 1, "name": "a"}


def _sqlite_instance(tmp_path: Path) -> Settings:
    """A populated SQLite instance (as before the PostgreSQL upgrade)."""
    cfg = Settings(database_url="", data_dir=tmp_path / "data", keys_dir=tmp_path / "keys")
    init_db(cfg)
    from app.core import identity

    identity.create_user("ancien", "Xq7!vTr9#mLp2zW", "analyst", must_change_password=False, settings=cfg)
    incidents.ingest_detection({"id": "SAR-001", "lat": 5.0, "lon": -52.0, "risk": {"score": 80}},
                               alert_id="a", dossier="", scene_id="S", seen_at=NOW.isoformat(), settings=cfg)
    with db(cfg) as conn:
        conn.execute("INSERT INTO ais_positions (mmsi, ts, lat, lon, source) VALUES (?, ?, ?, ?, ?)",
                     ("227000001", NOW.isoformat(), 5.0, -52.0, "aisstream"))
    audit.record("test.event", actor="t", settings=cfg)
    return cfg


@postgres_only
def test_first_start_on_postgres_migrates_the_sqlite_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old = _sqlite_instance(tmp_path)
    monkeypatch.setattr(get_settings(), "data_dir", old.data_dir)
    counts = dbtransfer.migrate_sqlite_if_needed()
    assert counts["users"] == 1 and counts["incidents"] == 1 and counts["ais_positions"] == 1
    assert not (old.data_dir / "kraken.db").exists() and list(old.data_dir.glob("kraken.db.migrated-*"))
    with read_db() as conn:
        assert conn.execute("SELECT username FROM users").fetchone()[0] == "ancien"
        assert conn.execute("SELECT ref FROM incidents").fetchone()[0] == "KRK-%s-0001" % NOW.year
    assert audit.verify_chain()["valid"]  # migrated chain + the migration entry
    # Ids continue after the migrated ones.
    new, _ = incidents.ingest_detection({"id": "SAR-009", "lat": 7.0, "lon": -50.0, "risk": {"score": 20}},
                                        alert_id="b", dossier="", scene_id="S2", seen_at=NOW.isoformat())
    assert new["id"] == 2
    assert dbtransfer.migrate_sqlite_if_needed() is None  # runs once


def test_logical_dump_restores_on_any_engine(tmp_path: Path) -> None:
    incidents.ingest_detection({"id": "SAR-001", "lat": 5.0, "lon": -52.0, "risk": {"score": 80}},
                               alert_id="a", dossier="", scene_id="S", seen_at=NOW.isoformat())
    audit.record("test.event", actor="t")
    dbtransfer.write_jsonl(tmp_path / "dump")
    target = Settings(database_url="", data_dir=tmp_path / "other")  # always SQLite
    counts = dbtransfer.load_jsonl(tmp_path / "dump", target)
    assert counts["incidents"] == 1 and counts["audit_log"] >= 1
    assert audit.verify_chain(target)["valid"]


@postgres_only
def test_audit_chain_never_forks_under_concurrency() -> None:
    def burst(n: int) -> None:
        for i in range(10):
            audit.record("test.concurrent", actor=f"t{n}", details={"i": i})

    threads = [threading.Thread(target=burst, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    result = audit.verify_chain()
    assert result["valid"] and result["entries"] == 60


@postgres_only
def test_timestamps_round_trip_as_iso_strings() -> None:
    from app.sources import ais_store

    ais_store.store_positions([{"mmsi": "227000001", "ts": NOW.isoformat(), "lat": 5.0, "lon": -52.0}],
                              zone_of=lambda *_: "eez")
    point = ais_store.tracks(1)["227000001"][0]
    assert isinstance(point["ts"], str) and point["ts"].startswith(NOW.strftime("%Y-%m-%dT%H:%M"))
    assert ais_store.positions_at(NOW.isoformat(), 1)[0]["mmsi"] == "227000001"
