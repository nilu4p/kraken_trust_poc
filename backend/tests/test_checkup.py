"""Regression tests for the reliability check-up (stale AIS, re-scans, flags, licences, context)."""

from __future__ import annotations

import datetime
import time
from typing import Any, Dict, List

import pytest

from app.config import get_settings
from app.core import audit
from app.core.pki import generate_pki
from app.pipeline import detection
from app.pipeline.aggregation import get_all_kraken_geojson
from app.pipeline.quality import assess
from app.pipeline.risk import nearby_events, score_vessel
from app.core.c2pa import generate_and_seal_alert
from app.soc import authorized, incidents, rules
from app.sources import ais_store, aisstream, open_meteo
from app.sources.mid import flag_name, is_french_flag

NOW = datetime.datetime.now(datetime.timezone.utc)


def eez(_lon: float, _lat: float) -> str:
    return "eez"


@pytest.fixture(autouse=True)
def quiet_notifications(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    sent: List[str] = []
    monkeypatch.setattr("app.soc.notify.publish", lambda channel, text, **_kw: sent.append(f"{channel}|{text}") or True)
    return sent


# --------------------------------------------------------------------------- AIS at the pass time


def test_old_scene_is_never_compared_with_live_ais(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "aisstream_api_key", "key")
    calls: List[int] = []
    monkeypatch.setattr("app.sources.aisstream.fetch_live_ais", lambda cfg=None: calls.append(1) or [{"mmsi": "1", "lat": 5, "lon": -52}])
    yesterday = (NOW - datetime.timedelta(hours=25)).isoformat()
    tracks, source = detection.load_ais_tracks(get_settings(), yesterday)
    assert (tracks, source) == ([], "unavailable") and calls == []
    recent = (NOW - datetime.timedelta(minutes=30)).isoformat()
    tracks, source = detection.load_ais_tracks(get_settings(), recent)
    assert source == "aisstream.io" and len(tracks) == 1 and calls == [1]


def test_unknown_ais_lowers_the_risk() -> None:
    vessel = {"lat": 5.5, "lon": -52.5, "confidence": 0.9}
    known = score_vessel(vessel, zone="eez", events=[], ais_source="aisstream.io (historique)")
    unknown = score_vessel(vessel, zone="eez", events=[], ais_source="unavailable")
    assert unknown["score"] < known["score"]
    assert any("absence d'AIS n'est pas démontrée" in r for r in unknown["reasons"])


# --------------------------------------------------------------------------- re-scans


def test_rescan_of_the_same_scene_adds_no_observation() -> None:
    target = {"id": "SAR-002", "lat": 4.692, "lon": -51.811, "risk": {"score": 38}, "quality": {"score": 0.32}}
    first, created = incidents.ingest_detection(target, alert_id="a1", dossier="d1", scene_id="S1D_X", seen_at=NOW.isoformat())
    again, created_again = incidents.ingest_detection(target, alert_id="a2", dossier="d2", scene_id="S1D_X", seen_at=NOW.isoformat())
    assert created and not created_again and again["id"] == first["id"]
    detail = incidents.get_incident(first["id"])
    assert len(detail["observations"]) == 1


def test_map_hides_old_closed_targets(tmp_path: Any) -> None:
    cfg = get_settings()
    generate_pki(cfg.keys_dir)
    old_scene = {"type": "Feature", "geometry": None,
                 "properties": {"datetime": (NOW - datetime.timedelta(days=45)).isoformat(), "scene_id": "OLD"}}
    generate_and_seal_alert([{"id": "SAR-001", "lat": 5.0, "lon": -52.0, "confidence": 0.8}], stac_source="OLD", scene=old_scene)
    assert len([f for f in get_all_kraken_geojson(max_age_days=30)["features"] if f["properties"]["type"] == "dark_vessel"]) == 0
    assert len([f for f in get_all_kraken_geojson(max_age_days=None)["features"] if f["properties"]["type"] == "dark_vessel"]) == 1


# --------------------------------------------------------------------------- flags


def test_overseas_flags_are_french_and_codes_get_names() -> None:
    assert is_french_flag("MTQ") and is_french_flag("GUF") and is_french_flag("Guyane (FR)") and not is_french_flag("VEN")
    assert flag_name("VEN") == "Venezuela" and flag_name("Brésil") == "Brésil"
    assert not rules._foreign({"flag": None, "gfw_flag": "MTQ"})
    assert rules._foreign({"flag": None, "gfw_flag": "VEN"})


# --------------------------------------------------------------------------- authorised vessels


def test_authorised_list_matches_hard_identifiers_only() -> None:
    with pytest.raises(ValueError, match="identifiant certain"):
        authorized.add_entry({"name": "ALTAMIRA III"}, actor="t")
    authorized.add_entry({"name": "ALTAMIRA III", "mmsi": "775000001", "licence": "UE-GUF-2026-12"}, actor="t")
    authorized.add_entry({"name": "OLD", "imo": "1234567", "valid_until": "2020-01-01"}, actor="t")
    matcher = authorized.AuthorizedMatcher()
    assert matcher.match(mmsi="775000001")["licence"] == "UE-GUF-2026-12"
    assert matcher.match(imo="1234567") is None  # expired licence
    assert matcher.match(mmsi="775000999") is None
    assert authorized.list_entries()["active"] == 1


def test_licensed_vessel_not_raised_as_foreign_fishing_but_mpa_still_applies(monkeypatch: pytest.MonkeyPatch) -> None:
    authorized.add_entry({"mmsi": "775000001", "licence": "L1"}, actor="t")
    ais_store.store_static("775000001", name="ALTAMIRA III", ship_type=30)
    ais_store.store_static("775000002", name="SIN LICENCIA", ship_type=30)
    ais_store.store_positions([{"mmsi": "775000001", "ts": NOW.isoformat(), "lat": 6.0, "lon": -52.0, "sog": 8},
                               {"mmsi": "775000002", "ts": NOW.isoformat(), "lat": 6.1, "lon": -52.0, "sog": 8}], zone_of=eez)
    assert rules.run_rules()["foreign_fishing"] == 1
    assert "SIN LICENCIA" in incidents.list_incidents()[0]["title"]


def test_authorised_import_and_api(client_as: Any) -> None:
    csv_body = ("Nom;MMSI;IMO;Indicatif;Pavillon;Licence;Fin validité\n"
                "ALTAMIRA III;775000001;;YYV1;VEN;UE-1;31/12/2027\n"
                "SANS ID;;;;VEN;UE-2;\n").encode()
    admin = client_as("admin")
    report = admin.post("/api/v1/authorized/import", content=csv_body, headers={"Content-Type": "application/octet-stream"}).json()
    assert report["imported"] == 1 and report["rejected_count"] == 1
    assert admin.get("/api/v1/authorized").json()["entries"][0]["valid_until"] == "2027-12-31"
    assert client_as("analyst").post("/api/v1/authorized", json={"mmsi": "775000003"}).status_code == 403
    assert client_as("supervisor").post("/api/v1/authorized", json={"mmsi": "775000003"}).status_code == 200
    ais_store.store_static("775000001", name="ALTAMIRA III", ship_type=30)
    ais_store.store_positions([{"mmsi": "775000001", "ts": NOW.isoformat(), "lat": 6.0, "lon": -52.0}], zone_of=eez)
    props = client_as("viewer").get("/api/v1/maritime/ais").json()["features"][0]["properties"]
    assert props["category"] == "licensed" and props["licence"]["licence"] == "UE-1" and props["flag"] == "Venezuela"


def test_licensed_and_stale_gap_events_do_not_raise_risk() -> None:
    def event(kind: str, mmsi: str, hours: float = 10) -> Dict[str, Any]:
        return {"geometry": {"coordinates": [-52.0, 6.0]},
                "properties": {"event_type": kind, "mmsi": mmsi, "gap_hours": hours}}

    events = [event("loitering", "775000001"), event("gap", "999", hours=370), event("gap", "998", hours=12)]
    assert nearby_events(6.0, -52.0, events, 30, exclude_mmsi={"775000001"}) == {"gap": 1}


# --------------------------------------------------------------------------- context and evidence


def test_shallow_guiana_waters_lower_quality() -> None:
    base = {"lat": 4.69, "lon": -51.81, "snr": 8.0, "pixels": 8, "elongation": 2.5, "dual_pol": True}
    deep = assess({**base, "ocean": {"depth_m": 40}})
    shallow = assess({**base, "ocean": {"depth_m": 6}})
    assert shallow["score"] == pytest.approx(deep["score"] - 0.1)
    assert any("bancs de vase" in f for f in shallow["flags"])


def test_sea_state_is_taken_at_the_pass_time(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: List[Dict[str, Any]] = []

    def fake(url: str, params: Dict[str, Any], **_kw: Any) -> Dict[str, Any]:
        seen.append(params)
        if "marine" in url:
            return {"hourly": {"wave_height": [0.7], "sea_surface_temperature": [28.1]}}
        return {"hourly": {"wind_speed_10m": [7.08]}}

    monkeypatch.setattr(open_meteo, "get_json", fake)
    state = open_meteo.sea_state_at(5.6, -52.3, "2026-10-19T21:34:39Z")
    assert state["at_pass"] and state["time"] == "2026-10-19T21:00" and state["wind_speed_ms"] == 7.08
    assert all(p["start_hour"] == "2026-10-19T21:00" for p in seen)


def test_evidence_previews_are_not_custody_events(client_as: Any) -> None:
    cfg = get_settings()
    generate_pki(cfg.keys_dir)
    path = generate_and_seal_alert([{"id": "SAR-001", "lat": 5.0, "lon": -52.0, "confidence": 0.8}], stac_source="S")
    dossier = path.parent.name
    client = client_as("viewer")
    assert client.get(f"/api/v1/evidence/files/{dossier}/radar_crop.jpg?preview=1").status_code == 200
    assert not audit.search(target=f"evidence:{dossier}")
    client.get(f"/api/v1/evidence/files/{dossier}/radar_crop.jpg")
    assert [e["action"] for e in audit.search(target=f"evidence:{dossier}")] == ["evidence.view"]


def test_collector_reports_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(aisstream.COLLECTOR, "connected_since", time.time() - 3 * 3600)
    monkeypatch.setitem(aisstream.COLLECTOR, "last_message_at", None)
    status = aisstream.collector_status()
    assert status["silent_minutes"] >= 179 and status["messages"] == aisstream.COLLECTOR["messages"]


def test_simulated_ais_never_used_in_production() -> None:
    from app.pipeline.detection import load_ais_tracks

    cfg = get_settings()
    assert load_ais_tracks(cfg)[1] == "mock"  # development demo without any AIS key
    tracks, source = load_ais_tracks(cfg.model_copy(update={"environment": "production"}))
    assert tracks == [] and source == "unavailable"
