"""Unit tests for aggregation, PKI, CFAR detection fallback, C2PA sealing and the API."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from cryptography import x509
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.core.c2pa import generate_and_seal_alert, validate_proof
from app.core.pki import generate_pki, pki_is_complete
from app.pipeline.aggregation import get_all_kraken_geojson
from app.pipeline.detection import cfar_mask, detect_ships_in_sar


@pytest.fixture()
def tmp_settings(tmp_path: Path) -> Settings:
    return Settings(keys_dir=tmp_path / "keys", preuves_dir=tmp_path / "preuves")


def test_pki_generation(tmp_path: Path) -> None:
    keys_dir = tmp_path / "keys"
    generate_pki(keys_dir)
    assert pki_is_complete(keys_dir)
    cert = x509.load_pem_x509_certificate((keys_dir / "cert.pem").read_bytes())
    assert "KRAKEN Sealer Node" in cert.subject.rfc4514_string()


def test_aggregator_merges_geojson(tmp_settings: Settings) -> None:
    preuves = tmp_settings.preuves_dir
    first = preuves / "alerte_20260101_000000_aaa"
    second = preuves / "alerte_20260102_000000_bbb"
    first.mkdir(parents=True)
    second.mkdir(parents=True)
    feature = {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [-52.3, 4.95]},
        "properties": {"type": "dark_vessel", "alert_id": "aaa"},
    }
    (first / "kraken_alerts.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": [feature]}),
        encoding="utf-8",
    )
    (second / "kraken_alerts.geojson").write_text(
        json.dumps(
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [-52.1, 5.0]},
                        "properties": {"type": "dark_vessel", "alert_id": "bbb"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    merged = get_all_kraken_geojson(tmp_settings)
    assert merged["type"] == "FeatureCollection"
    assert len(merged["features"]) == 2


def test_s3_failure_returns_empty_list() -> None:
    detections = detect_ships_in_sar("/tmp/kraken-missing-scene.tiff")
    assert detections == []


def test_cfar_mask_finds_bright_peak() -> None:
    data = np.ones((32, 32), dtype=np.float32)
    data[10, 10] = 500.0
    mask = cfar_mask(data, k=5.0)
    assert bool(mask[10, 10])
    assert int(mask.sum()) < 10


def test_sealer_fallback_sidecar(tmp_settings: Settings) -> None:
    generate_pki(tmp_settings.keys_dir)
    vessels = [{"id": "SAR-001", "lat": 4.95, "lon": -52.33, "confidence": 0.81}]
    path = generate_and_seal_alert(vessels, stac_source="test-scene", settings=tmp_settings)
    assert path is not None and path.is_file()
    dossier = path.parent
    assert (dossier / "radar_crop.jpg").is_file()
    sidecar = json.loads((dossier / "manifest.json").read_text(encoding="utf-8"))
    assert sidecar["hard_binding"]["alg"] == "SHA-256"
    assert sidecar["hard_binding"]["hash"]
    status = validate_proof(dossier, tmp_settings)
    assert status in {"verified", "fallback"}
    assert sidecar["seal"]["alg"] == "ES256" and sidecar["signature"]["root_fingerprint"]


def test_health_endpoint() -> None:
    from server import app

    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_sealer_crops_real_sar_window(tmp_settings: Settings) -> None:
    generate_pki(tmp_settings.keys_dir)
    window = np.random.default_rng(0).random((256, 256), dtype=np.float32)
    window[120, 140] = 50.0
    vessels = [{"id": "SAR-001", "lat": 4.9, "lon": -52.3, "row": 120, "col": 140, "confidence": 0.9}]
    path = generate_and_seal_alert(vessels, settings=tmp_settings, sar_window=window)
    assert path is not None
    with Image.open(path.parent / "radar_crop.jpg") as img:
        assert img.mode == "RGB"
        assert max(img.size) == 640


def test_tampered_proof_is_detected(tmp_settings: Settings) -> None:
    generate_pki(tmp_settings.keys_dir)
    vessels = [{"id": "SAR-001", "lat": 4.95, "lon": -52.33, "confidence": 0.81}]
    path = generate_and_seal_alert(vessels, settings=tmp_settings)
    assert path is not None
    image = path.parent / "radar_crop.jpg"
    image.write_bytes(image.read_bytes() + b"tamper")
    assert validate_proof(path.parent, tmp_settings) == "tampered"
    feature = get_all_kraken_geojson(tmp_settings)["features"][0]
    assert feature["properties"]["c2pa_status"] == "tampered"


def test_ais_tracks_and_rescanned_targets_are_deduplicated(tmp_settings: Settings) -> None:
    generate_pki(tmp_settings.keys_dir)
    vessels = [{"id": "SAR-001", "lat": 4.95, "lon": -52.33, "confidence": 0.81}]
    other = [{"id": "SAR-001", "lat": 5.40, "lon": -52.00, "confidence": 0.7}]
    ais = [{"mmsi": "227543000", "lat": 5.05, "lon": -52.10, "name": "PECHEUR"}]
    generate_and_seal_alert(vessels, settings=tmp_settings, ais_tracks=ais, stac_source="S1A_SCENE_1")
    generate_and_seal_alert(vessels, settings=tmp_settings, ais_tracks=ais, stac_source="S1A_SCENE_1")  # re-scan
    generate_and_seal_alert(other, settings=tmp_settings, ais_tracks=ais, stac_source="S1A_SCENE_2")
    features = get_all_kraken_geojson(tmp_settings)["features"]
    kinds = [f["properties"]["type"] for f in features]
    assert kinds.count("dark_vessel") == 2  # the re-scanned target is shown once
    assert kinds.count("ais_vessel") == 1


def test_api_alerts_and_zee(client_as) -> None:
    client = client_as("viewer")
    alerts = client.get("/api/v1/alerts")
    assert alerts.status_code == 200
    assert alerts.json()["type"] == "FeatureCollection"
    zee = client.get("/api/v1/zee")
    assert zee.json()["features"][0]["geometry"]["type"] == "Polygon"
