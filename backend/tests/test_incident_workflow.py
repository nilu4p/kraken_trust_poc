"""Incident list order and cancellation of an escalation or an intervention."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from app.db import db
from app.soc import incidents
from tests.test_incidents import _seal, _vessel


def _qualified(make_user: Any) -> tuple:
    _seal([_vessel()])
    [incident] = incidents.list_incidents()
    analyst = make_user("ana", "analyst")
    supervisor = make_user("sup", "supervisor")
    incidents.transition(incident["id"], "triage", analyst)
    incidents.transition(incident["id"], "qualified", analyst)
    return incident["id"], analyst, supervisor


def test_list_is_chronological_with_oldest_first_option() -> None:
    _seal([_vessel(risk=20)], scene="S1A")                       # P4, opened first
    _seal([_vessel(lat=7.5, lon=-51.0, risk=90)], scene="S1B")   # P1, opened second
    with db() as conn:  # make the opening dates distinct and in that order
        for offset, (incident_id,) in enumerate(conn.execute("SELECT id FROM incidents ORDER BY id").fetchall()):
            stamp = (datetime.datetime(2026, 10, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(hours=offset)).isoformat()
            conn.execute("UPDATE incidents SET created_at = ? WHERE id = ?", (stamp, incident_id))
    recent = [i["severity"] for i in incidents.list_incidents()]
    assert recent == ["P1", "P4"]
    assert [i["severity"] for i in incidents.list_incidents(sort="oldest")] == ["P4", "P1"]
    assert [i["severity"] for i in incidents.list_incidents(sort="priority")] == ["P1", "P4"]


def test_cancel_escalation(make_user: Any) -> None:
    incident_id, analyst, supervisor = _qualified(make_user)
    incidents.escalate(incident_id, "cross_ag", "REF-12", "", supervisor)
    assert incidents.get_incident(incident_id)["status"] == "escalated"
    with pytest.raises(PermissionError):
        incidents.transition(incident_id, "qualified", analyst, note="erreur de cible")
    with pytest.raises(ValueError, match="motif"):
        incidents.transition(incident_id, "qualified", supervisor, note="")
    done = incidents.transition(incident_id, "qualified", supervisor, note="Navire identifié : licence valide")
    assert done["status"] == "qualified" and done["escalation_target"] is None and done["escalated_at"] is None
    assert not next(a for a in done["actions"] if a["key"] == "escalate")["done"]
    event = next(e for e in done["events"] if e["kind"] == "status")
    assert event["payload"]["cancelled"] == "escalation" and event["payload"]["withdrawn_escalation"]["reference"] == "REF-12"
    # The replay still shows "escalated" before the cancellation.
    states = incidents.state_at(datetime.datetime.now(datetime.timezone.utc), None)
    assert states[incident_id]["status"] == "qualified"


def test_cancel_intervention(make_user: Any) -> None:
    incident_id, _analyst, supervisor = _qualified(make_user)
    incidents.transition(incident_id, "response", supervisor)
    with pytest.raises(ValueError, match="Aucune escalade"):
        incidents.transition(incident_id, "escalated", supervisor, note="moyen indisponible")
    back = incidents.transition(incident_id, "qualified", supervisor, note="Moyen nautique indisponible")
    assert back["status"] == "qualified"
    incidents.escalate(incident_id, "marine_nationale", "OPS-7", "", supervisor)
    incidents.transition(incident_id, "response", supervisor)
    back = incidents.transition(incident_id, "escalated", supervisor, note="Intervention reportée (météo)")
    assert back["status"] == "escalated" and back["escalation_ref"] == "OPS-7"  # escalation kept
    assert not next(a for a in back["actions"] if a["key"] == "follow_up")["done"]


def test_api_sort_and_cancel(client_as: Any, make_user: Any) -> None:
    incident_id, _a, _s = _qualified(make_user)
    supervisor = client_as("supervisor", "sup2")
    assert supervisor.get("/api/v1/incidents", params={"sort": "oldest", "scope": "all"}).status_code == 200
    assert supervisor.get("/api/v1/incidents", params={"sort": "n'importe"}).status_code == 422
    supervisor.post(f"/api/v1/incidents/{incident_id}/escalate", json={"target": "cross_ag", "reference": "R1", "note": ""})
    refused = supervisor.post(f"/api/v1/incidents/{incident_id}/transition", json={"status": "qualified", "note": ""})
    assert refused.status_code == 422 and "motif" in refused.json()["detail"]
    ok = supervisor.post(f"/api/v1/incidents/{incident_id}/transition", json={"status": "qualified", "note": "Doublon d'alerte"})
    assert ok.status_code == 200 and ok.json()["incident"]["status"] == "qualified"
