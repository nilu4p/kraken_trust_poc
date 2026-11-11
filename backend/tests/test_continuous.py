"""Continuous surveillance: AIS collector parsing, AIS history, 24/7 rules, GFW 4Wings integration."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import httpx
import pytest

from app.config import get_settings
from app.db import db
from app.pipeline.detection import correlate_with_ais, load_ais_tracks
from app.pipeline.orchestrator import _apply_gfw_sar
from app.pipeline.quality import assess
from app.pipeline.risk import score_vessel
from app.soc import incidents, rules, watchlist
from app.sources import ais_store, gfw_4wings
from app.sources.aisstream import parse_message

NOW = datetime.datetime.now(datetime.timezone.utc)


def iso(minutes_ago: float) -> str:
    return (NOW - datetime.timedelta(minutes=minutes_ago)).isoformat()


def eez(_lon: float, _lat: float) -> str:
    return "eez"


def coastal(_lon: float, _lat: float) -> str:
    return "territorial"


def fix(mmsi: str, minutes_ago: float, lat: float = 5.5, lon: float = -52.5, sog: float = 8.0, cog: float = 90.0) -> Dict[str, Any]:
    return {"mmsi": mmsi, "ts": iso(minutes_ago), "lat": lat, "lon": lon, "sog": sog, "cog": cog}


@pytest.fixture(autouse=True)
def quiet_notifications(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    sent: List[str] = []
    monkeypatch.setattr("app.soc.notify.publish", lambda channel, text, **_kw: sent.append(f"{channel}|{text}") or True)
    return sent


def test_parse_class_a_class_b_and_static_messages() -> None:
    class_b = {
        "MessageType": "StandardClassBPositionReport",
        "MetaData": {"MMSI": 710123456, "ShipName": "PESCADOR ", "latitude": 5.4, "longitude": -51.9, "time_utc": "2026-10-20 10:00:00.1 +0000 UTC"},
        "Message": {"StandardClassBPositionReport": {"Sog": 3.2, "Cog": 400, "TrueHeading": 511}},
    }
    parsed = parse_message(class_b)
    assert parsed["kind"] == "position" and parsed["mmsi"] == "710123456" and parsed["cog"] is None and parsed["heading"] is None
    static = parse_message({"MessageType": "ShipStaticData", "MetaData": {"MMSI": 710123456},
                            "Message": {"ShipStaticData": {"Name": "PESCADOR", "ImoNumber": 0, "CallSign": "PP1", "Type": 30,
                                                           "Dimension": {"A": 18, "B": 6, "C": 3, "D": 4}}}})
    assert static == {"kind": "static", "mmsi": "710123456", "name": "PESCADOR", "imo": 0, "call_sign": "PP1", "ship_type": 30,
                      "length_m": 24.0}
    report_b = parse_message({"MessageType": "StaticDataReport", "MetaData": {"MMSI": 765000001},
                              "Message": {"StaticDataReport": {"ReportB": {"ShipType": 30, "CallSign": "SR"}}}})
    assert report_b["ship_type"] == 30
    assert parse_message({"MessageType": "PositionReport", "MetaData": {}, "Message": {}}) is None


def test_history_correlates_at_pass_time_with_dead_reckoning() -> None:
    # Vessel heading east at 10 kn, fix 1 h before the pass at -52.6 → ~18.5 km east at pass time.
    pass_time = NOW - datetime.timedelta(hours=2)
    ais_store.store_positions([{"mmsi": "227000001", "ts": (pass_time - datetime.timedelta(hours=1)).isoformat(),
                                "lat": 5.5, "lon": -52.6, "sog": 10.0, "cog": 90.0}])
    tracks, source = load_ais_tracks(get_settings(), pass_time.isoformat())
    assert source == "aisstream.io (historique)" and tracks[0]["fix_age_h"] == pytest.approx(1.0, abs=0.01)
    assert tracks[0]["lon"] == pytest.approx(-52.6 + 18.52 / (111.32 * 0.9954), abs=0.01)
    radar = [{"id": "A", "lat": 5.5, "lon": tracks[0]["lon"] + 0.005, "confidence": 0.9}]
    dark, _ = correlate_with_ais(radar, get_settings(), ais_tracks=tracks, scene_time=pass_time.isoformat())
    assert dark == []  # explained by the projected AIS position


def test_foreign_fishing_rule_per_vessel(quiet_notifications: List[str]) -> None:
    ais_store.store_static("710123456", name="PESCADOR", ship_type=30)
    ais_store.store_static("710999999", name="OUTRO", ship_type=30)
    ais_store.store_static("745123456", name="GUYANAIS", ship_type=30)  # French Guiana: no alert
    ais_store.store_positions([fix("710123456", 5), fix("710999999", 4, lat=5.51), fix("745123456", 3)], zone_of=eez)
    result = rules.run_rules()
    assert result["foreign_fishing"] == 2
    again = rules.run_rules()
    assert again["foreign_fishing"] == 0  # same vessels, same day: no duplicates
    titles = sorted(i["title"] for i in incidents.list_incidents())
    assert all("Brésil" in t for t in titles) and len(titles) == 2  # two neighbours → two incidents
    assert any(m.startswith("alertes|") for m in quiet_notifications)


def test_watchlist_rule_is_continuous() -> None:
    watchlist.add_entry({"mmsi": "636012345", "reason": "ICCAT IUU"}, actor="t")
    ais_store.store_positions([fix("636012345", 2)], zone_of=eez)
    assert rules.run_rules()["watchlist_ais"] == 1
    [incident] = incidents.list_incidents()
    assert incident["kind"] == "watchlist_ais" and incident["severity"] == "P1"


def test_ais_gap_rule_requires_regular_emission_and_live_receiver() -> None:
    gapper = [fix("227000002", m) for m in range(300, 659, 60)]  # 6 fixes, last one 5 h ago
    ais_store.store_positions(gapper, zone_of=coastal)
    assert rules.rule_gaps(ais_store.vessels(48), get_settings()) == 0  # receiver silent → no conclusion
    ais_store.store_positions([fix("227000003", 1, lat=5.2), fix("227000004", 2, lat=5.3)], zone_of=coastal)
    assert rules.rule_gaps(ais_store.vessels(48), get_settings()) == 1
    assert incidents.list_incidents()[0]["kind"] == "ais_gap"


def test_encounter_rule() -> None:
    points = []
    for minutes in range(5, 65, 10):  # 6 ten-minute buckets side by side, slow
        points += [fix("710000011", minutes, sog=0.5), fix("273000022", minutes, lat=5.5015, sog=0.8)]
    ais_store.store_static("710000011", name="PESCA", ship_type=30)
    ais_store.store_positions(points, zone_of=eez)
    assert rules.rule_encounters(get_settings()) == 1
    incident = incidents.list_incidents()[0]
    assert incident["kind"] == "encounter" and "Rendez-vous" in incident["title"] and incident["severity"] == "P2"


def test_encounter_at_anchorage_is_ignored() -> None:
    points = []
    for minutes in range(5, 125, 10):  # two hours side by side off the Îles du Salut
        points += [fix("710000011", minutes, lat=5.30, lon=-52.60, sog=0.2), fix("273000022", minutes, lat=5.3015, lon=-52.60, sog=0.1)]
    ais_store.store_static("710000011", name="PESCA", ship_type=30)
    ais_store.store_positions(points, zone_of=eez)
    assert rules.rule_encounters(get_settings()) == 0


def test_purge_respects_retention(monkeypatch: pytest.MonkeyPatch) -> None:
    ais_store.store_positions([fix("227000005", 3 * 24 * 60), fix("227000005", 10)], zone_of=eez)
    monkeypatch.setattr(get_settings(), "ais_retention_days", 1)
    assert ais_store.purge() == 1
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ais_positions").fetchone()[0] == 1


def test_gfw_4wings_parsing_and_cross_validation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gfw_token", "token")
    payload = {"entries": [{"public-global-sar-presence:v3.0": [
        {"lat": 5.5, "lon": -52.5, "date": "2026-10-19", "detections": 1, "vesselId": None},
        {"lat": 5.52, "lon": -52.48, "date": "2026-10-10", "detections": 2},
        {"lat": 6.5, "lon": -51.0, "date": "2026-10-19", "detections": 1, "vesselId": "abc", "mmsi": "710000001"},
    ]}]}
    monkeypatch.setattr(gfw_4wings.httpx, "get", lambda url, **_kw: httpx.Response(200, request=httpx.Request("GET", url), json=payload))
    sar = gfw_4wings.sar_detections()
    assert sar["available"] and len(sar["features"]) == 3 and sar["unmatched"] == 2
    blip = {"lat": 5.5005, "lon": -52.5, "snr": 12.0, "pixels": 8, "elongation": 2.5, "dual_pol": None}
    _apply_gfw_sar(blip, sar["features"], "2026-10-19T21:34:39Z", get_settings())
    assert blip["gfw_confirmed"] and blip["dark_hotspot"] == 2
    assert any("Global Fishing Watch" in f for f in assess(blip)["flags"])
    risk = score_vessel({**blip, "confidence": 0.8}, zone="eez", events=[], ais_source="aisstream.io")
    assert any("non déclarée récurrente" in r for r in risk["reasons"])


def test_maritime_endpoints(client_as: Any) -> None:
    ais_store.store_static("710123456", name="PESCADOR", ship_type=30)
    ais_store.store_positions([fix("710123456", 30), fix("710123456", 5, lon=-52.45)], zone_of=eez)
    client = client_as("viewer")
    picture = client.get("/api/v1/maritime/ais").json()
    props = picture["features"][0]["properties"]
    assert props["category"] == "foreign_fishing" and props["flag"] == "Brésil" and len(props["track"]) == 2
    coverage = client.get("/api/v1/maritime/coverage").json()
    assert coverage["ais"]["vessels_1h"] == 1 and "sar" in coverage and "rules" in coverage
    assert client.get("/api/v1/maritime/gfw-sar").json()["available"] is False  # no token in tests
