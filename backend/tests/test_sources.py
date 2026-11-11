"""Tests for geo helpers, open-data sources, detection, correlation and risk scoring."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.core.c2pa import generate_and_seal_alert
from app.core.geo import distance_to_boundary_km, haversine_km, point_in_geometry, simplify_geometry
from app.core.pki import generate_pki
from app.pipeline.detection import blobs_to_targets, cfar_mask, correlate_with_ais, label_blobs, match_radius_km
from app.pipeline.risk import risk_level, score_vessel
from app.sources import marine_regions
from app.sources.aisstream import parse_position_report
from app.sources.gfw import fetch_events, parse_event
from app.sources.http import cache_write
from app.sources.open_meteo import sar_reliability, sea_state

SQUARE: Dict[str, Any] = {
    "type": "Polygon",
    "coordinates": [[[-53.0, 5.0], [-52.0, 5.0], [-52.0, 6.0], [-53.0, 6.0], [-53.0, 5.0]]],
}


@pytest.fixture()
def cfg(tmp_path: Path) -> Settings:
    return Settings(keys_dir=tmp_path / "keys", preuves_dir=tmp_path / "preuves", cache_dir=tmp_path / "cache")


def test_geo_helpers() -> None:
    assert point_in_geometry(-52.5, 5.5, SQUARE)
    assert not point_in_geometry(-51.5, 5.5, SQUARE)
    assert haversine_km(5.0, -52.0, 6.0, -52.0) == pytest.approx(111.2, abs=0.5)
    assert distance_to_boundary_km(-52.5, 5.5, SQUARE) == pytest.approx(55.0, abs=1.5)
    dense = {"type": "Polygon", "coordinates": [[[-53.0 + i / 100, 5.0] for i in range(101)] + [[-52.0, 6.0], [-53.0, 6.0], [-53.0, 5.0]]]}
    assert len(simplify_geometry(dense, 0.01)["coordinates"][0]) < 10


def test_fallback_eez_contains_sea_not_land(cfg: Settings) -> None:
    eez = marine_regions.eez_geometry(cfg)  # offline -> config polygon
    assert point_in_geometry(-52.3, 5.6, eez)  # off Kourou
    assert not point_in_geometry(-53.0, 4.0, eez)  # inland Guiana


def test_boundaries_use_cache_then_fallback(cfg: Settings) -> None:
    fallback = marine_regions.boundaries_feature_collection(cfg)
    assert [f["properties"]["source"] for f in fallback["features"]] == ["fallback"]

    cache_write(
        "marine_regions",
        f"eez_12nm:{marine_regions.TERRITORY}",
        {"geometry": SQUARE, "mrgid": 1, "geoname": "12nm", "area_km2": 1},
        cfg,
    )
    zones = marine_regions.zone_geometries(cfg)
    assert marine_regions.classify_zone(-52.5, 5.5, zones) == "territorial"
    assert marine_regions.classify_zone(-50.0, 7.0, zones) == "eez"
    assert marine_regions.classify_zone(-40.0, 0.0, zones) is None


def test_blob_extraction_sizes_and_confidence(cfg: Settings) -> None:
    data = np.random.default_rng(1).normal(10.0, 1.0, (200, 200)).astype(np.float32)
    data[50:53, 60:68] = 80.0  # 80 m echo = ~60 m hull + ~20 m of radar blur
    data[150, 150:152] = 60.0  # 2-pixel echo: no measurable hull
    data[100:106, 20:26] = 0.0
    for i in range(6):
        data[100 + i, 20 + i] = 80.0  # same 60 m hull, diagonal: not √2 longer
    targets = blobs_to_targets(data, cfar_mask(data, cfg.cfar_k), cfg)
    big = [t for t in targets if t["pixels"] >= 20]
    assert len(big) == 1 and 55 <= big[0]["length_m"] <= 65
    diagonal = [t for t in targets if t["pixels"] == 6]
    assert diagonal and 55 <= diagonal[0]["length_m"] <= 70
    assert [t["length_m"] for t in targets if t["pixels"] == 2] == [cfg.pixel_spacing_m]
    assert all(0.5 <= t["confidence"] <= 0.99 for t in targets)
    assert len(label_blobs(np.zeros((5, 5), dtype=bool))) == 0


def test_time_aware_ais_correlation(cfg: Settings) -> None:
    radar = [
        {"id": "A", "lat": 5.50, "lon": -52.50, "confidence": 0.9},
        {"id": "B", "lat": 5.505, "lon": -52.50, "confidence": 0.8},
        {"id": "C", "lat": 6.20, "lon": -51.80, "confidence": 0.7},
    ]
    # One AIS ship near A/B: it can only explain one of them.
    ais = [{"mmsi": "1", "lat": 5.501, "lon": -52.50, "time_utc": "2026-10-19T21:34:00Z", "sog_knots": 0}]
    dark, _ = correlate_with_ais(radar, cfg, ais_tracks=ais, scene_time="2026-10-19T21:34:39Z")
    assert sorted(d["id"] for d in dark) == ["B", "C"]

    moving = {"lat": 0, "lon": 0, "time_utc": "2026-10-19T19:34:39Z", "sog_knots": 5}
    assert match_radius_km(moving, None, cfg) == cfg.ais_match_km
    from app.pipeline.detection import _parse_time

    radius = match_radius_km(moving, _parse_time("2026-10-19T21:34:39Z"), cfg)
    assert radius == pytest.approx(cfg.ais_match_km + 5 * 1.852 * 2, abs=0.01)
    # Stored fix projected to the pass 2 h after it was received: only the projection error is added.
    projected = {**moving, "time_utc": "2026-10-19T21:34:39Z", "fix_age_h": 2.0}
    radius = match_radius_km(projected, _parse_time("2026-10-19T21:34:39Z"), cfg)
    assert radius == pytest.approx(cfg.ais_match_km + 0.25 * 5 * 1.852 * 2, abs=0.01)
    assert match_radius_km({**projected, "fix_age_h": 0.0}, None, cfg) == cfg.ais_match_km


def test_risk_scoring_explainable(cfg: Settings) -> None:
    vessel = {"lat": 5.5, "lon": -52.5, "confidence": 0.9, "length_m": 55.0}
    gap = {
        "geometry": {"type": "Point", "coordinates": [-52.45, 5.52]},
        "properties": {"event_type": "gap"},
    }
    calm = {"sar_reliability": sar_reliability(5.0, 1.0)}
    high = score_vessel(vessel, zone="territorial", events=[gap], sea_state=calm, ais_source="aisstream.io", settings=cfg)
    low = score_vessel(vessel, zone=None, events=[], sea_state=calm, ais_source="mock", settings=cfg)
    assert high["score"] > low["score"]
    assert high["level"] in {"high", "critical"}
    assert any("coupure" in r for r in high["reasons"])
    assert any("simulé" in r for r in low["reasons"])
    assert risk_level(10) == "low" and risk_level(80) == "critical"


def test_sea_state_offline_and_reliability(cfg: Settings) -> None:
    offline = sea_state(settings=cfg)
    assert offline["available"] is False
    assert offline["sar_reliability"]["level"] == "unknown"
    assert sar_reliability(15.0, 1.0)["level"] == "low"
    assert sar_reliability(5.0, 1.0)["level"] == "high"


def test_gfw_parsing_and_missing_token(cfg: Settings) -> None:
    assert fetch_events(cfg)["available"] is False
    feature = parse_event(
        {
            "id": "abc",
            "type": "gap",
            "start": "2026-10-01T00:00:00Z",
            "position": {"lat": 5.9, "lon": -51.9},
            "vessel": {"ssvid": "123", "name": "X", "flag": "VEN"},
            "gap": {"durationHours": 30.5, "intentionalDisabling": True},
        },
        "gap",
    )
    assert feature is not None
    assert feature["geometry"]["coordinates"] == [-51.9, 5.9]
    assert feature["properties"]["intentional_disabling"] is True
    assert parse_event({"id": "nopos"}, "fishing") is None


def test_aisstream_parsing() -> None:
    message = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": 227543000, "ShipName": "PECHEUR ", "latitude": 5.1, "longitude": -52.2, "time_utc": "2026-10-20 10:00:00.1 +0000 UTC"},
        "Message": {"PositionReport": {"Sog": 7.5, "Cog": 90.0, "TrueHeading": 91}},
    }
    track = parse_position_report(message)
    assert track is not None
    assert track["mmsi"] == "227543000" and track["name"] == "PECHEUR"
    assert track["sog_knots"] == 7.5
    assert parse_position_report({"MessageType": "ShipStaticData"}) is None


def test_sealed_alert_has_chip_sheet_risk_and_scene(cfg: Settings) -> None:
    generate_pki(cfg.keys_dir)
    chip = np.random.default_rng(2).random((96, 96), dtype=np.float32)
    vessels = [
        {
            "id": "SAR-001",
            "lat": 5.5,
            "lon": -52.5,
            "confidence": 0.9,
            "chip": chip,
            "zone": "eez",
            "risk": {"score": 72, "level": "high", "reasons": ["test"]},
            "ais_source": "mock",
        }
    ]
    scene = {
        "type": "Feature",
        "geometry": SQUARE,
        "properties": {"type": "s1_scene", "scene_id": "S1X", "datetime": "2026-10-19T21:34:39Z", "mode": "IW"},
    }
    path = generate_and_seal_alert(vessels, settings=cfg, scene=scene, stac_source="S1X")
    assert path is not None
    with Image.open(path.parent / "radar_crop.jpg") as img:
        assert img.size == (300, 322)
    import json

    features = json.loads(path.read_text(encoding="utf-8"))["features"]
    dark = next(f for f in features if f["properties"]["type"] == "dark_vessel")
    assert dark["properties"]["risk_score"] == 72
    assert dark["properties"]["scene_datetime"] == "2026-10-19T21:34:39Z"
    assert any(f["properties"]["type"] == "s1_scene" for f in features)
    assert '"chip":' not in json.dumps(features)  # raw ndarray never serialised
    assert dark["properties"]["image_url"].endswith("/chip_SAR-001.jpg")
    assert (path.parent / "chip_SAR-001.jpg").is_file()


def test_context_endpoints_offline(client_as) -> None:
    client = client_as("viewer")
    sources = client.get("/api/v1/sources").json()
    assert sources["open_meteo"]["key_required"] is False
    assert client.get("/api/v1/environment").json()["available"] is False
    assert client.get("/api/v1/events").json()["available"] is False
    zee = client.get("/api/v1/zee").json()
    assert zee["features"][0]["properties"]["zone"] == "eez"


def test_detection_on_gcp_georeferenced_geotiff(tmp_path: Path) -> None:
    """End-to-end SAR read: radar-geometry GeoTIFF with GCPs, like Sentinel-1 GRD."""
    import rasterio
    from rasterio.control import GroundControlPoint
    from rasterio.crs import CRS

    from app.pipeline.detection import detect_ships_with_window

    size = 512
    data = np.random.default_rng(3).normal(100.0, 10.0, (size, size)).astype(np.float32)
    data[100:103, 100:106] = 2000.0  # target inside the square
    data[400:403, 480:486] = 2000.0  # target outside the square (east of -52.0)
    # Pixel (0,0) -> (-53.0, 6.0) ; pixel (512,512) -> (-51.8, 4.8)
    gcps = [
        GroundControlPoint(row=0, col=0, x=-53.0, y=6.0),
        GroundControlPoint(row=0, col=size, x=-51.8, y=6.0),
        GroundControlPoint(row=size, col=0, x=-53.0, y=4.8),
        GroundControlPoint(row=size, col=size, x=-51.8, y=4.8),
    ]
    path = tmp_path / "s1.tif"
    with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=1, dtype="float32") as dst:
        dst.write(data, 1)
        dst.gcps = (gcps, CRS.from_epsg(4326))

    cfg = Settings(sar_window_size=256, sar_max_windows=4, access_key="", secret_key="")
    detections, window = detect_ships_with_window(str(path), cfg, eez=SQUARE)
    assert window is not None
    assert len(detections) == 1
    det = detections[0]
    assert det["lon"] == pytest.approx(-53.0 + 103 / size * 1.2, abs=0.01)
    assert det["lat"] == pytest.approx(6.0 - 101.5 / size * 1.2, abs=0.01)
    assert det["chip"].shape[0] > 0 and det["id"] == "SAR-001"
