"""Data integrity of the vessel history: one timestamp format, implausible fixes refused,
and a collection log that tells "no vessel" from "no data"."""

from __future__ import annotations

import datetime
from typing import Any, Dict

import pytest

from app.config import get_settings
from app.db import db
from app.pipeline.detection import load_ais_tracks
from app.sources import ais_store, aisstream, feed_log

NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


def ago(minutes: float) -> datetime.datetime:
    return NOW - datetime.timedelta(minutes=minutes)


def eez(_lon: float, _lat: float) -> str:
    return "eez"


def fix(mmsi: str, when: Any, lat: float = 5.5, lon: float = -52.5) -> Dict[str, Any]:
    return {"mmsi": mmsi, "ts": when, "lat": lat, "lon": lon, "sog": 6.0, "cog": 90.0}


def test_aisstream_timestamps_are_stored_as_iso_and_found_by_windows() -> None:
    raw = ago(20).strftime("%Y-%m-%d %H:%M:%S") + ".123456789 +0000 UTC"  # aisstream MetaData.time_utc
    parsed = aisstream.parse_message({
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 227000001, "latitude": 5.5, "longitude": -52.5, "time_utc": raw},
        "Message": {"PositionReport": {"Sog": 6, "Cog": 90}},
    })
    assert ais_store.store_positions([parsed], zone_of=eez) == 1
    with db() as conn:
        stored = conn.execute("SELECT ts FROM ais_positions").fetchone()[0]
        last_seen = conn.execute("SELECT last_seen FROM ais_vessels").fetchone()[0]
    assert "T" in str(stored) and str(last_seen).startswith(ago(20).strftime("%Y-%m-%dT%H:%M"))
    # The same-day fix is inside every "last N hours" window (it was not with the raw format).
    assert [v["mmsi"] for v in ais_store.vessels(1)] == ["227000001"]
    assert ais_store.tracks(1)["227000001"] and ais_store.picture_at(None, 1)
    assert ais_store.stats()["positions_1h"] == 1


def test_implausible_fixes_are_refused() -> None:
    ais_store.store_positions([fix("227000002", ago(30).isoformat())], zone_of=eez)
    refused = [
        fix("227000002", (NOW + datetime.timedelta(hours=2)).isoformat()),           # clock in the future
        fix("227000002", (NOW - datetime.timedelta(days=400)).isoformat()),          # beyond the history
        fix("227000002", ago(25).isoformat(), lat=6.5),                              # 110 km in 5 min
        fix("227000002", ago(24).isoformat(), lat=0, lon=0),                         # null island
        fix("227000002", "pas une date"),
    ]
    assert ais_store.store_positions(refused, zone_of=eez) == 0
    assert ais_store.store_positions([fix("227000002", ago(20).isoformat(), lat=5.51)], zone_of=eez) == 1  # 1 km / 10 min
    reasons = ais_store.stats()["rejected"]["aisstream"]
    assert reasons["saut de position impossible"] == 1 and reasons["horodatage dans le futur"] == 1
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ais_positions WHERE mmsi = '227000002'").fetchone()[0] == 2
        assert conn.execute("SELECT last_lat FROM ais_vessels WHERE mmsi = '227000002'").fetchone()[0] == 5.51


def test_collection_log_coverage_and_gaps() -> None:
    assert feed_log.coverage(ago(60), NOW) is None  # never logged: unknown, not zero
    feed_log.mark_span("aisstream", ago(60), ago(40), messages=12)
    feed_log.mark_span("aishub", ago(20), NOW, messages=3)
    cov = feed_log.coverage(ago(60), NOW)
    assert 0.6 < cov < 0.8
    [gap] = feed_log.gaps(ago(60), NOW, min_minutes=15)
    # The last minute of a poll window is observed: the gap starts the minute after.
    assert gap["start"].startswith(ago(39).strftime("%Y-%m-%dT%H:%M")) and gap["end"].startswith(ago(20).strftime("%Y-%m-%dT%H:%M"))
    with db() as conn:
        assert conn.execute("SELECT SUM(messages) FROM feed_minutes").fetchone()[0] == 15


def test_radar_pass_during_collection_gap_is_not_called_without_ais(monkeypatch: pytest.MonkeyPatch) -> None:
    """An echo is only 'without AIS' if the AIS feeds were listening at the pass."""
    monkeypatch.setattr(get_settings(), "aisstream_api_key", "k")
    ais_store.store_positions([fix("227000003", ago(170).isoformat())], zone_of=eez)
    feed_log.mark_span("aisstream", ago(240), ago(160))  # collector up, then down for the last 160 min
    _tracks, source = load_ais_tracks(get_settings(), ago(60).isoformat())
    assert source == "unavailable"
    feed_log.mark_span("aisstream", ago(160), NOW)
    _tracks, source = load_ais_tracks(get_settings(), ago(60).isoformat())
    assert source != "unavailable"
