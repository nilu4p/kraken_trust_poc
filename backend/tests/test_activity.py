"""EEZ activity report (GFW area report), port visits, foreign effort and isobaths."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import numpy as np
import pytest

from app.config import get_settings
from app.sources import erddap, gfw, gfw_4wings

START, END = datetime.date(2026, 7, 1), datetime.date(2026, 9, 29)

DAILY = [
    {"date": "2026-07-01", "flag": "FRA", "hours": 10},
    {"date": "2026-07-01", "flag": "BRA", "hours": 30},
    {"date": "2026-07-02", "flag": "SUR", "hours": 20},
    {"date": "2026-07-02", "flag": "", "hours": 5},
    {"date": "2026-07-02", "flag": "VEN", "hours": 0},
]
GEARS = [{"geartype": "set_gillnets", "hours": 40}, {"geartype": "trawlers", "hours": 25}]
VESSELS = [
    {"vesselId": "a1", "mmsi": 710000001, "shipName": "PESCADOR", "flag": "BRA", "geartype": "set_gillnets", "hours": 30},
    {"vesselId": "f1", "mmsi": 227000001, "shipName": "CAYENNE", "flag": "FRA", "geartype": "trawlers", "hours": 10},
    {"vesselId": "s1", "mmsi": 765000001, "shipName": "PARAMARIBO", "flag": "SUR", "geartype": "trawlers", "hours": 20},
]


@pytest.fixture()
def gfw_reports(monkeypatch: pytest.MonkeyPatch) -> List[Dict[str, Any]]:
    calls: List[Dict[str, Any]] = []

    def fake(dataset, group_by, resolution, temporal, start, end, cfg, aggregate=False):
        calls.append({"group_by": group_by, "temporal": temporal, "aggregate": aggregate})
        return {"FLAG": DAILY, "GEARTYPE": GEARS, "VESSEL_ID": VESSELS}[group_by]

    monkeypatch.setattr(get_settings(), "gfw_token", "token")
    monkeypatch.setattr(gfw_4wings, "_report_range", fake)
    return calls


def test_activity_report_totals_by_flag_gear_vessel(gfw_reports: List[Dict[str, Any]]) -> None:
    report = gfw_4wings.activity_report(START, END)
    assert report["available"] and report["resolution"] == "daily"
    assert all(c["aggregate"] for c in gfw_reports)  # whole-zone sums, no grid cells
    assert report["total_hours"] == 65 and report["foreign_hours"] == 50
    assert report["foreign_share"] == pytest.approx(76.9)
    assert [f["code"] for f in report["by_flag"]] == ["BRA", "SUR", "FRA", None]
    assert report["series"] == [
        {"date": "2026-07-01", "french": 10, "foreign": 30, "unknown": 0},
        {"date": "2026-07-02", "french": 0, "foreign": 20, "unknown": 5},
    ]
    assert report["by_gear"][0]["label"] == "Fileyeurs"
    assert [v["name"] for v in report["vessels"]] == ["PESCADOR", "PARAMARIBO", "CAYENNE"]
    assert report["vessel_count"] == 3 and report["foreign_vessel_count"] == 2
    assert not report["vessels"][2]["foreign"]


def test_activity_report_long_period_is_monthly(gfw_reports: List[Dict[str, Any]]) -> None:
    report = gfw_4wings.activity_report(datetime.date(2025, 10, 1), END)
    assert report["resolution"] == "monthly" and gfw_reports[0]["temporal"] == "MONTHLY"


def test_activity_report_without_token() -> None:
    report = gfw_4wings.activity_report(START, END)
    assert not report["available"] and report["reason"] == "GFW_TOKEN non configuré"


def test_port_visit_parsing_keeps_stops_only() -> None:
    entry = {"id": "pv1", "start": "2026-08-01T10:00:00Z", "end": "2026-08-02T10:00:00Z",
             "position": {"lat": 4.85, "lon": -52.26},
             "vessel": {"name": "PESCADOR", "ssvid": "710000001", "flag": "BRA"},
             "port_visit": {"confidence": 4, "durationHrs": 24.0,
                            "startAnchorage": {"name": "DEGRAD DES CANNES", "topDestination": "CAYENNE", "flag": "GUF"}}}
    feature = gfw.parse_event(entry, "port_visit")
    assert feature["properties"]["port_name"] == "CAYENNE" and feature["properties"]["port_hours"] == 24.0
    assert gfw.parse_event({**entry, "port_visit": {**entry["port_visit"], "confidence": 2}}, "port_visit") is None


def test_activity_endpoint_adds_ports_and_validates_period(client_as, gfw_reports, monkeypatch: pytest.MonkeyPatch) -> None:
    visit = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-52.26, 4.85]},
             "properties": {"port_name": "CAYENNE", "flag_code": "BRA", "mmsi": "710000001"}}
    monkeypatch.setattr(gfw, "port_visits", lambda start, end, settings=None: [visit, visit])
    client = client_as("viewer")
    body = client.get("/api/v1/maritime/activity-report", params={"start": "2026-07-01", "end": "2026-09-29"}).json()
    assert body["ports"] == [{"port": "CAYENNE", "visits": 2, "foreign_visits": 2, "vessels": 1}]
    assert body["vessels"][0]["watchlist"] is None
    assert client.get("/api/v1/maritime/activity-report", params={"start": "2026-09-29", "end": "2026-07-01"}).status_code == 422
    assert client.get("/api/v1/maritime/activity-report", params={"start": "2024-01-01", "end": "2026-09-29"}).status_code == 422


def test_foreign_effort_cells(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "gfw_token", "token")
    monkeypatch.setattr(gfw_4wings, "_report", lambda *a, **k: [
        {"lat": 5.5, "lon": -52.0, "flag": "BRA", "hours": 3}, {"lat": 5.5, "lon": -52.0, "flag": "FRA", "hours": 2}])
    cells = gfw_4wings.fishing_effort()["features"]
    assert [c["properties"]["foreign"] for c in cells] == [True, False]


def test_isobath_contours(monkeypatch: pytest.MonkeyPatch) -> None:
    lats = np.array([4.0, 4.1, 4.2])
    lons = np.array([-52.0, -51.9, -51.8])
    altitude = np.array([[-10, -150, -300], [-10, -150, -300], [-10, -150, -300]], dtype=np.int16)
    monkeypatch.setattr(erddap, "_load_grid", lambda cfg: (lats, lons, altitude))
    erddap._isobaths.clear()
    data = erddap.isobaths()
    depths = {f["properties"]["depth_m"]: f["geometry"]["coordinates"] for f in data["features"]}
    assert set(depths) == {20, 50, 100, 200}  # nothing deeper than 300 m in this grid
    for a, b in depths[200]:  # the 200 m line sits 1/3 of the way between -51.9 (150 m) and -51.8 (300 m)
        assert a[0] == pytest.approx(-51.8667, abs=1e-3) and b[0] == pytest.approx(-51.8667, abs=1e-3)
    assert len(depths[200]) == 2
