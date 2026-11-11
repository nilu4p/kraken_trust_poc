"""Command support: interception geometry, decision advice, means, signed reports to the chain of command."""

from __future__ import annotations

import datetime
import math
from typing import Any, Dict

import pytest

from app.config import get_settings
from app.db import db
from app.soc import assets, decision, incidents, reports


def _incident(lat: float = 5.6, lon: float = -52.0, risk: int = 85, quality: float = 0.8, vid: str = "SAR-001",
              seen_at: str = "2026-10-22T10:00:00+00:00", alert: str = "a1") -> Dict[str, Any]:
    vessel = {"id": vid, "lat": lat, "lon": lon, "zone": "eez", "risk": {"score": risk}, "quality": {"score": quality}}
    incident, _ = incidents.ingest_detection(vessel, alert_id=alert, dossier="", scene_id=None, seen_at=seen_at)
    return incidents.get_incident(incident["id"])


def _store_fix(mmsi: str, ts: str, lat: float, lon: float, sog: float, cog: float) -> None:
    with db() as conn:
        conn.execute("INSERT INTO ais_positions (mmsi, ts, lat, lon, sog, cog, source) VALUES (?, ?, ?, ?, ?, ?, 'aisstream')",
                     (mmsi, ts, lat, lon, sog, cog))


# --------------------------------------------------------------------------- geometry


def test_intercept_of_a_stationary_target_is_readiness_plus_transit() -> None:
    t = decision.intercept_time_h((0.0, 0.0), (0.0, 0.0), (-37.04, 0.0), speed_kmh=18.52, readiness_h=0.5)
    assert t == pytest.approx(0.5 + 2.0, abs=1e-6)


def test_intercept_of_a_receding_target_takes_longer_and_a_faster_one_escapes() -> None:
    base = (0.0, -50.0)
    still = decision.intercept_time_h((0.0, 0.0), (0.0, 0.0), base, 40.0, 0.0)
    fleeing = decision.intercept_time_h((0.0, 0.0), (0.0, 20.0), base, 40.0, 0.0)
    assert still == pytest.approx(1.25) and fleeing == pytest.approx(2.5)
    # The meeting point is reached exactly at the asset's speed.
    assert math.hypot(0.0, 20.0 * fleeing + 50.0) == pytest.approx(40.0 * fleeing)
    assert decision.intercept_time_h((0.0, 0.0), (0.0, 50.0), base, 40.0, 0.0) is None


def test_target_coming_towards_the_base_is_met_earlier() -> None:
    approaching = decision.intercept_time_h((0.0, 0.0), (0.0, -20.0), (0.0, -60.0), 40.0, 0.0)
    assert approaching == pytest.approx(1.0)


# --------------------------------------------------------------------------- target state & advice


def test_dark_target_uses_a_growing_datum_circle() -> None:
    incident = _incident()
    now = datetime.datetime(2026, 10, 22, 13, 0, tzinfo=datetime.timezone.utc)
    state = decision.target_state(incident, now, get_settings())
    assert state["kinematics"] == "unknown" and state["age_minutes"] == 180
    assert state["uncertainty_nm"] == pytest.approx(1 + 8 * 3)
    assert (state["lat_now"], state["lon_now"]) == (5.6, -52.0)


def test_two_observations_give_an_estimated_course() -> None:
    _incident(lat=5.6, lon=-52.0, seen_at="2026-10-22T08:00:00+00:00", alert="a1")
    incident = _incident(lat=5.7, lon=-52.0, seen_at="2026-10-22T10:00:00+00:00", alert="a2")
    state = decision.target_state(incident, datetime.datetime(2026, 10, 22, 10, 0, tzinfo=datetime.timezone.utc), get_settings())
    assert state["kinematics"] == "observations"
    assert state["cog_deg"] == pytest.approx(0, abs=1)
    assert state["sog_kn"] == pytest.approx(11.06 / 1.852 / 2, abs=0.2)


def test_ais_incident_follows_the_vessel_live() -> None:
    record = {"id": "AIS-227000001", "lat": 5.6, "lon": -52.0, "zone": "eez", "risk": {"score": 80}, "quality": {"score": 1.0}}
    created, _ = incidents.ingest_detection(record, alert_id="rule-x", dossier="", scene_id=None,
                                            seen_at="2026-10-22T09:00:00+00:00", kind="foreign_fishing", same_vessel_only=True)
    _store_fix("227000001", "2026-10-22T11:00:00+00:00", 5.8, -52.0, 10.0, 0.0)
    incident = incidents.get_incident(created["id"])
    now = datetime.datetime(2026, 10, 22, 12, 0, tzinfo=datetime.timezone.utc)
    state = decision.target_state(incident, now, get_settings())
    assert state["kinematics"] == "ais" and state["mmsi"] == "227000001"
    assert state["lat_now"] == pytest.approx(5.8 + 18.52 / 110.57, abs=1e-3)  # 10 kn north for one hour


def test_reliable_p1_within_reach_recommends_interception() -> None:
    incident = _incident(lat=5.4, lon=-52.0, quality=0.85)
    now = datetime.datetime.fromisoformat(incident["last_seen"]) + datetime.timedelta(minutes=20)
    result = decision.assess(incident, now)
    assert result["recommended"] == "intercept"
    best = next(m for m in result["means"] if m["feasible"] and m["can_board"])
    assert best["intercept_zone"] == "eez" and best["eta_minutes"] > best["readiness_min"]
    assert any("CNUDM art. 73" in text for text in result["legal"])
    assert any("valeurs génériques" in c for c in result["caveats"])


def test_doubtful_detection_recommends_airborne_identification() -> None:
    incident = _incident(lat=5.4, lon=-52.0, quality=0.3)
    now = datetime.datetime.fromisoformat(incident["last_seen"]) + datetime.timedelta(minutes=20)
    assert decision.assess(incident, now)["recommended"] == "identify"


def test_unavailable_or_out_of_range_means_are_not_feasible() -> None:
    fleet = assets.list_assets()
    for asset in fleet:
        assets.update(asset["id"], {"status": "unavailable"}, "sup")
    incident = _incident(lat=5.4, lon=-52.0)
    now = datetime.datetime.fromisoformat(incident["last_seen"]) + datetime.timedelta(minutes=20)
    result = decision.assess(incident, now)
    assert not any(m["feasible"] for m in result["means"])
    assert result["recommended"] == "cooperate"
    short = assets.create({"name": "Annexe", "kind": "fast_boat", "base_lat": 4.93, "base_lon": -52.22, "speed_kn": 20,
                           "endurance_nm": 10}, "sup")
    [evaluated] = [m for m in decision.assess(incident, now)["means"] if m["asset_id"] == short["id"]]
    assert not evaluated["in_range"] and any("distance franchissable" in i for i in evaluated["issues"])


def test_asset_validation_and_seed_once() -> None:
    first = assets.list_assets()
    assert len(first) == len(assets.DEFAULT_FLEET)
    for asset in first:
        assets.delete(asset["id"], "sup")
    assert assets.list_assets() == []  # an emptied fleet is not silently re-seeded
    with pytest.raises(assets.AssetError):
        assets.create({"name": "X", "kind": "submarine", "base_lat": 5, "base_lon": -52, "speed_kn": 10, "endurance_nm": 10}, "sup")
    with pytest.raises(assets.AssetError):
        assets.create({"name": "X", "kind": "fast_boat", "base_lat": 95, "base_lon": -52, "speed_kn": 10, "endurance_nm": 10}, "sup")


# --------------------------------------------------------------------------- reports


def _user(role: str = "supervisor") -> Dict[str, Any]:
    return {"id": 1, "username": "chef", "display_name": "Chef de quart", "role": role}


def test_incident_report_is_signed_logged_and_verifiable() -> None:
    incident = _incident()
    incidents.comment(incident["id"], "Écho confirmé sur l'imagerie optique", {"id": 1, "username": "ana", "role": "analyst"})
    draft = reports.draft_incident(incident["id"], _user(), assessment="Navire de pêche probable.")
    assert draft["draft"] and "(projet — non émis)" in draft["content"]
    assert reports.list_reports() == []  # a preview stores nothing

    issued = reports.draft_incident(incident["id"], _user(), assessment="Navire de pêche probable.",
                                    proposals="Engager la vedette.", addressees="CROSS AG", issue=True)
    assert issued["ref"].startswith("CR-") and issued["signed"] and issued["incident_id"] == incident["id"]
    text = issued["content"]
    assert text.startswith(get_settings().report_classification)
    for expected in (incident["ref"], "GDH", "CROSS AG", "Écho confirmé", "Engager la vedette", "CNUDM"):
        assert expected in text
    events = incidents.get_incident(incident["id"])["events"]
    assert events[0]["kind"] == "report" and events[0]["payload"]["ref"] == issued["ref"]
    check = reports.verify(issued["id"], _user())
    assert check["valid"] and check["signature_ok"] and check["chain_ok"]
    with db() as conn:
        actions = [r[0] for r in conn.execute("SELECT action FROM audit_log").fetchall()]
    assert "report.issue" in actions and "report.verify" in actions


def test_tampered_report_fails_verification() -> None:
    incident = _incident()
    issued = reports.draft_incident(incident["id"], _user(), issue=True)
    with db() as conn:
        conn.execute("UPDATE reports SET content = replace(content, 'P1', 'P4') WHERE id = ?", (issued["id"],))
    check = reports.verify(issued["id"], _user())
    assert not check["valid"] and not check["hash_ok"] and check["signature_ok"] is False


def test_export_carries_the_seal_after_the_signed_text() -> None:
    issued = reports.draft_incident(_incident()["id"], _user(), issue=True)
    name, text = reports.export_text(issued["id"], _user())
    assert name == f"{issued['ref']}.txt"
    signed, seal = text.split("=" * reports.WIDTH, 1)
    assert signed == issued["content"] and issued["sha256"] in seal and "BEGIN CERTIFICATE" in seal


def test_sitrep_counts_the_period_and_rejects_bad_periods() -> None:
    _incident(risk=90)
    _incident(lat=7.5, lon=-51.0, risk=20, alert="a2")
    draft = reports.draft_sitrep(_user())
    assert "2 incident(s) ouvert(s) (P1 1, P2 0, P3 0, P4 1)" in draft["content"]
    assert "Patrouilleur hauturier" in draft["content"]
    now = datetime.datetime.now(datetime.timezone.utc)
    with pytest.raises(reports.ReportError):
        reports.draft_sitrep(_user(), start=now, end=now - datetime.timedelta(hours=1))
    with pytest.raises(reports.ReportError):
        reports.draft_sitrep(_user(), start=now - datetime.timedelta(days=40), end=now)
    with pytest.raises(reports.ReportError):
        reports.draft_sitrep(_user(), classification="TRÈS SECRET")


def test_dtg_format() -> None:
    assert reports.dtg(datetime.datetime(2026, 10, 22, 14, 30, tzinfo=datetime.timezone.utc)) == "221430Z OCT 26"


# --------------------------------------------------------------------------- API & roles


def test_command_api_roles(client_as: Any) -> None:
    incident = _incident()
    viewer = client_as("viewer")
    assert viewer.get(f"/api/v1/incidents/{incident['id']}/decision").status_code == 200
    assert viewer.get("/api/v1/assets").json()["assets"]
    assert viewer.post("/api/v1/assets", json={}).status_code in (403, 422)
    assert viewer.post(f"/api/v1/reports/incident/{incident['id']}", json={}).status_code == 403

    analyst = client_as("analyst")
    preview = analyst.post(f"/api/v1/reports/incident/{incident['id']}", json={"assessment": "RAS"})
    assert preview.status_code == 200 and preview.json()["draft"]
    issued = analyst.post(f"/api/v1/reports/incident/{incident['id']}", json={"issue": True}).json()
    assert analyst.post("/api/v1/reports/sitrep", json={"issue": True}).status_code == 403
    assert analyst.post("/api/v1/reports/sitrep", json={"start": "2026-10-22T00:00:00"}).status_code == 422
    assert analyst.post(f"/api/v1/reports/{issued['id']}/verify").json()["valid"]
    export = analyst.get(f"/api/v1/reports/{issued['id']}/export")
    assert export.status_code == 200 and export.headers["content-type"].startswith("text/plain")
    assert "certificate" not in viewer.get(f"/api/v1/reports/{issued['id']}").json()["report"]

    supervisor = client_as("supervisor")
    assert supervisor.post("/api/v1/reports/sitrep", json={"issue": True}).json()["ref"].startswith("SITREP-")
    created = supervisor.post("/api/v1/assets", json={"name": "Vedette 2", "kind": "fast_boat", "base_lat": 5.0,
                                                      "base_lon": -52.3, "speed_kn": 25, "endurance_nm": 200})
    assert created.status_code == 201
    assert supervisor.put(f"/api/v1/assets/{created.json()['id']}", json={"status": "engaged"}).json()["status"] == "engaged"
    assert supervisor.delete(f"/api/v1/assets/{created.json()['id']}").status_code == 204
    assert [r["kind"] for r in viewer.get("/api/v1/reports").json()["reports"]] == ["sitrep", "incident"]
