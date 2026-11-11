"""Multi-sensor detection (SAR artefacts, optical/thermal, VIIRS lights) and the corroboration of radar targets."""

from __future__ import annotations

import datetime
from typing import Any, Dict

import numpy as np
import pytest

from app.db import db
from app.pipeline import optical_detection as od
from app.pipeline import sar_artifacts
from app.soc import decision, fusion, incidents, sensors
from app.sources import multispectral, viirs_night

NOW = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)


# --------------------------------------------------------------------------- SAR artefacts


def _t(row: int, col: int, peak: float) -> Dict[str, Any]:
    return {"abs_row": row, "abs_col": col, "peak": peak}


def test_azimuth_ambiguity_and_sidelobe_are_rejected_but_neighbours_kept() -> None:
    ship = _t(1000, 500, 1000.0)
    ghost = _t(1000 + 520, 502, 100.0)       # 5.2 km along azimuth, same range, 20 dB weaker
    sidelobe = _t(1000, 520, 50.0)           # same row, 200 m, 26 dB weaker
    small_boat = _t(1000 + 520, 560, 100.0)  # same azimuth offset but another range bin: a real boat
    nearby_peer = _t(1010, 530, 600.0)       # bright enough to be a vessel of its own
    targets = [ghost, small_boat, ship, sidelobe, nearby_peer]
    rejected = sar_artifacts.reject_artifacts(targets, 10.0)
    assert rejected == {"azimuth_ambiguity": 1, "sidelobe": 1}
    assert ghost not in targets and sidelobe not in targets
    assert ship in targets and small_boat in targets and nearby_peer in targets


# --------------------------------------------------------------------------- optical detector


def _sea(shape=(200, 200), level=0.04, noise=0.004, seed=0):
    rng = np.random.default_rng(seed)
    nir = level + noise * rng.standard_normal(shape)
    swir = 0.005 + 0.001 * rng.standard_normal(shape)
    green = 0.08 + 0.003 * rng.standard_normal(shape)
    return nir, swir, green, np.ones(shape, bool)


def test_optical_detector_finds_a_hull_and_rejects_shore_cloud_and_turbidity() -> None:
    nir, swir, green, water = _sea()
    nir[100:103, 120:126] = 0.25
    swir[100:103, 120:126] = 0.12
    water[100:103, 120:126] = False          # the hull is "not water" for the classifier
    invalid = np.zeros_like(water)
    invalid[20:50, 20:50] = True              # a large cloud
    nir[20:50, 20:50] = 0.6
    water[:, :8] = False                      # land strip
    nir[60, 9] = 0.4                          # bright pixel on the shoreline
    nir[150:152, 60:62] = 0.2                 # bright in NIR only (sediment front): no SWIR signal
    targets = od.detect(nir, water, 10.0, invalid=invalid, swir=swir, green=green)
    assert [(t["row"], t["col"]) for t in targets] == [(100, 120)]
    assert targets[0]["length_m"] >= 50 and targets[0]["swir_delta"] > 0.05


def test_cumulus_with_its_shadow_is_not_a_vessel_but_a_cloud_labelled_hull_is() -> None:
    nir, swir, green, water = _sea()
    invalid = np.zeros_like(water)
    nir[80:86, 80:86] = 0.3                   # small cumulus, missed by the cloud mask
    swir[80:86, 80:86] = 0.2
    green[95:101, 92:98] = 0.03               # its shadow, a few hundred metres away
    nir[170:172, 170:175] = 0.3               # hull classified "cloud" by the scene classifier, 1 km away
    swir[170:172, 170:175] = 0.1
    invalid[170:172, 170:175] = True
    targets = od.detect(nir, water, 10.0, invalid=invalid, swir=swir, green=green)
    [hull] = targets
    assert 170 <= hull["row"] <= 171 and 170 <= hull["col"] <= 174


def test_thermal_band_rejects_cold_cloud_tops() -> None:
    nir, swir, green, water = _sea(shape=(120, 120))
    nir[60:62, 60:62] = 0.3
    swir[60:62, 60:62] = 0.15
    bt = np.full(nir.shape, 300.0)
    bt[59:63, 59:63] = 290.0                  # 10 K colder than the sea: a cloud
    assert od.detect(nir, water, 30.0, thermal_k=bt, swir=swir, green=green) == []
    bt[59:63, 59:63] = 301.0                  # warm engine / hull: kept with its thermal contrast
    [target] = od.detect(nir, water, 30.0, thermal_k=bt, swir=swir, green=green)
    assert target["thermal_delta_k"] == pytest.approx(1.0)


def test_overlapping_tiles_and_fragments_are_merged() -> None:
    base = {"sensor": "s2_msi", "acquired_at": "2026-10-21T11:11:00Z", "attributes": {}}
    dets = [{**base, "lat": 5.0, "lon": -52.0, "confidence": 0.9, "length_m": 120, "attributes": {}},
            {**base, "lat": 5.0003, "lon": -52.0002, "confidence": 0.6, "length_m": 30, "attributes": {}},  # same hull, other tile
            {**base, "lat": 5.01, "lon": -52.0, "confidence": 0.7, "length_m": 40, "attributes": {}}]      # 1.1 km away: another vessel
    kept = multispectral.merge_duplicates(dets)
    assert len(kept) == 2 and kept[0]["attributes"]["merged"] == 1


# --------------------------------------------------------------------------- VIIRS night lights


COLORMAP_XML = """<ColorMaps><ColorMap title="No Data"><Entries>
<ColorMapEntry rgb="0,0,0" transparent="true" sourceValue="[0,0]" value="[-999,-999]" nodata="true" ref="0"/></Entries></ColorMap>
<ColorMap title="Radiance"><Entries>
<ColorMapEntry rgb="10,10,10" transparent="false" value="[0.0,0.5)" ref="1"/>
<ColorMapEntry rgb="200,200,200" transparent="false" value="[10,20)" ref="2"/>
<ColorMapEntry rgb="255,255,255" transparent="false" value="[100,1000000)" ref="3"/>
</Entries></ColorMap></ColorMaps>"""


def test_colormap_decoding_gives_physical_values() -> None:
    table = viirs_night.parse_colormap(COLORMAP_XML)
    rgba = np.array([[[10, 10, 10, 255], [200, 200, 200, 255], [255, 255, 255, 255], [1, 2, 3, 0]]], dtype=np.uint8)
    values = viirs_night.decode(rgba, table)
    assert values[0, 0] == pytest.approx(0.25) and values[0, 1] == pytest.approx(15)
    assert values[0, 2] == pytest.approx(100)  # open-ended saturation class: lower bound
    assert np.isnan(values[0, 3])


def test_isolated_lights_kept_cities_and_lit_clouds_rejected() -> None:
    radiance = np.full((100, 100), 0.4) + 0.05 * np.random.default_rng(1).standard_normal((100, 100))
    sea = np.ones_like(radiance, bool)
    bt = np.full(radiance.shape, 299.0)
    radiance[30, 30] = 25.0                   # a lit fishing vessel
    radiance[60:66, 60:66] = 40.0             # a town / lit cloud bank: too large
    radiance[80, 20] = 20.0
    bt[78:83, 18:23] = 270.0                  # cold cloud top around this one
    lights = viirs_night.detect_lights(radiance, sea, bt, k=6.0)
    assert [(l["row"], l["col"]) for l in lights] == [(30, 30)]
    assert lights[0]["confidence"] >= 0.55


# --------------------------------------------------------------------------- fusion


def _detection(sensor: str, scene: str, at: datetime.datetime, lat: float, lon: float, conf: float, **attrs: Any) -> Dict[str, Any]:
    return {"sensor": sensor, "platform": "test", "scene_id": scene, "acquired_at": at.isoformat(),
            "time_uncertainty_min": 60 if sensor == "viirs_dnb" else 0, "lat": lat, "lon": lon, "length_m": 40,
            "confidence": conf, "attributes": attrs}


def test_fusion_corroborates_a_dark_vessel_across_disciplines() -> None:
    t0 = NOW - datetime.timedelta(hours=6)
    sensors.save([
        _detection("viirs_dnb", "snpp:x", t0, 6.000, -52.000, 0.6, radiance=20),
        _detection("s2_msi", "S2A_X", t0 + datetime.timedelta(hours=3), 6.080, -52.010, 0.7),   # ~9 km, 3 h later: same area
        _detection("s2_msi", "S2A_X", t0 + datetime.timedelta(hours=3), 6.085, -52.010, 0.6),   # same image, 0.5 km: another boat
        _detection("s2_msi", "S2A_Y", t0, 7.5, -50.0, 0.5),                                     # far away, alone
    ])
    picture = fusion.fuse(NOW - datetime.timedelta(hours=12), NOW)
    statuses = sorted(c["status"] for c in picture["contacts"])
    assert statuses == ["dark_corroborated", "dark_single", "dark_single"]
    corroborated = picture["contacts"][0]
    assert {i["int"] for i in corroborated["ints"]} == {"NIGHT", "EO"}
    assert corroborated["confidence"] == pytest.approx(1 - 0.4 * 0.3, abs=1e-3)


def test_loose_track_is_not_corroboration() -> None:
    t0 = NOW - datetime.timedelta(hours=8)
    sensors.save([
        _detection("viirs_dnb", "snpp:y", t0, 5.0, -51.8, 0.9, radiance=40),
        _detection("s2_msi", "S2A_W", t0 + datetime.timedelta(hours=6), 5.0, -52.15, 0.6),  # 39 km, 6 h later: reachable, not "same area"
    ])
    [contact] = fusion.fuse(NOW - datetime.timedelta(hours=12), NOW)["contacts"]
    assert contact["status"] == "dark_single" and "non confirmé" in contact["explanation"]
    assert contact["confidence"] == pytest.approx(0.9)  # the loose link adds no weight
    assert not any(i["corroborated"] for i in contact["ints"])


def test_ais_track_explains_an_optical_detection() -> None:
    with db() as conn:
        for minutes, lat in ((0, 5.50), (60, 5.65)):
            conn.execute("INSERT INTO ais_positions (mmsi, ts, lat, lon, sog, cog, source) VALUES (?, ?, ?, ?, ?, ?, 'aisstream')",
                         ("227000001", (NOW - datetime.timedelta(hours=2) + datetime.timedelta(minutes=minutes)).isoformat(),
                          lat, -52.0, 9.0, 0.0))
    sensors.save([_detection("s2_msi", "S2A_Z", NOW - datetime.timedelta(minutes=90), 5.575, -52.001, 0.8)])
    picture = fusion.fuse(NOW - datetime.timedelta(hours=6), NOW)
    [contact] = picture["contacts"]
    assert contact["status"] == "cooperative" and contact["identity"]["mmsi"] == "227000001"
    assert {i["int"] for i in contact["ints"]} == {"AIS", "EO"}


def test_weak_radar_echo_corroborated_by_optical_image_is_no_longer_doubtful(monkeypatch: pytest.MonkeyPatch) -> None:
    v = {"id": "SAR-001", "lat": 5.4, "lon": -52.0, "zone": "eez", "risk": {"score": 85}, "quality": {"score": 0.3}}
    incident, _ = incidents.ingest_detection(v, alert_id="a1", dossier="", scene_id=None,
                                             seen_at=(NOW - datetime.timedelta(minutes=30)).isoformat())
    full = incidents.get_incident(incident["id"])
    assert decision.assess(full, NOW)["recommended"] == "identify"
    sar_obs = fusion._obs("SAR", at=NOW - datetime.timedelta(minutes=30), lat=5.4, lon=-52.0, sigma_km=0.1, p=0.3,
                          source="Sentinel-1", ref="a1:SAR-001", acquisition="s1:x", extra={"incident_id": incident["id"]})
    monkeypatch.setattr(fusion, "_sar", lambda *_a: [sar_obs])
    sensors.save([_detection("s2_msi", "S2A_CORRO", NOW - datetime.timedelta(minutes=10), 5.41, -52.0, 0.85)])
    result = decision.assess(full, NOW)
    assert result["corroboration"]["corroborated"]
    assert result["recommended"] == "intercept" and "Corroboration multi-capteurs" in result["recommendation"]


# --------------------------------------------------------------------------- watch & API


def test_sensor_watch_processes_each_scene_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"optical": 0, "night": 0}
    item = {"id": "S2A_TEST_L2A", "properties": {}}

    def scan_night(satellite: str, day: datetime.date, _cfg: Any) -> Dict[str, Any]:
        calls["night"] += 1
        return {"available": True, "detections": [_detection("viirs_dnb", f"{satellite}:{day}", NOW, 6.0, -52.0, 0.6)]}

    def scan_scene(_item: Any, sensor: str, _cfg: Any) -> list:
        calls["optical"] += 1
        return [_detection(sensor, _item["id"], NOW, 5.5, -52.3, 0.7)]

    monkeypatch.setattr(viirs_night, "scan_night", scan_night)
    monkeypatch.setattr(multispectral, "search", lambda sensor, *a, **k: [item] if sensor == "s2_msi" else [])
    monkeypatch.setattr(multispectral, "scan_scene", scan_scene)
    first = sensors.watch_tick(now=NOW)
    assert first["optical_scenes"] == 1 and first["nights"] >= 3 and first["detections"] >= 4
    second = sensors.watch_tick(now=NOW)
    assert second == {"nights": 0, "optical_scenes": 0, "detections": 0}
    assert calls["optical"] == 1


def test_optical_scan_requires_analyst(client_as: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    found = [_detection("s2_msi", "S2A_API", NOW - datetime.timedelta(hours=1), 5.5, -52.3, 0.7)]
    monkeypatch.setattr(multispectral, "scan_area", lambda *a, **k: {"scenes": [{"scene_id": "S2A_API"}], "detections": found})
    body = {"lat": 5.5, "lon": -52.3, "radius_km": 5, "days": 5}
    viewer = client_as("viewer")
    assert viewer.post("/api/v1/sensors/optical/scan", json=body).status_code == 403
    result = client_as("analyst").post("/api/v1/sensors/optical/scan", json=body).json()
    assert result["stored"] == 1 and len(result["detections"]) == 1
    for removed in ("/api/v1/fusion", "/api/v1/osint/news", "/api/v1/intel/reports"):
        assert viewer.get(removed).status_code == 404
