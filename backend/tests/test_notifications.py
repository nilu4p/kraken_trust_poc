"""SOC notifications: channel routing, detailed cards, lifecycle, scan reports, sources, security, anti-flood."""

from __future__ import annotations

from typing import Any, Dict, List

import httpx
import pytest

from app.config import get_settings
from app.soc import notify, scheduler

INCIDENT: Dict[str, Any] = {
    "id": 12, "ref": "KRK-2026-0012", "title": "Navire sans AIS en ZEE (~42 m)", "severity": "P1", "status": "new",
    "risk_score": 82, "lat": 5.61, "lon": -52.31, "zone": "eez", "first_seen": "2026-10-19T21:34:39+00:00",
    "triage_due": "2099-01-01T00:00:00+00:00",
}
VESSEL = {
    "length_m": 42.0, "dual_pol": True, "ais_source": "aisstream.io",
    "risk": {"reasons": ["Écho radar sans AIS (confiance 90 %)", "Dans la ZEE (200 NM)"]},
    "quality": {"score": 0.82, "level": "reliable", "flags": ["Confirmé en double polarisation"]},
}


@pytest.fixture()
def captured(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    """Capture webhook calls synchronously."""
    calls: List[Dict[str, Any]] = []
    cfg = get_settings()
    monkeypatch.setattr(cfg, "alert_webhook_url", "http://notif-sink:8090/hooks/{channel}")
    monkeypatch.setattr(cfg, "webhook_allowed_hosts", ["notif-sink"])

    def fake_post(url: str, json: Dict[str, Any], **_kw: Any) -> httpx.Response:
        calls.append({"url": url, "payload": json})
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", fake_post)

    monkeypatch.setattr(notify, "_spawn", lambda target, *args: target(*args))
    return calls


def test_routing_per_channel_and_single_url(captured: List[Dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    notify.publish("sla", "x")
    assert captured[-1]["url"] == "http://notif-sink:8090/hooks/sla"
    assert captured[-1]["payload"]["channel"] == "kraken-sla"
    monkeypatch.setattr(get_settings(), "alert_webhook_url", "http://notif-sink:8090/hooks/kraken")
    notify.publish("securite", "y")
    assert captured[-1]["url"] == "http://notif-sink:8090/hooks/kraken"  # single URL: channel in payload
    assert captured[-1]["payload"]["channel"] == "kraken-securite"
    assert notify.publish("unknown", "z") is False


def test_incident_card_is_detailed(captured: List[Dict[str, Any]]) -> None:
    notify.notify_incident(INCIDENT, "Nouvel incident", vessel=VESSEL, dossier="alerte_20261019_213439_deadbeef")
    payload = captured[-1]["payload"]
    card = payload["attachments"][0]
    fields = {f["title"]: f["value"] for f in card["fields"]}
    assert captured[-1]["url"].endswith("/hooks/alertes") and card["color"] == "#ff3b3b"
    assert "5°36" in fields["Position"] and "W" in fields["Position"]
    assert fields["Fiabilité détection"].startswith("82 %") and fields["Polarisation"] == "double ✓"
    assert "Dans la ZEE" in fields["Pourquoi ce risque"] and "double polarisation" in fields["Signaux de fiabilité"]
    assert fields["Taille estimée"] == "~42 m" and "dans" in fields["Échéance"]
    labels = [a["text"] for a in card["actions"]]
    assert labels == ["Ouvrir l'incident", "Situation tactique", "Dossier de preuves"]
    assert card["actions"][2]["url"].endswith("#/evidence/alerte_20261019_213439_deadbeef")


def test_workflow_messages_go_to_escalades(captured: List[Dict[str, Any]]) -> None:
    notify.notify_workflow(INCIDENT, "escalation", "chef", {"label": "CROSS Antilles-Guyane", "reference": "CROSS-118", "note": "Transmis"})
    card = captured[-1]["payload"]["attachments"][0]
    fields = {f["title"]: f["value"] for f in card["fields"]}
    assert captured[-1]["url"].endswith("/hooks/escalades") and "CROSS Antilles-Guyane" in card["pretext"]
    assert fields["Référence de transmission"] == "CROSS-118" and fields["Par"] == "chef"
    notify.notify_workflow(INCIDENT, "closed", "chef", {"resolution_label": "Pêche INN confirmée", "note": "Dérouté"})
    assert "Pêche INN confirmée" in captured[-1]["payload"]["attachments"][0]["pretext"]
    assert notify.notify_workflow(INCIDENT, "comment", "x", {}) is False  # not notified


def test_scan_report(captured: List[Dict[str, Any]]) -> None:
    notify.notify_scan({"status": "done", "scene_id": "S1A_X", "dark_vessels": 3, "radar_targets": 9, "discarded": 2,
                        "new_incidents": [{"ref": "KRK-2026-0012", "severity": "P1"}], "actor": "ana", "ais_source": "mock"})
    card = captured[-1]["payload"]["attachments"][0]
    fields = {f["title"]: f["value"] for f in card["fields"]}
    assert captured[-1]["url"].endswith("/hooks/operations") and fields["Nouveaux incidents"] == "KRK-2026-0012 [P1]"
    notify.notify_scan({"status": "error", "message": "SECRET_KEY Copernicus refusée"})
    assert captured[-1]["payload"]["attachments"][0]["color"] == notify.LEVEL_COLORS["danger"]


def test_source_outage_and_recovery_are_notified_once(captured: List[Dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    from app.sources import health

    health.record("shom", True)
    scheduler.watch_sources()
    for _ in range(3):
        health.record("shom", False, "HTTP 503")
    assert scheduler.watch_sources() == 1
    assert scheduler.watch_sources() == 0  # still down: no repeat
    health.record("shom", True)
    assert scheduler.watch_sources() == 1
    titles = [c["payload"]["text"] for c in captured]
    assert titles == ["Source indisponible : shom", "Source rétablie : shom"]


def test_lockout_and_audit_tamper_go_to_securite(captured: List[Dict[str, Any]], make_user: Any) -> None:
    from app.core import identity
    from app.db import db

    make_user("bob", "analyst")
    for _ in range(get_settings().login_max_attempts):
        with pytest.raises(identity.AuthError):
            identity.login("bob", "bad", ip="203.0.113.7", user_agent="t")
    lock = [c for c in captured if "verrouillé" in c["payload"]["text"]]
    assert len(lock) == 1 and lock[0]["url"].endswith("/hooks/securite")
    assert scheduler.check_audit_chain() is True
    with db() as conn:
        conn.execute("UPDATE audit_log SET actor = 'mallory' WHERE id = 1")
    assert scheduler.check_audit_chain() is False
    assert "ALTÉRÉ" in captured[-1]["payload"]["text"]
    scheduler.check_audit_chain()
    assert sum("ALTÉRÉ" in c["payload"]["text"] for c in captured) == 1  # throttled


def test_webhook_test_button_hits_every_channel(captured: List[Dict[str, Any]], client_as: Any) -> None:
    result = client_as("admin").post("/api/v1/settings/keys/test/webhook").json()
    assert result["ok"] is True
    assert sorted(c["url"].rsplit("/", 1)[-1] for c in captured if "Test du canal" in c["payload"]["text"]) == sorted(notify.CHANNELS)
