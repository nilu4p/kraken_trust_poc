"""Surveillance zones drawn on the map, used by custom rules."""

from __future__ import annotations

import datetime
from typing import Any

import pytest

from app.soc import custom_rules, zones
from app.sources import ais_store

NOW = datetime.datetime.now(datetime.timezone.utc)
SQUARE = {"type": "Polygon", "coordinates": [[[-52.6, 5.4], [-52.4, 5.4], [-52.4, 5.6], [-52.6, 5.6]]]}


def eez(_lon: float, _lat: float) -> str:
    return "eez"


def test_geometry_validation() -> None:
    clean = zones.validate_geometry(SQUARE)
    assert clean["coordinates"][0][0] == clean["coordinates"][0][-1]  # ring closed
    bowtie = {"type": "Polygon", "coordinates": [[[-52.6, 5.4], [-52.4, 5.6], [-52.4, 5.4], [-52.6, 5.6]]]}
    with pytest.raises(zones.ZoneError, match="croisent"):
        zones.validate_geometry(bowtie)
    with pytest.raises(zones.ZoneError, match="sommets"):
        zones.validate_geometry({"type": "Polygon", "coordinates": [[[-52.6, 5.4], [-52.4, 5.4]]]})
    with pytest.raises(zones.ZoneError, match="petite"):
        zones.validate_geometry({"type": "Polygon", "coordinates": [[[-52.6, 5.4], [-52.59999, 5.4], [-52.59999, 5.40001]]]})


def test_zone_in_custom_rule_and_protected_delete() -> None:
    zone = zones.create({"name": "Estuaire", "geometry": SQUARE}, actor="sup")
    ais_store.store_static("710000001", name="DEDANS", ship_type=30)
    ais_store.store_static("710000002", name="DEHORS", ship_type=30)
    ais_store.store_positions([
        {"mmsi": "710000001", "ts": NOW.isoformat(), "lat": 5.5, "lon": -52.5},
        {"mmsi": "710000002", "ts": NOW.isoformat(), "lat": 6.5, "lon": -51.0},
    ], zone_of=eez)
    definition = {"conditions": [{"field": "drawn_zone", "op": "in", "value": [zone["id"]]}], "risk": 70}
    assert "se trouve dans la zone « Estuaire »" in custom_rules.sentence(custom_rules.validate(definition))
    [match] = custom_rules.preview(definition)["matches"]
    assert match["name"] == "DEDANS" and match["checks"][0]["observed"] == "Estuaire"
    with pytest.raises(custom_rules.RuleError, match="zone inexistante"):
        custom_rules.validate({"conditions": [{"field": "drawn_zone", "op": "in", "value": [999]}]})
    rule = custom_rules.create({"name": "Pêche estuaire", "definition": definition}, actor="sup")
    with pytest.raises(zones.ZoneError, match="Pêche estuaire"):
        zones.delete(zone["id"], actor="sup")
    custom_rules.update(rule["id"], {"enabled": False}, actor="sup")
    zones.delete(zone["id"], actor="sup")  # no enabled rule uses it any more
    assert zones.list_zones() == []


def test_zones_api(client_as: Any) -> None:
    viewer, supervisor = client_as("viewer"), client_as("supervisor")
    assert viewer.post("/api/v1/zones", json={"name": "Z", "geometry": SQUARE}).status_code == 403
    created = supervisor.post("/api/v1/zones", json={"name": "Chenal", "geometry": SQUARE, "color": "#f59e0b"})
    assert created.status_code == 201
    listed = viewer.get("/api/v1/zones").json()
    assert listed["features"][0]["properties"]["name"] == "Chenal" and listed["features"][0]["properties"]["used_by"] == []
    zone_id = created.json()["id"]
    assert supervisor.put(f"/api/v1/zones/{zone_id}", json={"name": "Chenal Nord"}).json()["name"] == "Chenal Nord"
    assert supervisor.put(f"/api/v1/zones/{zone_id}", json={"color": "rouge"}).status_code == 422
    fields = {f["id"]: f for f in viewer.get("/api/v1/rules").json()["builder"]["fields"]}
    assert fields["drawn_zone"]["options"] == [[zone_id, "Chenal Nord"]]
    assert supervisor.delete(f"/api/v1/zones/{zone_id}").status_code == 204
