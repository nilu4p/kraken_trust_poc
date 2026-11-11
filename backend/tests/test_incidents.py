"""SOC chain: detection → incident, correlation, workflow & RBAC, escalation, SLA, KPIs, evidence vault."""

from __future__ import annotations

import datetime
import io
import json
import zipfile
from typing import Any, Dict

import numpy as np

from app.config import get_settings
from app.core.c2pa import generate_and_seal_alert
from app.core.pki import ensure_pki
from app.db import db
from app.pipeline.orchestrator import _open_incidents
from app.pipeline.quality import assess
from app.soc import incidents, scheduler


def _vessel(lat: float = 5.5, lon: float = -52.5, risk: int = 80, vid: str = "SAR-001") -> Dict[str, Any]:
    return {
        "id": vid,
        "lat": lat,
        "lon": lon,
        "confidence": 0.9,
        "zone": "eez",
        "length_m": 42.0,
        "risk": {"score": risk, "level": "critical", "reasons": []},
        "quality": {"score": 0.8, "level": "reliable", "flags": []},
        "chip": np.random.default_rng(1).random((96, 96), dtype=np.float32),
    }


def _seal(vessels: list, scene: str = "S1A_TEST") -> Dict[str, Any]:
    cfg = get_settings()
    ensure_pki(cfg)
    path = generate_and_seal_alert(vessels, stac_source=scene, settings=cfg)
    assert path is not None
    # Recent pass: correlation only considers incidents seen within incident_correlation_hours.
    seen_at = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)).isoformat()
    _open_incidents(vessels, path, seen_at, scene, cfg)
    return {"dossier": path.parent.name, "alert_id": path.parent.name.rsplit("_", 1)[-1]}


def test_detection_creates_incident_with_sla_and_playbook() -> None:
    _seal([_vessel(risk=80)])
    [incident] = incidents.list_incidents()
    assert incident["ref"].startswith("KRK-") and incident["severity"] == "P1"
    assert incident["status"] == "new" and incident["observations"] == 1
    full = incidents.get_incident(incident["id"])
    assert len(full["actions"]) == len(incidents.PLAYBOOK)
    triage_due = datetime.datetime.fromisoformat(full["triage_due"])
    created = datetime.datetime.fromisoformat(full["created_at"])
    assert (triage_due - created).total_seconds() == get_settings().sla_triage_minutes["P1"] * 60


def test_nearby_detections_are_correlated_and_severity_upgraded() -> None:
    _seal([_vessel(risk=40)])  # P3
    _seal([_vessel(lat=5.55, risk=60)], scene="S1B")  # ~5.6 km away → same incident, upgraded to P2
    _seal([_vessel(lat=7.5, lon=-51.0, risk=20)], scene="S1C")  # far away → new incident
    items = incidents.list_incidents()
    assert len(items) == 2
    merged = next(i for i in items if i["observations"] == 2)
    assert merged["severity"] == "P2"
    kinds = [e["kind"] for e in incidents.get_incident(merged["id"])["events"]]
    assert "observation" in kinds and "severity" in kinds


def test_feed_is_annotated_with_incident(client_as: Any) -> None:
    _seal([_vessel()])
    client = client_as("viewer")
    dark = [f for f in client.get("/api/v1/alerts").json()["features"] if f["properties"]["type"] == "dark_vessel"]
    props = dark[0]["properties"]
    assert props["incident_ref"].startswith("KRK-") and props["incident_status"] == "new"
    assert props["image_url"].startswith("/api/v1/evidence/files/")
    assert client.get(props["image_url"]).status_code == 200  # served through the authenticated vault


def test_full_workflow_with_roles(client_as: Any) -> None:
    _seal([_vessel()])
    incident_id = incidents.list_incidents()[0]["id"]
    analyst = client_as("analyst")
    supervisor = client_as("supervisor")
    viewer = client_as("viewer")
    base = f"/api/v1/incidents/{incident_id}"

    assert viewer.post(f"{base}/transition", json={"status": "triage"}).status_code == 403
    triaged = analyst.post(f"{base}/transition", json={"status": "triage"}).json()["incident"]
    assert triaged["acknowledged_at"] and triaged["assignee"] == analyst.user["username"]
    assert analyst.post(f"{base}/transition", json={"status": "response"}).status_code == 422  # illegal jump
    analyst.post(f"{base}/actions/check_detection", json={"done": True})
    analyst.post(f"{base}/comment", json={"text": "Écho net, 40 m, pas d'AIS."})
    analyst.post(f"{base}/transition", json={"status": "qualified"})
    # Escalation is a supervisor decision.
    assert analyst.post(f"{base}/escalate", json={"target": "cross_ag"}).status_code == 403
    escalated = supervisor.post(
        f"{base}/escalate", json={"target": "cross_ag", "reference": "CROSS-2026-118", "note": "Transmis"}
    ).json()["incident"]
    assert escalated["status"] == "escalated" and escalated["escalation_ref"] == "CROSS-2026-118"
    assert next(a for a in escalated["actions"] if a["key"] == "escalate")["done"]
    supervisor.post(f"{base}/transition", json={"status": "response"})
    assert analyst.post(f"{base}/transition", json={"status": "closed", "resolution": "confirmed_iuu"}).status_code == 403
    assert supervisor.post(f"{base}/transition", json={"status": "closed"}).status_code == 422  # resolution required
    closed = supervisor.post(
        f"{base}/transition", json={"status": "closed", "resolution": "confirmed_iuu", "note": "Navire dérouté"}
    ).json()["incident"]
    assert closed["status"] == "closed" and closed["resolution_label"] == "Pêche INN confirmée"
    kinds = [e["kind"] for e in closed["events"]]
    assert {"created", "status", "comment", "action", "escalation"} <= set(kinds)
    reopened = supervisor.post(f"{base}/transition", json={"status": "triage"}).json()["incident"]
    assert reopened["status"] == "triage" and reopened["resolution"] is None


def test_analyst_can_dismiss_false_positive_and_it_feeds_quality(client_as: Any) -> None:
    _seal([_vessel()])
    incident_id = incidents.list_incidents()[0]["id"]
    analyst = client_as("analyst")
    analyst.post(f"/api/v1/incidents/{incident_id}/transition", json={"status": "triage"})
    response = analyst.post(
        f"/api/v1/incidents/{incident_id}/transition", json={"status": "closed", "resolution": "false_positive", "note": "Bouée"}
    )
    assert response.status_code == 200
    history = incidents.detection_history()
    assert history[0]["review"] == "false_positive"
    target = {"lat": 5.5, "lon": -52.5, "snr": 20.0, "pixels": 12, "elongation": 3.0, "dual_pol": True}
    flagged = assess(target, history=history, scene_id="S1-new")
    assert any("Fausse alarme" in f for f in flagged["flags"])


def test_assignment_rules(client_as: Any) -> None:
    _seal([_vessel()])
    incident_id = incidents.list_incidents()[0]["id"]
    analyst = client_as("analyst")
    other = client_as("analyst", username="analyst-2")
    supervisor = client_as("supervisor")
    viewer = client_as("viewer")
    base = f"/api/v1/incidents/{incident_id}/assign"
    assert analyst.post(base, json={"assignee_id": other.user["id"]}).status_code == 403  # not for others
    assert analyst.post(base, json={"assignee_id": analyst.user["id"]}).status_code == 200  # self-assign
    assert supervisor.post(base, json={"assignee_id": viewer.user["id"]}).status_code == 422  # viewers can't own
    assert supervisor.post(base, json={"assignee_id": other.user["id"]}).json()["incident"]["assignee"] == "analyst-2"
    names = [u["username"] for u in analyst.get("/api/v1/users/assignable").json()["users"]]
    assert "analyst-2" in names and "viewer-user" not in names


def test_sla_breach_flagged_once_and_kpis(client_as: Any) -> None:
    _seal([_vessel()])
    incident_id = incidents.list_incidents()[0]["id"]
    past = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=5)).isoformat()
    with db() as conn:
        conn.execute("UPDATE incidents SET triage_due = ? WHERE id = ?", (past, incident_id))
    assert scheduler.check_sla() == 1
    assert scheduler.check_sla() == 0  # not re-flagged
    kpis = client_as("viewer").get("/api/v1/incidents/kpis").json()
    assert kpis["open"] == 1 and kpis["sla_breached"] == 1 and kpis["open_by_severity"]["P1"] == 1
    assert kpis["unassigned"] == 1 and kpis["detections_7d"] == 1


def test_queue_filters(client_as: Any) -> None:
    _seal([_vessel(risk=80)])
    _seal([_vessel(lat=7.5, lon=-51.0, risk=20)], scene="S1C")
    client = client_as("analyst")
    assert len(client.get("/api/v1/incidents", params={"severity": "P1"}).json()["incidents"]) == 1
    assert len(client.get("/api/v1/incidents", params={"scope": "unassigned"}).json()["incidents"]) == 2
    assert client.get("/api/v1/incidents", params={"scope": "mine"}).json()["incidents"] == []
    ref = incidents.list_incidents()[0]["ref"]
    assert client.get("/api/v1/incidents", params={"q": ref}).json()["incidents"][0]["ref"] == ref
    meta = client.get("/api/v1/incidents/meta").json()
    assert "cross_ag" in meta["escalation_targets"] and "confirmed_iuu" in meta["resolutions"]


def test_evidence_vault_verify_and_export_with_custody(client_as: Any) -> None:
    sealed = _seal([_vessel()])
    analyst = client_as("analyst")
    viewer = client_as("viewer")
    dossiers = viewer.get("/api/v1/evidence").json()["dossiers"]
    assert dossiers[0]["dossier"] == sealed["dossier"] and dossiers[0]["incidents"][0]["ref"].startswith("KRK-")
    assert viewer.get(f"/api/v1/evidence/{sealed['dossier']}/export").status_code == 403  # analyst+
    report = analyst.post(f"/api/v1/evidence/{sealed['dossier']}/verify").json()
    assert report["integrity"] in ("verified", "fallback") and all(f["match"] for f in report["files"])
    export = analyst.get(f"/api/v1/evidence/{sealed['dossier']}/export")
    assert export.status_code == 200 and export.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(export.content))
    names = {n.split("/", 1)[1] for n in archive.namelist()}
    assert {"radar_crop.jpg", "manifest.json", "verification.json", "chain_of_custody.json", "rootCA_cert.pem"} <= names
    custody = json.loads(archive.read(f"{sealed['dossier']}/chain_of_custody.json"))
    assert [e["action"] for e in custody][:2] == ["evidence.verify", "evidence.verify"]
    incident = incidents.get_incident(incidents.list_incidents()[0]["id"])
    assert any(e["kind"] == "evidence_export" for e in incident["events"])


def test_point_dossier(client_as: Any) -> None:
    _seal([_vessel()])
    dossier = client_as("viewer").get("/api/v1/point", params={"lat": 5.51, "lon": -52.5}).json()
    assert dossier["zone"] == "eez" and dossier["incidents"][0]["distance_km"] < 2


def test_runtime_settings_admin_only_and_validated(client_as: Any) -> None:
    admin = client_as("admin")
    assert client_as("supervisor").put("/api/v1/system/settings", json={"cfar_k": 6}).status_code == 403
    assert admin.put("/api/v1/system/settings", json={"cfar_k": 99}).status_code == 422
    assert admin.put("/api/v1/system/settings", json={"unknown": 1}).status_code == 422
    updated = admin.put("/api/v1/system/settings", json={"cfar_k": 6.5, "auto_scan_interval_minutes": 60}).json()
    assert updated["settings"]["cfar_k"]["value"] == 6.5 and get_settings().cfar_k == 6.5
    get_settings().cfar_k = 5.0
    get_settings().auto_scan_interval_minutes = 0
