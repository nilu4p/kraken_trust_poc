"""Keyless Sentinel-1 mirror (Planetary Computer): same scene, other reading path."""

from __future__ import annotations

import datetime
from typing import Any, Dict, List

import pytest

from app.config import get_settings
from app.sources import planetary

COPERNICUS_ID = "S1D_IW_GRDH_1SDV_20261019T091201_20261019T091226_005082_0099DD_7A1B_COG"
MIRROR_ID = "S1D_IW_GRDH_1SDV_20261019T091201_20261019T091226_005082_0099DD"


def test_product_key_matches_both_catalogues() -> None:
    assert planetary.product_key(COPERNICUS_ID) == planetary.product_key(MIRROR_ID)
    assert planetary.product_key(COPERNICUS_ID) != planetary.product_key(MIRROR_ID.replace("0099DD", "0099DE"))


def test_bands_are_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    item = {"id": MIRROR_ID, "assets": {"vv": {"href": "https://blob/iw-vv.tiff"}, "vh": {"href": "https://blob/iw-vh.tiff"}}}
    monkeypatch.setattr(planetary, "find", lambda *a, **k: item)
    monkeypatch.setattr(planetary, "_sas_token", lambda _cfg: "sv=1&sig=abc")
    bands = planetary.bands_for(COPERNICUS_ID, "2026-10-19T09:12:01Z")
    assert bands == {"co": "https://blob/iw-vv.tiff?sv=1&sig=abc", "cross": "https://blob/iw-vh.tiff?sv=1&sig=abc"}


def test_mirror_unreachable_is_not_an_error() -> None:
    assert planetary.find(COPERNICUS_ID, "2026-10-19T09:12:01Z") is None  # network disabled in tests
    assert planetary.bands_for(COPERNICUS_ID, None) is None


def _item():
    import pystac

    item = pystac.Item(COPERNICUS_ID, {"type": "Point", "coordinates": [-52, 5]}, None,
                       datetime.datetime(2026, 10, 19, 9, 12, 1, tzinfo=datetime.timezone.utc), {})
    item.add_asset("vh", pystac.Asset("s3://eodata/x-vh.tiff"))
    item.add_asset("vv", pystac.Asset("s3://eodata/x-vv.tiff"))
    return item


MIRROR = {"co": "https://blob/vv?sig", "cross": "https://blob/vh?sig"}


def _run(monkeypatch: pytest.MonkeyPatch, cfg_update: Dict[str, Any], fail_on: str) -> List[str]:
    from app.config import get_settings
    from app.pipeline import orchestrator
    from app.pipeline.detection import SarReadError

    live = get_settings()
    for key, value in cfg_update.items():
        monkeypatch.setattr(live, key, value)
    monkeypatch.setattr(orchestrator, "fetch_sentinel1_data", lambda *a, **k: _item())
    monkeypatch.setattr(orchestrator.planetary, "bands_for", lambda *a, **k: dict(MIRROR))
    calls: List[str] = []

    def detect(url: str, *_a: Any, **_k: Any) -> Any:
        calls.append(url)
        if fail_on in url:
            raise SarReadError("forbidden", "Accès S3 refusé (403)")
        return [], None

    monkeypatch.setattr(orchestrator, "detect_ships_with_window", detect)
    orchestrator.run_kraken_pipeline(scene_id=COPERNICUS_ID, actor="tester")
    return calls


def test_without_keys_the_scan_reads_the_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline.scan_state import snapshot

    calls = _run(monkeypatch, {}, fail_on="never")
    assert calls == ["https://blob/vh?sig"]
    assert snapshot()["status"] == "done" and snapshot()["stats"]["pixel_source"] == "planetary"


def test_s3_failure_falls_back_to_the_same_scene_on_the_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline.scan_state import snapshot

    calls = _run(monkeypatch, {"access_key": "a", "secret_key": "b"}, fail_on="s3://")
    assert calls == ["s3://eodata/x-vh.tiff", "https://blob/vh?sig"]
    stats = snapshot()["stats"]
    assert snapshot()["status"] == "done" and stats["pixel_source"] == "planetary" and stats["copernicus_error"] == "forbidden"


def test_forced_copernicus_never_uses_the_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.pipeline.scan_state import snapshot

    calls = _run(monkeypatch, {"access_key": "a", "secret_key": "b", "sar_source": "copernicus"}, fail_on="s3://")
    assert calls == ["s3://eodata/x-vh.tiff"]
    assert snapshot()["status"] == "error" and "403" in snapshot()["message"]
