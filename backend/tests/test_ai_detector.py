"""AI second opinion: decoding, matching with CFAR targets, modes, model integrity, evaluation."""

from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pytest

from app.config import get_settings
from app.pipeline import ai_detector
from app.pipeline.detection import _ai_opinion
from app.pipeline.quality import assess


class FakeSession:
    """onnxruntime stand-in: one ship box at (cx, cy) in every crop that contains the bright pixel."""

    def __init__(self, image_point: tuple, score: float = 0.9) -> None:
        self.point, self.score, self.calls = image_point, score, 0

    def get_inputs(self) -> List[Any]:
        return [type("I", (), {"name": "images"})()]

    def run(self, _outputs: Any, feeds: Dict[str, np.ndarray]) -> List[np.ndarray]:
        self.calls += 1
        crop = feeds["images"][0, 0]
        out = np.zeros((1, 5, 8400), dtype=np.float32)
        ys, xs = np.nonzero(crop > 0.99)
        if len(xs):
            out[0, :, 0] = [xs[0], ys[0], 12, 30, self.score]
            out[0, :, 1] = [xs[0] + 1, ys[0], 12, 30, self.score * 0.8]  # duplicate removed by NMS
        return [out]


def window_with_ship(row: int = 700, col: int = 300) -> np.ndarray:
    # Uniform sea: after the dB stretch only the ship reaches full scale (the fake model keys on it).
    data = np.full((1024, 1024), 40.0, dtype=np.float32)
    data[row, col] = 5000.0
    return data


def test_detect_decodes_crops_and_suppresses_duplicates(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession((700, 300))
    monkeypatch.setattr(ai_detector, "_session", lambda cfg: session)
    boxes = ai_detector.detect(window_with_ship())
    assert session.calls == 4  # 1024 px window → 2 × 2 overlapping 640 px crops
    assert len(boxes) == 1 and abs(boxes[0]["row"] - 700) < 1 and abs(boxes[0]["col"] - 300) < 1
    assert boxes[0]["confidence"] == pytest.approx(0.9)


def test_annotate_and_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_detector, "_session", lambda cfg: FakeSession((700, 300)))
    cfg = get_settings()
    targets = [{"row": 702, "col": 301, "snr": 20, "pixels": 10, "elongation": 3, "confidence": 0.9},
               {"row": 100, "col": 100, "snr": 20, "pixels": 10, "elongation": 3, "confidence": 0.9}]
    monkeypatch.setattr(cfg, "ai_detector_mode", 1)
    stats = {"boxes": 0, "ai_only": 0}
    assert _ai_opinion(window_with_ship(), targets, cfg, stats) is None
    assert targets[0]["ai_confidence"] == pytest.approx(0.9) and targets[1]["ai_confidence"] == 0.0
    base = {"lat": 5.5, "lon": -52.5, "snr": 20, "pixels": 10, "elongation": 3}
    neutral = assess(base, settings=cfg)["score"]
    observed = assess({**base, "ai_confidence": 0.9}, settings=cfg)
    assert observed["score"] == neutral and any("observation" in f for f in observed["flags"])
    monkeypatch.setattr(cfg, "ai_detector_mode", 2)
    assert assess({**base, "ai_confidence": 0.9}, settings=cfg)["score"] > neutral
    assert assess({**base, "ai_confidence": 0.0}, settings=cfg)["score"] < neutral


def test_active_mode_adds_ai_only_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ai_detector, "_session", lambda cfg: FakeSession((700, 300), score=0.8))
    cfg = get_settings()
    monkeypatch.setattr(cfg, "ai_detector_mode", 2)
    targets: List[Dict[str, Any]] = []
    stats = {"boxes": 0, "ai_only": 0}
    _ai_opinion(window_with_ship(), targets, cfg, stats)
    [candidate] = targets
    assert candidate["ai_only"] and stats["ai_only"] == 1 and candidate["snr"] > 0
    q = assess({**candidate, "lat": 5.5, "lon": -52.5}, settings=cfg)
    assert any("IA seule" in f for f in q["flags"])
    monkeypatch.setattr(cfg, "ai_detector_mode", 1)
    targets = []
    _ai_opinion(window_with_ship(), targets, cfg, stats)
    assert targets == []  # observation mode never adds a target


def test_model_with_unexpected_hash_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_a: Any) -> None:
            return None

        def read(self, _n: int = -1) -> bytes:
            return b"not the expected model"

    monkeypatch.setattr(ai_detector.urllib.request, "urlopen", lambda *_a, **_kw: Response())
    with pytest.raises(ai_detector.AiUnavailable, match="empreinte"):
        ai_detector._model_path(get_settings())
    monkeypatch.setattr(get_settings(), "ai_model_url", "https://evil.example/model.onnx")
    with pytest.raises(ai_detector.AiUnavailable, match="huggingface"):
        ai_detector._model_path(get_settings())


def test_evaluation_against_operator_verdicts() -> None:
    def f(ai: float, resolution: str, status: str = "closed") -> Dict[str, Any]:
        return {"properties": {"ai_confidence": ai, "incident_status": status, "incident_resolution": resolution}}

    result = ai_detector.evaluation([f(0.9, "confirmed_iuu"), f(0.8, "legitimate"), f(0.1, "false_positive"),
                                     f(0.7, "false_positive"), f(0.2, "confirmed_iuu"), f(0.9, "x", "triage")])
    assert (result["tp"], result["fp"], result["tn"], result["fn"]) == (2, 1, 1, 1)
    assert result["precision"] == pytest.approx(0.667, abs=1e-3) and result["judged"] == 5 and not result["enough_to_decide"]
