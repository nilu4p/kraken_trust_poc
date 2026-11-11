"""Watchlist (IUU), CNES GEODES, SHOM fishing limits, NOAA ocean context, catalogue."""

from __future__ import annotations

import io
import zipfile
from typing import Any, Dict, List

import httpx
import pytest

from app.config import get_settings
from app.pipeline.orchestrator import _flag_watchlisted_ais, _open_watchlist_incidents
from app.pipeline.quality import assess
from app.pipeline.risk import score_vessel
from app.soc import incidents, watchlist
from app.sources import erddap, geodes, shom
from app.sources.http import cache_write
from app.sources.optical import geodes_feature

SQUARE = {"type": "Polygon", "coordinates": [[[-53.0, 5.0], [-52.0, 5.0], [-52.0, 6.0], [-53.0, 6.0], [-53.0, 5.0]]]}


def _xlsx(rows: List[List[str]]) -> bytes:
    """Minimal XLSX (shared strings) built like Excel does."""
    strings: List[str] = []
    xml_rows = []
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            strings.append(value)
            cells.append(f'<c r="{chr(65 + c)}{r}" t="s"><v>{len(strings) - 1}</v></c>')
        xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{s}</t></si>" for s in strings) + "</sst>")
        archive.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{''.join(xml_rows)}</sheetData></worksheet>")
    return buffer.getvalue()


IUU_ROWS = [
    ["Combined IUU Vessel List", "", "", ""],  # title block before the header, like the TMT file
    ["Vessel Name", "IMO", "Flag", "RFMO"],
    ["Ocean Phantom", "9123456", "Unknown", "ICCAT"],
    ["Black Pearl II", "", "Belize", "IOTC"],
]


def test_parse_xlsx_with_title_block_and_csv() -> None:
    entries = watchlist.parse_table(_xlsx(IUU_ROWS))
    assert [e["name"] for e in entries] == ["Ocean Phantom", "Black Pearl II"]
    assert entries[0]["imo"] == "9123456" and entries[0]["reason"] == "ICCAT"
    csv_entries = watchlist.parse_table("Name;MMSI;Flag\nSea Ghost;227543000;Venezuela\n".encode())
    assert csv_entries == [{"name": "Sea Ghost", "mmsi": "227543000", "imo": "", "call_sign": "", "flag": "Venezuela", "reason": ""}]
    with pytest.raises(ValueError):
        watchlist.parse_table(b"foo,bar\n1,2\n")


def test_watchlist_matching_and_import_replaces_source() -> None:
    watchlist.import_entries(watchlist.parse_table(_xlsx(IUU_ROWS)), source="iuu_combined", actor="t")
    watchlist.add_entry({"mmsi": "227543000", "reason": "Suivi DM"}, actor="t")
    matcher = watchlist.Matcher()
    assert matcher.match(imo="IMO 9123456")["name"] == "Ocean Phantom"
    assert matcher.match(name="black-pearl ii")["matched_on"] == "nom / alias ORGP"
    assert matcher.match(mmsi=227543000)["reason"] == "Suivi DM"
    assert matcher.match(mmsi="111111111", name="Nobody") is None
    watchlist.import_entries([{"name": "Only One"}], source="iuu_combined", actor="t")
    assert watchlist.list_entries()["counts"] == {"iuu_combined": 1, "manual": 1}


def test_listed_ais_vessel_in_eez_opens_incident() -> None:
    watchlist.add_entry({"mmsi": "227543000", "name": "SEA GHOST", "reason": "INN"}, actor="t")
    tracks = [
        {"mmsi": "227543000", "name": "SEA GHOST", "lat": 5.5, "lon": -52.5},
        {"mmsi": "227543000", "name": "SEA GHOST", "lat": 0.0, "lon": 0.0},  # outside the EEZ
        {"mmsi": "999999999", "name": "LEGIT", "lat": 5.5, "lon": -52.4},
    ]
    listed = _flag_watchlisted_ais(tracks, {"eez": SQUARE}, get_settings())
    assert len(listed) == 1 and tracks[0]["watchlist"]["reason"] == "INN" and "watchlist" not in tracks[2]
    _open_watchlist_incidents(listed, "S1X", "2026-10-19T21:34:39+00:00", get_settings())
    [incident] = incidents.list_incidents()
    assert incident["kind"] == "watchlist_ais" and incident["severity"] == "P1" and "SEA GHOST" in incident["title"]


def test_watchlist_api_rbac_and_import(client_as: Any) -> None:
    admin = client_as("admin")
    analyst = client_as("analyst")
    data = _xlsx(IUU_ROWS)
    headers = {"Content-Type": "application/octet-stream"}
    assert analyst.post("/api/v1/watchlist/import", content=data, headers=headers).status_code == 403
    response = admin.post("/api/v1/watchlist/import", content=data, headers=headers)
    assert response.json()["imported"] == 2 and response.json()["source"] == "iuu_combined"
    assert admin.post("/api/v1/watchlist/import", content=b"not a table", headers=headers).status_code == 422
    assert analyst.post("/api/v1/watchlist", json={"mmsi": "123"}).status_code == 422  # invalid MMSI, no name
    entry = analyst.post("/api/v1/watchlist", json={"name": "Suspect", "reason": "observé"}).json()["entry"]
    assert analyst.delete(f"/api/v1/watchlist/{entry['id']}").status_code == 403
    assert admin.delete(f"/api/v1/watchlist/{entry['id']}").status_code == 200
    assert client_as("viewer").get("/api/v1/watchlist").json()["counts"]["iuu_combined"] == 2


def test_bathymetry_drives_quality_and_chlorophyll_risk(monkeypatch: pytest.MonkeyPatch) -> None:
    target = {"lat": 5.5, "lon": -52.5, "snr": 20.0, "pixels": 12, "elongation": 3.0, "dual_pol": True}
    clean = assess(target)["score"]
    land = assess({**target, "ocean": {"on_land": True, "depth_m": 0.0}})
    shoal = assess({**target, "ocean": {"on_land": False, "depth_m": 1.5}})
    assert land["score"] < shoal["score"] < clean
    assert any("terre" in f for f in land["flags"]) and any("Haut-fond" in f for f in shoal["flags"])
    vessel = {"lat": 5.5, "lon": -52.5, "confidence": 0.9, "ocean": {"productive_waters": True, "chlorophyll_mg_m3": 2.4},
              "fishing_limits": {"fishing_3nm": 2.0}, "watchlist": {"source": "manual", "reason": "INN"}}
    risk = score_vessel(vessel, zone="eez", events=[], ais_source="aisstream.io")
    assert any("productives" in r for r in risk["reasons"]) and any("3 milles" in r for r in risk["reasons"])
    assert any("liste de surveillance" in r for r in risk["reasons"])


def test_erddap_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, **_kw: Any) -> httpx.Response:
        request = httpx.Request("GET", url)
        if "etopo" in url:
            table = {"columnNames": ["latitude", "longitude", "altitude"], "rows": [[5.5, -52.3, -46]]}
        else:
            table = {"columnNames": ["time", "altitude", "latitude", "longitude", "chlor_a"], "rows": [["t", 0, 5.5, -52.25, 1.8]]}
        return httpx.Response(200, request=request, json={"table": table})

    monkeypatch.setattr(erddap.httpx, "get", fake_get)
    context = erddap.ocean_context(5.5, -52.3)
    assert context["depth_m"] == 46 and not context["on_land"]
    assert context["chlorophyll_mg_m3"] == 1.8 and context["productive_waters"]


def test_shom_fishing_limits_from_cache() -> None:
    line = {"type": "Feature", "geometry": {"type": "MultiLineString", "coordinates": [[[-53.0, 5.6], [-52.0, 5.6]]]},
            "properties": {"territory": "Guyane", "reference": "R951-14"}}
    cache_write("shom", "fishing_100nm", [line], get_settings())
    cache_write("shom", "fishing_3nm", [], get_settings())
    fc = shom.fishing_limits()
    assert fc["features"][0]["properties"]["limit"] == "fishing_100nm"
    distances = shom.distances_km(5.5, -52.5)
    assert distances["fishing_100nm"] == pytest.approx(11.1, abs=0.3) and distances["fishing_3nm"] is None


def test_geodes_queries_each_collection_and_normalises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Dict[str, Any]] = []

    def fake_post(url: str, json: Dict[str, Any], **_kw: Any) -> httpx.Response:
        calls.append(json)
        item = {
            "id": "URN:X", "collection": json["collections"][0], "geometry": None,
            "properties": {"dataset": json["collections"][0], "platform": "S1C", "product:type": "GRD",
                           "start_datetime": "2026-10-19T09:11:32Z", "datetime": "2026-10-20T18:00:00Z"},
            "assets": {"q": {"href": "https://geodes-portal.cnes.fr/api/quicklook/x", "roles": ["overview"]}},
        }
        return httpx.Response(200, request=httpx.Request("POST", url), json={"features": [item] if "S1" in json["collections"][0] else []})

    monkeypatch.setattr(geodes.httpx, "post", fake_post)
    items = geodes.search(5.6, -52.3, "2026-10-17", "2026-10-22")
    assert len(calls) == len(geodes.COLLECTIONS) and all(len(c["collections"]) == 1 for c in calls)
    from app.sources.optical import _parse_dt

    props = geodes_feature(items[0], _parse_dt("2026-10-19T21:34:39Z"))["properties"]
    assert props["sensor"] == "sar" and props["thumbnail"].startswith("https://geodes-portal.cnes.fr")
    assert props["delta_hours_vs_sar"] == pytest.approx(-12.4, abs=0.1)


def test_catalogue_lists_restricted_sources(client_as: Any) -> None:
    sources = client_as("viewer").get("/api/v1/system/catalogue").json()["sources"]
    by_id = {s["id"]: s for s in sources}
    assert by_id["cnes_geodes"]["status"] == "active"
    assert by_id["copernicus_s1"]["status"] == "needs_key"
    assert by_id["dinamis"]["status"] == "available" and not by_id["dinamis"]["integrated"]
    assert "kineis_cls" in by_id and "vms" in by_id


def _real_xls(rows: List[List[Any]]) -> bytes:
    """Genuine Excel 97-2003 (BIFF) file, numbers stored as numbers like Excel does."""
    xlwt = pytest.importorskip("xlwt")
    book = xlwt.Workbook()
    sheet = book.add_sheet("IUU")
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            sheet.write(r, c, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_parse_real_binary_xls() -> None:
    data = _real_xls([["Combined IUU Vessel List"], ["Vessel Name", "IMO", "Flag", "RFMO"],
                      ["Ocean Phantom", 9123456, "Unknown", "ICCAT"], ["Black Pearl II", "", "Belize", "IOTC"]])
    assert watchlist.detect_format(data) == "xls"
    entries = watchlist.parse_table(data)
    assert [e["name"] for e in entries] == ["Ocean Phantom", "Black Pearl II"]
    assert entries[0]["imo"] == "9123456"  # 9123456.0 float → digits


def test_parse_html_table_saved_as_xls() -> None:
    html = """<html><head><meta charset="utf-8"></head><body>
    <table border=1><tr><th>Vessel Name</th><th>IMO</th><th>Flag</th><th>RFMO</th></tr>
    <tr><td>Ocean&nbsp;Phantom</td><td>9123456</td><td>Unknown</td><td>ICCAT</td></tr>
    <tr><td>Mar del Sur</td><td></td><td>Venezuela</td><td>WCPFC<br>IATTC</td></tr></table></body></html>"""
    data = html.encode("cp1252")
    assert watchlist.detect_format(data) == "html"
    entries = watchlist.parse_table(data)
    assert entries[0]["name"] == "Ocean Phantom" and entries[0]["imo"] == "9123456"
    assert entries[1]["reason"] == "WCPFC IATTC"


def test_parse_spreadsheetml_2003() -> None:
    xml = """<?xml version="1.0"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
    xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="IUU"><Table>
    <Row><Cell><Data ss:Type="String">Vessel Name</Data></Cell><Cell><Data ss:Type="String">IMO</Data></Cell>
         <Cell><Data ss:Type="String">Flag</Data></Cell></Row>
    <Row><Cell><Data ss:Type="String">Sea Ghost</Data></Cell><Cell ss:Index="3"><Data ss:Type="String">Togo</Data></Cell></Row>
    </Table></Worksheet></Workbook>"""
    data = xml.encode()
    assert watchlist.detect_format(data) == "spreadsheetml"
    assert watchlist.parse_table(data) == [{"name": "Sea Ghost", "mmsi": "", "imo": "", "call_sign": "", "flag": "Togo", "reason": ""}]


def test_import_xls_through_api(client_as: Any) -> None:
    data = _real_xls([["Vessel Name", "IMO", "Flag"], ["Ocean Phantom", 9123456, "Unknown"]])
    response = client_as("admin").post("/api/v1/watchlist/import", content=data, headers={"Content-Type": "application/octet-stream"})
    assert response.json()["imported"] == 1 and response.json()["format"] == "xls"
    assert watchlist.Matcher().match(imo="9123456")["name"] == "Ocean Phantom"


TMT_HEADER = ["CurrentlyListed", "Name", "RFMOName", "IMO", "NNR", "VesselType", "GearType", "Flag", "IRCS", "MMSI",
              "GT", "Length", "OwnerName", "ICCAT", "Reason", "IOTC", "Reason", "NAFO", "Reason"]


def _tmt_xls() -> bytes:
    """Same structure as the real iuu-vessels.org download: Disclaimer sheet first, data on sheet 2."""
    xlwt = pytest.importorskip("xlwt")
    book = xlwt.Workbook(encoding="utf-8")
    disclaimer = book.add_sheet("Disclaimer")
    for r, text in enumerate([" ", "The Combined IUU Vessel List", "Date: 2026-10-20", "Disclaimer: …"]):
        disclaimer.write(r, 1, text)
    sheet = book.add_sheet("IUUList")
    rows = [
        TMT_HEADER,
        ["True", " LU RONG YUAN YU 197, 鲁荣远 渔 197", "Unknown", "", "", "Fishing Vessel", "", "", "", "",
         "", "", "", "", "", "", "", "2023-10-11 - ", "Cross-listing"],
        ["True", "ABDI BABA 1", "Sharon 1", 8692299, "", "Fishing Vessel", "Purse seine", "Unknown", "CPA 226", "",
         143.0, 28.78, "Rptd Sold", "2008-11-24 - ", "Use of fraudulent documentation", "2019-12 - ", "Cross-listing", "", ""],
        ["True", "ABISHAK PUTHA 3", "ABUNDANT 1 (YI HONG 06)", "", "", "", "", "Honduras", "4SFXXXX", 417000878,
         "", "", "", "", "", "2015-05-01 - ", "Operating without authorisation", "", ""],
        ["False", "OLD DELISTED", "", 7000001, "", "", "", "", "", "", "", "", "", "2005-12-26 - 2012-06-21", "x", "", "", "", ""],
    ]
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            sheet.write(r, c, value)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_tmt_combined_iuu_layout() -> None:
    report = watchlist.parse_report(_tmt_xls())
    assert report["sheet"] == 2 and report["layout"] == "Combined IUU (TMT)" and report["skipped_delisted"] == 1
    lu, abdi, abishak = report["entries"]
    assert lu["name"] == "LU RONG YUAN YU 197" and lu["details"]["full_name"].endswith("197")
    assert lu["reason"] == "Inscription croisée : NAFO"
    assert abdi["imo"] == "8692299" and abdi["call_sign"] == "CPA 226" and abdi["flag"] == ""
    assert abdi["reason"].startswith("ICCAT (2008-11-24) : Use of fraudulent documentation")
    assert "Inscription croisée : IOTC" in abdi["reason"]
    assert abdi["aliases"] == ["Sharon 1"] and abdi["details"]["gear"] == "Purse seine"
    assert abishak["mmsi"] == "417000878" and abishak["flag"] == "Honduras"
    assert sorted(abishak["aliases"]) == ["ABUNDANT 1", "YI HONG 06"]
    listing = abdi["details"]["listings"][0]
    assert listing == {"rfmo": "ICCAT", "since": "2008-11-24", "until": None, "reason": "Use of fraudulent documentation"}


def test_tmt_matching_by_call_sign_and_rfmo_alias(client_as: Any) -> None:
    response = client_as("admin").post("/api/v1/watchlist/import", content=_tmt_xls(), headers={"Content-Type": "application/octet-stream"})
    body = response.json()
    assert body["imported"] == 3 and body["skipped_delisted"] == 1 and body["with_call_sign"] == 2
    matcher = watchlist.Matcher()
    assert matcher.match(call_sign="CPA226")["name"] == "ABDI BABA 1"  # AIS call sign without the space
    assert matcher.match(name="YI HONG 06")["name"] == "ABISHAK PUTHA 3"  # RFMO alias
    assert matcher.match(name="SHARON 1")["matched_on"] == "nom / alias ORGP"
    assert matcher.match(call_sign="4SFXXXX") is None  # placeholder never matches
    assert matcher.match(imo=7000001) is None  # delisted vessel not imported
    hit = watchlist.summary(matcher.match(imo="8692299"))
    assert hit["gear"] == "Purse seine" and hit["rfmos"] == ["ICCAT", "IOTC"]


def test_real_tmt_file_if_present() -> None:
    """Regression on the real download when it sits next to the project (not committed)."""
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "IUUList-20261020.xls"
    if not path.is_file():
        pytest.skip("IUUList-20261020.xls absent")
    report = watchlist.parse_report(path.read_bytes())
    assert report["layout"] == "Combined IUU (TMT)" and len(report["entries"]) > 100
