"""Rules page: built-in rules explained and switchable, custom rules validated, tested, run and explained."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import pytest

from app.config import get_settings
from app.soc import custom_rules, incidents, rule_catalog, rules
from app.sources import ais_store

NOW = datetime.datetime.now(datetime.timezone.utc)


def iso(minutes_ago: float) -> str:
    return (NOW - datetime.timedelta(minutes=minutes_ago)).isoformat()


def eez(_lon: float, _lat: float) -> str:
    return "eez"


def territorial(_lon: float, _lat: float) -> str:
    return "territorial"


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    sent: List[str] = []
    monkeypatch.setattr("app.soc.notify.publish", lambda channel, text, **_kw: sent.append(channel) or True)
    return sent


def _fleet() -> None:
    ais_store.store_static("710123456", name="PESCADOR", ship_type=30)       # Brazilian fisher, slow
    ais_store.store_static("636000001", name="CARGO ONE", ship_type=70)      # Liberian cargo
    ais_store.store_positions([
        {"mmsi": "710123456", "ts": iso(5), "lat": 5.5, "lon": -52.5, "sog": 3.0, "cog": 90},
        {"mmsi": "636000001", "ts": iso(5), "lat": 6.5, "lon": -51.0, "sog": 12.0, "cog": 90},
    ], zone_of=territorial)


def _incident_explanation(kind: str) -> Dict[str, Any]:
    [incident] = [i for i in incidents.list_incidents() if i["kind"] == kind]
    created = next(e for e in incidents.get_incident(incident["id"])["events"] if e["kind"] == "created")
    return created["payload"]["explanation"]


def test_builtin_catalogue_is_complete_and_explained() -> None:
    described = {r["id"]: r for r in rule_catalog.describe()}
    assert set(described) == {"dark_vessel", "foreign_fishing", "watchlist_ais", "sanctions", "mpa_fishing", "ais_gap", "encounter"}
    for rule in described.values():
        assert rule["summary"] and rule["why"] and rule["conditions"] and rule["outcome"] and rule["limits"]
        assert rule["performance"]["opened"] == 0
    assert described["foreign_fishing"]["ready"] is False and described["foreign_fishing"]["missing_sources"]
    params = {p["key"]: p for p in described["encounter"]["parameters"]}
    assert params["encounter_distance_km"]["value"] == get_settings().encounter_distance_km and params["encounter_distance_km"]["what"]


def test_builtin_rule_can_be_switched_off_and_explains_itself() -> None:
    _fleet()
    rule_catalog.set_enabled("foreign_fishing", False)
    assert rules.run_rules()["foreign_fishing"] == 0
    rule_catalog.set_enabled("foreign_fishing", True)
    assert rules.run_rules()["foreign_fishing"] == 1
    why = _incident_explanation("foreign_fishing")
    assert why["rule"] == "foreign_fishing" and all(c["ok"] for c in why["checks"])
    assert any(c["observed"] == "Brésil" for c in why["checks"])
    with pytest.raises(ValueError):
        rule_catalog.set_enabled("dark_vessel", False)


SLOW_FOREIGN_FISHER = {
    "conditions": [
        {"field": "fishing", "op": "is", "value": True},
        {"field": "foreign", "op": "is", "value": True},
        {"field": "speed", "op": "lt", "value": 5},
        {"field": "zone", "op": "in", "value": ["territorial"]},
    ],
    "risk": 80, "channel": "alertes", "cooldown_hours": 12,
}


def test_validation_messages_are_understandable() -> None:
    cases = [
        ({"conditions": []}, "au moins une condition"),
        ({"conditions": [{"field": "rm -rf", "op": "is", "value": True}]}, "critère inconnu"),
        ({"conditions": [{"field": "speed", "op": "is", "value": 3}]}, "opérateur invalide"),
        ({"conditions": [{"field": "speed", "op": "lt", "value": 900}]}, "entre 0 et 60 nœuds"),
        ({"conditions": [{"field": "flag", "op": "in", "value": ["Atlantide"]}]}, "valeur inconnue"),
        ({"conditions": [{"field": "area", "op": "inside", "value": {"lat": 5, "lon": -52, "radius_km": 0}}]}, "rayon"),
        ({**SLOW_FOREIGN_FISHER, "risk": 0}, "risque"),
        ({**SLOW_FOREIGN_FISHER, "channel": "twitter"}, "Canal"),
    ]
    for definition, message in cases:
        with pytest.raises(custom_rules.RuleError, match=message):
            custom_rules.validate(definition)


def test_sentence_reads_like_french() -> None:
    text = custom_rules.sentence(custom_rules.validate(SLOW_FOREIGN_FISHER))
    assert text.startswith("Si un navire est un navire de pêche et est sous pavillon étranger et navigue à moins de 5 nœuds")
    assert "incident P1 (risque 80)" in text and "toutes les 12 h" in text


def test_preview_shows_matches_and_near_misses() -> None:
    _fleet()
    result = custom_rules.preview(SLOW_FOREIGN_FISHER)
    assert result["evaluated"] == 2 and result["match_count"] == 1
    [match] = result["matches"]
    assert match["name"] == "PESCADOR" and [c["observed"] for c in match["checks"]][2] == "3.0 nd"
    circle = {"conditions": [{"field": "area", "op": "inside", "value": {"lat": 6.5, "lon": -51.0, "radius_km": 5}}]}
    [inside] = custom_rules.preview(circle)["matches"]
    assert inside["name"] == "CARGO ONE" and inside["checks"][0]["observed"] == "à 0.0 km du centre"
    past = custom_rules.preview(SLOW_FOREIGN_FISHER, at=NOW - datetime.timedelta(hours=2))
    assert past["mode"] == "replay" and past["match_count"] == 0  # the fleet was not there yet


def test_custom_rule_runs_with_cooldown_and_explanation() -> None:
    _fleet()
    rule = custom_rules.create({"name": "Pêcheur lent étranger côtier", "definition": SLOW_FOREIGN_FISHER}, actor="sup")
    result = rules.run_rules()
    assert result[f"custom_{rule['id']}"] == 1
    assert rules.run_rules()[f"custom_{rule['id']}"] == 0  # cooldown
    why = _incident_explanation(rule["kind"])
    assert why["label"] == "Pêcheur lent étranger côtier" and len(why["checks"]) == 4
    [listed] = custom_rules.list_rules()
    assert listed["performance"]["opened"] == 1
    custom_rules.update(rule["id"], {"enabled": False}, actor="sup")
    assert f"custom_{rule['id']}" not in rules.run_rules()


def test_rules_api_roles(client_as: Any) -> None:
    _fleet()
    viewer, analyst, supervisor = client_as("viewer"), client_as("analyst"), client_as("supervisor")
    page = viewer.get("/api/v1/rules").json()
    assert len(page["builtin"]) == 7 and page["builder"]["fields"]
    body = {"name": "Test", "definition": SLOW_FOREIGN_FISHER}
    assert viewer.post("/api/v1/rules/preview", json={"definition": SLOW_FOREIGN_FISHER}).status_code == 403
    assert analyst.post("/api/v1/rules/preview", json={"definition": SLOW_FOREIGN_FISHER}).json()["match_count"] == 1
    assert analyst.post("/api/v1/rules/custom", json=body).status_code == 403
    created = supervisor.post("/api/v1/rules/custom", json=body)
    assert created.status_code == 201
    rule_id = created.json()["id"]
    bad = supervisor.put(f"/api/v1/rules/custom/{rule_id}", json={"definition": {"conditions": []}})
    assert bad.status_code == 422 and "condition" in bad.json()["detail"]
    assert supervisor.put("/api/v1/rules/builtin/encounter", json={"enabled": False}).json()["enabled"] is False
    assert supervisor.put("/api/v1/rules/builtin/dark_vessel", json={"enabled": False}).status_code == 400
    assert supervisor.delete(f"/api/v1/rules/custom/{rule_id}").status_code == 204
    assert supervisor.delete(f"/api/v1/rules/custom/{rule_id}").status_code == 404


EITHER = {
    "groups": [
        [{"field": "fishing", "op": "is", "value": True}, {"field": "foreign", "op": "is", "value": True}],
        [{"field": "name", "op": "contains", "value": "CARGO"}],
    ],
    "risk": 60,
}


def test_or_groups_sentence_and_preview() -> None:
    _fleet()
    clean = custom_rules.validate(EITHER)
    text = custom_rules.sentence(clean)
    assert "(est un navire de pêche et est sous pavillon étranger) ou (a un nom contenant « CARGO »)" in text
    result = custom_rules.preview(EITHER)
    assert sorted(m["name"] for m in result["matches"]) == ["CARGO ONE", "PESCADOR"]
    cargo = next(m for m in result["matches"] if m["name"] == "CARGO ONE")
    assert [c["condition"] for c in cargo["checks"]] == ["a un nom contenant « CARGO »"]  # the group that matched
    with pytest.raises(custom_rules.RuleError, match="Groupe 2, condition 1"):
        custom_rules.validate({"groups": [EITHER["groups"][0], [{"field": "speed", "op": "lt", "value": 99}]]})
    with pytest.raises(custom_rules.RuleError, match="groupes"):
        custom_rules.validate({"groups": [EITHER["groups"][1]] * 6})
    # Legacy single-list definitions still validate (stored rules created before "or").
    assert custom_rules.validate(SLOW_FOREIGN_FISHER)["groups"][0][0]["field"] == "fishing"
