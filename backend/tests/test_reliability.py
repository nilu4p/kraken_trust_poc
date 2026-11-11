"""Detection quality, optical imagery, STAC retries and real C2PA sealing."""

from __future__ import annotations

import datetime
import json
from pathlib import Path
from typing import Any, Dict, List

import httpx
import numpy as np
import pytest
from cryptography import x509
from fastapi.testclient import TestClient

from app.config import Settings, get_settings
from app.core.c2pa import generate_and_seal_alert, validate_proof
from app.core.pki import ensure_pki, generate_pki, leaf_is_c2pa_compatible
from app.pipeline.detection import cfar_mask, dual_pol_confirmed
from app.pipeline.quality import assess
from app.sources import stac
from app.sources.optical import search_optical, to_feature


@pytest.fixture()
def cfg(tmp_path: Path) -> Settings:
    return Settings(keys_dir=tmp_path / "keys", preuves_dir=tmp_path / "preuves", cache_dir=tmp_path / "cache")


def _seal(cfg: Settings, lat: float = 5.5, lon: float = -52.5, scene: str = "S1A") -> Path:
    vessel = {
        "id": "SAR-001",
        "lat": lat,
        "lon": lon,
        "confidence": 0.9,
        "chip": np.random.default_rng(0).random((96, 96), dtype=np.float32),
    }
    path = generate_and_seal_alert([vessel], settings=cfg, stac_source=scene)
    assert path is not None
    return path


def test_local_cfar_handles_heterogeneous_clutter() -> None:
    rng = np.random.default_rng(1)
    data = rng.gamma(4.0, 10.0, (300, 300)).astype(np.float32)
    data[:, 150:] *= 6.0  # wind-roughened half of the scene
    data[100:102, 60:66] = 180.0  # ship in the calm half, dimmer than the rough sea
    mask = cfar_mask(data, 5.0)
    assert mask[100:102, 60:66].any()
    # A single global threshold would miss it entirely:
    assert not (data[100:102, 60:66] > np.percentile(data, 99.5)).any()
    assert dual_pol_confirmed(mask, 101, 63)
    assert not dual_pol_confirmed(np.zeros_like(mask), 101, 63)


def test_quality_flags_and_levels(cfg: Settings) -> None:
    good = {"lat": 5.5, "lon": -52.5, "snr": 20.0, "pixels": 12, "elongation": 3.0, "dual_pol": True, "swath_edge": False}
    weak = {"lat": 5.5, "lon": -52.5, "snr": 5.5, "pixels": 1, "elongation": 1.0, "dual_pol": False, "swath_edge": True}
    q_good = assess(good, settings=cfg)
    q_weak = assess(weak, settings=cfg)
    assert q_good["level"] == "reliable" and q_weak["level"] == "doubtful"
    assert any("double polarisation" in f for f in q_good["flags"])
    assert any("Bord de fauchée" in f for f in q_weak["flags"])


def test_persistent_target_and_false_positive_feedback(cfg: Settings) -> None:
    target = {"lat": 5.5, "lon": -52.5, "snr": 20.0, "pixels": 12, "elongation": 3.0, "dual_pol": True}
    history = [
        {"lat": 5.5005, "lon": -52.5, "scene_id": "S1-old-1", "review": None},
        {"lat": 5.4998, "lon": -52.5003, "scene_id": "S1-old-2", "review": None},
    ]
    baseline = assess(target, settings=cfg)["score"]
    persistent = assess(target, history=history, scene_id="S1-new", settings=cfg)
    assert any("Cible récurrente" in f for f in persistent["flags"])
    # A vessel returning to its spot must stay an alert: moderate penalty only.
    assert persistent["score"] >= baseline - 0.15 - 1e-9 and persistent["score"] >= cfg.min_detection_quality
    # No recurrence penalty where an operator already confirmed a vessel.
    confirmed = assess(target, history=[*history, {**history[0], "scene_id": "S1-old-3", "review": "confirmed"}],
                       scene_id="S1-new", settings=cfg)
    assert not any("Cible récurrente" in f for f in confirmed["flags"])
    flagged = assess(target, history=[{**history[0], "review": "false_positive"}], scene_id="S1-new", settings=cfg)
    assert flagged["score"] < baseline - 0.3
    # An old verdict no longer blinds the spot.
    old = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=cfg.false_positive_memory_days + 1)).isoformat()
    expired = assess(target, history=[{**history[0], "review": "false_positive", "reviewed_at": old}], scene_id="S1-new", settings=cfg)
    assert expired["score"] == baseline


def test_real_c2pa_signature_and_chip_tamper(cfg: Settings) -> None:
    pytest.importorskip("c2pa")
    generate_pki(cfg.keys_dir)
    path = _seal(cfg)
    assert validate_proof(path.parent, cfg) == "verified"
    chip = path.parent / "chip_SAR-001.jpg"
    chip.write_bytes(chip.read_bytes() + b"tamper")
    assert validate_proof(path.parent, cfg) == "tampered"


def test_proof_resigned_by_another_pki_is_rejected(cfg: Settings, tmp_path: Path) -> None:
    """A valid C2PA signature is not enough: it must chain to a KRAKEN root."""
    pytest.importorskip("c2pa")
    generate_pki(cfg.keys_dir)
    path = _seal(cfg)
    forger = cfg.model_copy(update={"keys_dir": tmp_path / "forger", "preuves_dir": tmp_path / "forged"})
    generate_pki(forger.keys_dir, forger)
    forged = _seal(forger)
    # Forged dossier, sidecar hashes recomputed by the forger: still not a KRAKEN proof.
    assert validate_proof(forged.parent, cfg) == "tampered"
    assert validate_proof(path.parent, cfg) == "verified"


def test_sidecar_seal_protects_fallback_proofs(cfg: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    import hashlib

    from app.core import c2pa as sealer

    generate_pki(cfg.keys_dir)
    monkeypatch.setattr(sealer, "_try_c2pa_sign", lambda *_a, **_kw: False)  # C2PA library unavailable
    path = _seal(cfg)
    dossier = path.parent
    assert validate_proof(dossier, cfg) == "fallback"
    # Editing the image and recomputing the hash in the sidecar breaks the seal.
    image = dossier / "radar_crop.jpg"
    image.write_bytes(image.read_bytes() + b"edit")
    manifest = json.loads((dossier / "manifest.json").read_text(encoding="utf-8"))
    manifest["hard_binding"]["hash"] = hashlib.sha256(image.read_bytes()).hexdigest()
    (dossier / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert validate_proof(dossier, cfg) == "tampered"
    # An old unsealed sidecar with matching hashes cannot be proven either way.
    manifest.pop("seal")
    (dossier / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    assert validate_proof(dossier, cfg) == "unverifiable"


def test_renewed_leaf_and_replaced_root_keep_old_proofs_verifiable(cfg: Settings) -> None:
    pytest.importorskip("c2pa")
    from app.core import pki

    generate_pki(cfg.keys_dir)
    old = _seal(cfg)
    pki.renew_leaf(cfg)
    assert validate_proof(old.parent, cfg) == "verified"
    renewed = _seal(cfg, scene="S1B")
    pki.generate_pki(cfg.keys_dir, cfg)  # new root: the previous one is archived
    assert len(pki.trusted_roots(cfg)) == 2
    assert validate_proof(old.parent, cfg) == "verified" and validate_proof(renewed.parent, cfg) == "verified"


def test_legacy_code_signing_pki_is_regenerated(cfg: Settings) -> None:
    generate_pki(cfg.keys_dir)
    assert leaf_is_c2pa_compatible(cfg.keys_dir)
    # Simulate the old PKI: a leaf without key identifiers.
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "old")])
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    old = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(1).not_valid_before(now).not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    (cfg.keys_dir / "cert.pem").write_bytes(old.public_bytes(serialization.Encoding.PEM))
    assert not leaf_is_c2pa_compatible(cfg.keys_dir)
    ensure_pki(cfg)
    assert leaf_is_c2pa_compatible(cfg.keys_dir)


def test_optical_feature_normalisation() -> None:
    item: Dict[str, Any] = {
        "id": "PH1B_X",
        "geometry": {"type": "Point", "coordinates": [-54.2, 4.1]},
        "properties": {
            "platform": "pleiades-1b",
            "gsd": 2.0,
            "eo:cloud_cover": 0.0,
            "datetime": "2025-04-29T14:02:07Z",
            "start_datetime": "2024-11-15T14:17:40Z",
            "product:type": "PHR_MS___3",
        },
        "assets": {"product": {"href": "https://download.example/Products(1)/$value"}},
    }
    from app.sources.optical import _parse_dt

    props = to_feature(item, "ccm-optical", _parse_dt("2024-11-15T12:17:40Z"))["properties"]
    assert props["label"] == "Pléiades" and props["acquired"].startswith("2024-11-15")
    assert props["delta_hours_vs_sar"] == 2.0
    assert "éligibles" in props["access"]


def test_optical_search_offline(cfg: Settings) -> None:
    result = search_optical(5.0, -52.0, settings=cfg)
    assert result["available"] is False and result["features"] == []


def test_stac_retries_on_rate_limit(monkeypatch: pytest.MonkeyPatch, cfg: Settings) -> None:
    calls: List[int] = []

    def fake_post(*_args: Any, **_kwargs: Any) -> httpx.Response:
        calls.append(1)
        request = httpx.Request("POST", "https://stac.test/search")
        if len(calls) == 1:
            return httpx.Response(429, request=request, headers={"Retry-After": "1"})
        return httpx.Response(200, request=request, json={"features": [{"id": "A"}]})

    monkeypatch.setattr(stac.httpx, "post", fake_post)
    assert stac.stac_search({"collections": ["x"]}, settings=cfg) == [{"id": "A"}]
    assert len(calls) == 2
    # Second call is served from cache.
    assert stac.stac_search({"collections": ["x"]}, settings=cfg) == [{"id": "A"}]
    assert len(calls) == 2


def test_imagery_endpoint_validation(client_as) -> None:
    client = client_as("viewer")
    assert client.get("/api/v1/imagery").status_code == 422
    body = client.get("/api/v1/imagery", params={"lat": 5.5, "lon": -52.5}).json()
    assert body["type"] == "FeatureCollection"


def test_s3_errors_are_classified_for_operators() -> None:
    from app.pipeline.detection import classify_s3_error

    assert classify_s3_error(Exception("SignatureDoesNotMatch: ...")).code == "bad_secret"
    assert classify_s3_error(Exception("InvalidAccessKeyId")).code == "bad_access_key"
    assert classify_s3_error(Exception("HTTP response code: 403")).code == "forbidden"
    assert classify_s3_error(Exception("SSL certificate problem")).code == "tls"
    assert classify_s3_error(Exception("Could not resolve host")).code == "network"
    assert classify_s3_error(ValueError("boom")).code == "read_error"


def test_scan_reports_the_real_sar_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import pystac

    from app.pipeline import orchestrator
    from app.pipeline.detection import SarReadError
    from app.pipeline.scan_state import snapshot

    item = pystac.Item(
        "S1X", {"type": "Point", "coordinates": [-52, 5]}, None,
        datetime.datetime(2026, 10, 19, tzinfo=datetime.timezone.utc), {},
    )
    item.add_asset("vh", pystac.Asset("s3://eodata/x-vh.tiff"))
    monkeypatch.setattr(orchestrator, "fetch_sentinel1_data", lambda *a, **k: item)

    def fail(*_a: Any, **_k: Any) -> Any:
        raise SarReadError("no_tile", "La scène ne recouvre pas la ZEE : choisissez une autre scène")

    monkeypatch.setattr(orchestrator, "detect_ships_with_window", fail)
    orchestrator.run_kraken_pipeline(scene_id="S1X", actor="tester")
    state = snapshot()
    assert state["status"] == "error" and "ne recouvre pas la ZEE" in state["message"]


def test_key_check_reports_exact_s3_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline import detection
    from app.sources import checks

    class FakeClient:
        def list_objects_v2(self, **_kw: Any) -> None:
            error = Exception("denied")
            error.response = {"Error": {"Code": "InvalidAccessKeyId"}, "ResponseMetadata": {"HTTPStatusCode": 403}}  # type: ignore[attr-defined]
            raise error

    import boto3

    cfg = get_settings().model_copy(update={"access_key": "admin", "secret_key": "x"})
    monkeypatch.setattr(detection, "probe_sar_access", lambda _cfg: {"ok": False, "code": "forbidden", "message": "403"})
    monkeypatch.setattr(boto3, "client", lambda *a, **k: FakeClient())
    result = checks.check_copernicus_s3(cfg)
    assert not result["ok"] and "InvalidAccessKeyId (HTTP 403)" in result["message"]
    monkeypatch.setattr(detection, "probe_sar_access", lambda _cfg: {"ok": True, "code": "ok", "message": "Lecture SAR OK"})
    assert checks.check_copernicus_s3(cfg)["ok"]


def test_webhook_allowlist_and_rich_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.soc import notify

    cfg = get_settings().model_copy(update={"webhook_allowed_hosts": ["notif-sink"], "public_url": "http://kraken.local"})
    assert notify.validate_webhook_url("http://notif-sink:8090/hooks/kraken", cfg)
    with pytest.raises(notify.WebhookRejected):
        notify.validate_webhook_url("http://other-internal:8090/x", cfg)
    incident = {"id": 3, "ref": "KRK-2026-0003", "title": "T", "severity": "P2", "risk_score": 60, "lat": 5.5, "lon": -52.5, "zone": "eez"}
    attachment = notify.incident_attachment(incident, "Nouvel incident", cfg)
    assert attachment["color"] == "#ff8c1a" and attachment["title_link"] == "http://kraken.local/#/incidents/3"
    assert {f["title"] for f in attachment["fields"]} >= {"Sévérité", "Risque", "Zone", "Position"}


def test_runtime_settings_are_documented() -> None:
    from app.soc import runtime

    for name, spec in runtime.describe().items():
        assert spec["what"] and spec["higher"] and spec["lower"] and spec["advice"], name
        assert spec["min"] <= spec["default"] <= spec["max"], name
