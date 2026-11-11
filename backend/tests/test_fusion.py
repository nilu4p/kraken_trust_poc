"""Multi-source fusion: AISHub, VMS, Copernicus Marine, WDPA, OpenSanctions, GFW Insights."""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List

import httpx
import pytest

from app.config import get_settings
from app.db import db
from app.pipeline.detection import load_ais_tracks
from app.pipeline.risk import score_vessel
from app.soc import catalogue, incidents, rules
from app.sources import aishub, ais_store, cmems, gfw_4wings, opensanctions, vms, wdpa

NOW = datetime.datetime.now(datetime.timezone.utc)

SQUARE = {"type": "Polygon", "coordinates": [[[-52.6, 5.4], [-52.4, 5.4], [-52.4, 5.6], [-52.6, 5.6], [-52.6, 5.4]]]}
MPA_LAYER = {"features": [
    {"type": "Feature", "geometry": SQUARE,
     "properties": {"site_id": 147302, "name": "Ile Du Grand-Connétable", "desig": "Réserve naturelle nationale",
                    "iucn_cat": "IV", "realm": "Marine", "no_take": "Not Reported", "rep_m_area": 78.5, "iso3": "GUF"}},
    {"type": "Feature", "geometry": SQUARE,
     "properties": {"site_id": 1, "name": "Forêt", "realm": "Terrestrial", "rep_m_area": 0}},
]}


def eez(_lon: float, _lat: float) -> str:
    return "eez"


def response(url: str, payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", url), json=payload)


@pytest.fixture(autouse=True)
def quiet_notifications(monkeypatch: pytest.MonkeyPatch) -> List[str]:
    sent: List[str] = []
    monkeypatch.setattr("app.soc.notify.publish", lambda channel, text, **_kw: sent.append(f"{channel}|{text}") or True)
    return sent


# --------------------------------------------------------------------------- AISHub


AISHUB_PAYLOAD = [
    {"ERROR": False, "USERNAME": "u", "FORMAT": "HUMAN", "RECORDS": 2},
    [
        {"MMSI": 710123456, "TIME": (NOW - datetime.timedelta(minutes=3)).strftime("%Y-%m-%d %H:%M:%S GMT"),
         "LONGITUDE": -52.5, "LATITUDE": 5.5, "COG": 90.0, "SOG": 3.1, "HEADING": 511, "NAVSTAT": 7,
         "IMO": 0, "NAME": "PESCADOR", "CALLSIGN": "PP1", "TYPE": 30},
        {"MMSI": 765000001, "TIME": "2026-10-20 10:00:00 GMT", "LONGITUDE": 181, "LATITUDE": 91, "NAME": "NOPOS", "TYPE": 70},
    ],
]


def test_aishub_parse_and_errors() -> None:
    positions, statics = aishub.parse_response(AISHUB_PAYLOAD)
    assert len(positions) == 1 and positions[0]["source"] == "aishub" and positions[0]["heading"] is None
    assert positions[0]["ts"].endswith("+00:00") and positions[0]["nav_status"] == 7
    assert {s["mmsi"] for s in statics} == {"710123456", "765000001"}
    with pytest.raises(aishub.AisHubError, match="Invalid username"):
        aishub.parse_response([{"ERROR": True, "ERROR_MESSAGE": "Invalid username or password!"}])


def test_aishub_poll_fuses_with_aisstream(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "aishub_username", "member")
    monkeypatch.setattr(aishub.httpx, "get", lambda url, **_kw: response(url, AISHUB_PAYLOAD))
    ts = aishub.parse_response(AISHUB_PAYLOAD)[0][0]["ts"]
    # Same fix already heard by aisstream: stored once.
    ais_store.store_positions([{"mmsi": "710123456", "ts": ts, "lat": 5.5, "lon": -52.5, "sog": 3.1, "cog": 90}], zone_of=eez)
    assert aishub.poll_once(eez) == 1
    ais_store.store_positions([{"mmsi": "227000009", "ts": ts, "lat": 5.0, "lon": -52.0}], zone_of=eez, source="aishub")
    with db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM ais_positions WHERE mmsi = '710123456'").fetchone()[0] == 1
        vessel = dict(conn.execute("SELECT * FROM ais_vessels WHERE mmsi = '710123456'").fetchone())
    assert vessel["call_sign"] == "PP1" and vessel["ship_type"] == 30
    assert set(ais_store.stats()["by_source"]) == {"aisstream", "aishub"}
    # The aisstream collector was listening around the pass (logged by the real collector).
    from app.sources import feed_log

    feed_log.mark_span("aisstream", NOW - datetime.timedelta(hours=1), NOW + datetime.timedelta(minutes=31))
    _tracks, label = load_ais_tracks(get_settings(), NOW.isoformat())
    assert label == "AISHub + aisstream.io (historique)"


def test_older_fix_from_slower_feed_does_not_move_vessel() -> None:
    ais_store.store_positions([{"mmsi": "227000010", "ts": NOW.isoformat(), "lat": 6.0, "lon": -52.0}], zone_of=eez)
    late = (NOW - datetime.timedelta(minutes=30)).isoformat()
    ais_store.store_positions([{"mmsi": "227000010", "ts": late, "lat": 5.0, "lon": -53.0}], zone_of=eez, source="aishub")
    [vessel] = ais_store.vessels(1)
    assert vessel["last_lat"] == 6.0 and vessel["last_source"] == "aisstream"


# --------------------------------------------------------------------------- VMS

NAF = (
    "//SR//AD/FRA//FR/VEN//TM/POS//RC/YYV1234//NA/DON PEDRO//FS/VEN//LT/+5.512//LG/-052.430//"
    "SP/35//CO/270//DA/{date}//TI/{time}//ER//"
)


def test_vms_naf_and_csv_parsing() -> None:
    stamp = NOW - datetime.timedelta(minutes=20)
    fmt, positions = vms.parse(NAF.format(date=stamp.strftime("%Y%m%d"), time=stamp.strftime("%H%M")).encode())
    assert fmt == "naf" and len(positions) == 1
    p = positions[0]
    assert p["mmsi"] == "VMS-YYV1234" and p["flag"] == "Venezuela" and p["sog"] == 3.5 and p["cog"] == 270.0
    csv_text = "Date heure;Latitude;Longitude;Vitesse;Cap;Indicatif radio;Nom navire;Pavillon\n" \
               "2026-10-20 10:00:00;5,5;-52,4;4,2;180;YYV9;LA PERLA;VEN\n;;;;;;;\n"
    fmt, positions = vms.parse(csv_text.encode())
    assert fmt == "csv" and positions[0]["lat"] == 5.5 and positions[0]["mmsi"] == "VMS-YYV9"
    with pytest.raises(vms.VmsFormatError, match="latitude"):
        vms.parse(b"foo,bar\n1,2\n")


def test_vms_licensed_vessel_not_raised_as_foreign_fishing_and_explains_radar() -> None:
    stamp = NOW - datetime.timedelta(minutes=10)
    report = vms.import_positions(NAF.format(date=stamp.strftime("%Y%m%d"), time=stamp.strftime("%H%M")).encode(), zone_of=eez)
    assert report["positions"] == 1 and report["vessels"] == 1
    result = rules.run_rules()
    assert result["foreign_fishing"] == 0  # licensed by VMS
    tracks, label = load_ais_tracks(get_settings(), NOW.isoformat())
    assert label == "VMS (historique)" and tracks[0]["mmsi"] == "VMS-YYV1234"


# --------------------------------------------------------------------------- Copernicus Marine


def _cmems_get(url: str, params: Dict[str, Any], **_kw: Any) -> httpx.Response:
    layer = params["LAYER"]
    x = int(params["TILECOL"]) * 256 + int(params["I"])  # global pixel column at zoom 9 (≈ 300 m)
    if "sea_water_velocity" in layer:
        props = {"value": 1.0, "component1Value": 0.0, "component2Value": 1.0, "units": "m s-1"}
    elif "analysed_sst" in layer:
        # Temperature rising eastward by 0.02 °C per pixel (pixel ≈ 0.304 km → 0.66 °C / 10 km): a thermal front.
        props = {"value": 300.0 + 0.02 * (x - 46000), "units": "kelvin"}
    else:
        props = {"value": 1.4, "units": "milligram m-3"}
    return response(url, {"type": "FeatureCollection", "features": [{"properties": props}]})


def test_cmems_point_values_front_and_current(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmems.httpx, "get", _cmems_get)
    ocean = cmems.ocean_point(5.5, -52.5)
    assert ocean["current_speed_kn"] == pytest.approx(1.94, abs=0.01) and ocean["current_towards_deg"] == 0
    assert ocean["chlorophyll_mg_m3"] == 1.4 and ocean["sst_gradient_c_10km"] == pytest.approx(0.66, abs=0.02)
    assert ocean["thermal_front"] is True
    assert ocean["source"].startswith("Copernicus Marine")
    layers = cmems.tile_layers()
    assert set(layers) == {"sst", "chl", "current"} and "{z}" in layers["sst"]["url"]


def test_cmems_land_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmems.httpx, "get", lambda url, **_kw: response(url, {"features": [{"properties": {"value": None}}]}))
    assert cmems.ocean_point(4.5, -53.0) is None


def test_risk_uses_front_and_mpa() -> None:
    vessel = {"lat": 5.5, "lon": -52.5, "confidence": 0.8,
              "ocean": {"thermal_front": True, "sst_gradient_c_10km": 0.8},
              "mpa": {"name": "Ile Du Grand-Connétable", "designation": "Réserve naturelle nationale"}}
    risk = score_vessel(vessel, zone="territorial", events=[], ais_source="aisstream.io")
    assert any("Front thermique" in r for r in risk["reasons"]) and any("Grand-Connétable" in r for r in risk["reasons"])


# --------------------------------------------------------------------------- WDPA


def test_wdpa_keeps_marine_sites_and_mpa_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wdpa, "get_json", lambda *_a, **_kw: MPA_LAYER)
    areas = wdpa.protected_areas()
    assert [f["properties"]["name"] for f in areas["features"]] == ["Ile Du Grand-Connétable"]
    assert wdpa.MpaIndex(areas).find(5.5, -52.5)["site_id"] == 147302
    assert wdpa.MpaIndex(areas).find(6.5, -52.5) is None
    ais_store.store_static("227000011", name="CHALUTIER", ship_type=30)
    ais_store.store_positions([{"mmsi": "227000011", "ts": NOW.isoformat(), "lat": 5.5, "lon": -52.5, "sog": 2.5}], zone_of=eez)
    assert rules.run_rules()["mpa_fishing"] == 1
    [incident] = incidents.list_incidents()
    assert incident["kind"] == "mpa_fishing" and "Grand-Connétable" in incident["title"]


# --------------------------------------------------------------------------- OpenSanctions + GFW Insights


def test_opensanctions_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "opensanctions_api_key", "key")
    seen: Dict[str, Any] = {}

    def post(url: str, json: Dict[str, Any], headers: Dict[str, str], **_kw: Any) -> httpx.Response:
        seen.update(body=json, auth=headers["Authorization"])
        results = [
            {"id": "ofac-1", "caption": "SEA SHADOW", "score": 0.93, "datasets": ["us_ofac_sdn"],
             "properties": {"imoNumber": ["9216834"], "topics": ["sanction"]}},
            {"id": "x", "caption": "SEA LION", "score": 0.4, "datasets": ["other"], "properties": {}},
        ]
        return httpx.Response(200, request=httpx.Request("POST", url), json={"responses": {"q": {"results": results}}})

    monkeypatch.setattr(opensanctions.httpx, "post", post)
    matches = opensanctions.screen_vessel(name="SEA SHADOW", imo=9216834, mmsi="636012345")
    assert [m["caption"] for m in matches] == ["SEA SHADOW"] and matches[0]["imo"] == "9216834"
    assert seen["auth"] == "ApiKey key" and seen["body"]["queries"]["q"]["schema"] == "Vessel"
    assert opensanctions.screen_vessel(name="AIS") == []  # nothing identifying: no call


def test_intel_rule_sanctions_and_gfw_iuu(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = get_settings()
    monkeypatch.setattr(cfg, "opensanctions_api_key", "key")
    monkeypatch.setattr(cfg, "gfw_token", "token")
    monkeypatch.setattr("app.sources.opensanctions.screen_vessel",
                        lambda **_kw: [{"caption": "SEA SHADOW", "score": 0.95, "datasets": ["us_ofac_sdn"]}])
    monkeypatch.setattr("app.sources.gfw_4wings.vessel_identity", lambda mmsi, cfg=None: {"gfw_id": "abc", "flag": "PAN"})
    monkeypatch.setattr("app.sources.gfw_4wings.vessel_insights",
                        lambda gid, cfg=None: {"iuu_listed": True, "iuu_times_listed": 2, "ais_off_events": 4})
    ais_store.store_positions([{"mmsi": "351000001", "ts": NOW.isoformat(), "lat": 6.0, "lon": -52.0, "sog": 9}], zone_of=eez)
    result = rules.run_rules()
    assert result["sanctions"] == 1 and result["iuu_gfw"] == 1
    kinds = sorted(i["kind"] for i in incidents.list_incidents())
    assert kinds == ["sanctions", "watchlist_ais"]
    with db() as conn:
        intel = json.loads(conn.execute("SELECT intel FROM ais_vessels WHERE mmsi = '351000001'").fetchone()[0])
    assert intel["gfw"]["iuu_listed"] and intel["sanctions"][0]["caption"] == "SEA SHADOW"
    # Re-checked only after intel_refresh_days: no new lookups, no duplicate incidents.
    assert rules.run_rules()["sanctions"] == 0


def test_gfw_insights_parsing() -> None:
    parsed = gfw_4wings.parse_insights({
        "apparentFishing": {"eventsInNoTakeMpas": ["e1", "e2"], "eventsInRfmoWithoutKnownAuthorization": [],
                            "periodSelectedCounters": {"events": 10}},
        "gaps": {"periodSelectedCounters": {"events": 5, "eventsGapOff": 3}, "aisOff": ["g1"]},
        "coverage": {"percentage": 82.5},
        "vesselIdentity": {"iuuVesselList": {"totalTimesListed": 1, "totalTimesListedInThePeriod": 0},
                           "flagsChanges": {"totalTimesListedInThePeriod": 2}},
    })
    assert parsed == {"iuu_listed": True, "iuu_times_listed": 1, "fishing_in_no_take_mpa": 2,
                      "fishing_without_rfmo_authorization": 0, "ais_off_events": 3, "flag_changes": 2,
                      "ais_coverage_pct": 82.5}


# --------------------------------------------------------------------------- API & catalogue


def test_fusion_endpoints(client_as: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wdpa, "get_json", lambda *_a, **_kw: MPA_LAYER)
    ais_store.store_static("710123456", name="PESCADOR", ship_type=30)
    ais_store.store_positions([{"mmsi": "710123456", "ts": NOW.isoformat(), "lat": 5.5, "lon": -52.5, "sog": 3}], zone_of=eez)
    viewer = client_as("viewer")
    profile = viewer.get("/api/v1/maritime/vessel/710123456").json()
    assert profile["vessel"]["name"] == "PESCADOR" and profile["category"] == "foreign_fishing"
    assert profile["feeds"]["aisstream"]["positions_24h"] == 1 and profile["licensed_vms"] is False
    assert viewer.get("/api/v1/maritime/vessel/000").status_code == 404
    assert viewer.get("/api/v1/maritime/vessel/..%2Fx").status_code in (404, 422)
    assert len(viewer.get("/api/v1/maritime/mpa").json()["features"]) == 1
    assert "sst" in viewer.get("/api/v1/maritime/ocean-layers").json()["layers"]
    coverage = viewer.get("/api/v1/maritime/coverage").json()
    assert coverage["ais"]["feeds"]["aishub"] is False and coverage["fusion"]["wdpa"] is True

    stamp = NOW - datetime.timedelta(minutes=5)
    body = NAF.format(date=stamp.strftime("%Y%m%d"), time=stamp.strftime("%H%M")).encode()
    headers = {"Content-Type": "application/octet-stream"}
    assert client_as("analyst").post("/api/v1/maritime/vms/import", content=body, headers=headers).status_code == 403
    supervisor = client_as("supervisor")
    report = supervisor.post("/api/v1/maritime/vms/import", content=body, headers=headers).json()
    assert report["format"] == "naf" and report["vessels"] == 1
    bad = supervisor.post("/api/v1/maritime/vms/import", content=b"a,b\n1,2\n", headers=headers)
    assert bad.status_code == 422


def test_catalogue_lists_new_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    status = {s["id"]: s["status"] for s in catalogue.describe()}
    assert status["cmems"] == "active" and status["wdpa"] == "active" and status["vms"] == "active"
    assert status["aishub"] == "needs_key" and status["opensanctions"] == "needs_key"
    monkeypatch.setattr(get_settings(), "opensanctions_api_key", "k")
    assert {s["id"]: s["status"] for s in catalogue.describe()}["opensanctions"] == "active"
