"""Open-source AI ship detector (ONNX) as a second opinion on the physical CFAR detections.

Default model: YOLOv8n trained on SAR ship chips (Hugging Face
``MeWan2808/yolov8n-sar-vessel-detection``, ONNX, ~12 MB, CPU via onnxruntime —
no PyTorch). The model card says MIT but the exported file declares the
Ultralytics AGPL-3.0 licence: an administrator must validate the licence before
enabling it (``AI_MODEL_URL`` / ``AI_MODEL_SHA256`` can point to another model
with the same YOLOv8 output format).

The AI never decides alone:
- ``ai_detector_mode = 1`` (observation): the model scores every CFAR target
  (``ai_confidence``) — shown and sealed, no effect on quality or risk; its
  agreement with the operators' verdicts is measured (:func:`evaluation`);
- ``ai_detector_mode = 2`` (active): once validated on local data, the opinion weighs on
  the detection quality (confirmation +, denial −) and very confident AI-only
  detections become candidates, flagged as such.

The model file is downloaded once into the cache, checked against its SHA-256,
and never executed if it does not match.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import numpy as np

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

INPUT = 640
STRIDE = 512          # 640 px crops overlapping by 128 px: a ship on a seam is seen whole once
IOU_NMS = 0.5
MATCH_MARGIN_PX = 3
_lock = threading.Lock()
_sessions: Dict[str, Any] = {}
_status: Dict[str, Any] = {"loaded": False, "error": None}


class AiUnavailable(RuntimeError):
    pass


def status() -> Dict[str, Any]:
    cfg = get_settings()
    return {**_status, "mode": cfg.ai_detector_mode, "enabled": cfg.ai_detector_mode >= 1,
            "active": cfg.ai_detector_mode == 2, "model_url": cfg.ai_model_url, "model_sha256": cfg.ai_model_sha256}


def _model_path(cfg: Settings) -> Path:
    path = Path(cfg.cache_dir) / "models" / f"{cfg.ai_model_sha256[:16]}.onnx"
    if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == cfg.ai_model_sha256:
        return path
    if not cfg.ai_model_url.startswith("https://huggingface.co/"):
        raise AiUnavailable("modèle IA : seules les URL https://huggingface.co/ sont acceptées")
    logger.info("Downloading AI model %s", cfg.ai_model_url)
    request = urllib.request.Request(cfg.ai_model_url, headers={"User-Agent": "KRAKEN-Trust"})
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310 (https, fixed host)
        data = response.read(200 * 1024 * 1024)
    digest = hashlib.sha256(data).hexdigest()
    if digest != cfg.ai_model_sha256:
        raise AiUnavailable(f"empreinte du modèle IA inattendue ({digest[:12]}…) : fichier refusé")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _session(cfg: Settings) -> Any:
    with _lock:
        if cfg.ai_model_sha256 in _sessions:
            return _sessions[cfg.ai_model_sha256]
        try:
            import onnxruntime as ort
        except ImportError as exc:
            _status.update(loaded=False, error="onnxruntime non installé")
            raise AiUnavailable("onnxruntime non installé") from exc
        try:
            options = ort.SessionOptions()
            options.intra_op_num_threads = 2  # the scan worker shares the host: bounded CPU
            options.inter_op_num_threads = 1
            session = ort.InferenceSession(str(_model_path(cfg)), options, providers=["CPUExecutionProvider"])
        except AiUnavailable as exc:
            _status.update(loaded=False, error=str(exc))
            raise
        except Exception as exc:
            _status.update(loaded=False, error=f"{type(exc).__name__}: {str(exc)[:120]}")
            raise AiUnavailable(_status["error"]) from exc
        _sessions[cfg.ai_model_sha256] = session
        _status.update(loaded=True, error=None)
        return session


def to_display(window: np.ndarray) -> np.ndarray:
    """SAR amplitude → 8-bit image like the training chips (dB, robust 2–99.5 % stretch)."""
    db = 20.0 * np.log10(np.maximum(window.astype(np.float32), 1.0))
    valid = db[window > 0]
    if valid.size == 0:
        return np.zeros(window.shape, dtype=np.uint8)
    low, high = np.percentile(valid, [2, 99.5])
    scaled = np.clip((db - low) / max(high - low, 1e-6), 0, 1) * 255
    return scaled.astype(np.uint8)


def _nms(boxes: np.ndarray, scores: np.ndarray) -> List[int]:
    """Greedy non-maximum suppression on (x1, y1, x2, y2) boxes."""
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.clip(xx2 - xx1, 0, None) * np.clip(yy2 - yy1, 0, None)
        area = lambda b: (b[..., 2] - b[..., 0]) * (b[..., 3] - b[..., 1])  # noqa: E731
        iou = inter / np.maximum(area(boxes[i]) + area(boxes[rest]) - inter, 1e-6)
        order = rest[iou < IOU_NMS]
    return keep


def detect(window: np.ndarray, settings: Optional[Settings] = None) -> List[Dict[str, float]]:
    """Ship boxes in a SAR window (pixel coordinates of the window): ``{row, col, h, w, confidence}``."""
    cfg = settings or get_settings()
    session = _session(cfg)
    image = to_display(window)
    height, width = image.shape
    boxes, scores = [], []
    for top in range(0, max(1, height - INPUT + STRIDE), STRIDE):
        for left in range(0, max(1, width - INPUT + STRIDE), STRIDE):
            crop = np.zeros((INPUT, INPUT), dtype=np.uint8)
            part = image[top:top + INPUT, left:left + INPUT]
            crop[:part.shape[0], :part.shape[1]] = part
            tensor = np.repeat(crop[None, None].astype(np.float32) / 255.0, 3, axis=1)
            output = session.run(None, {session.get_inputs()[0].name: tensor})[0][0]  # (5, 8400): cx, cy, w, h, score
            keep = output[4] >= cfg.ai_min_confidence
            for cx, cy, w, h, score in output[:, keep].T:
                boxes.append([left + cx - w / 2, top + cy - h / 2, left + cx + w / 2, top + cy + h / 2])
                scores.append(float(score))
    if not boxes:
        return []
    boxes_a, scores_a = np.array(boxes, dtype=np.float32), np.array(scores, dtype=np.float32)
    result = []
    for i in _nms(boxes_a, scores_a):
        x1, y1, x2, y2 = boxes_a[i]
        result.append({"row": float((y1 + y2) / 2), "col": float((x1 + x2) / 2), "h": float(y2 - y1),
                       "w": float(x2 - x1), "confidence": round(float(scores_a[i]), 3)})
    return result


def annotate(targets: List[Dict[str, Any]], boxes: List[Dict[str, float]]) -> List[Dict[str, float]]:
    """Give each CFAR target the AI confidence of the box containing it (0 = the AI saw nothing);
    return the AI boxes that match no CFAR target."""
    used = set()
    for target in targets:
        best = 0.0
        for index, box in enumerate(boxes):
            if (abs(target["row"] - box["row"]) <= box["h"] / 2 + MATCH_MARGIN_PX
                    and abs(target["col"] - box["col"]) <= box["w"] / 2 + MATCH_MARGIN_PX):
                used.add(index)
                best = max(best, box["confidence"])
        target["ai_confidence"] = round(best, 3)
    return [box for index, box in enumerate(boxes) if index not in used]


def _yolo_opinion(props: Dict[str, Any]) -> Optional[float]:
    return props.get("ai_confidence")


def evaluation(features: List[Dict[str, Any]],
               opinion: Callable[[Dict[str, Any]], Optional[float]] = _yolo_opinion) -> Dict[str, Any]:
    """Agreement of an AI opinion with the operators' verdicts on closed incidents.

    A target is "real" when its incident was closed as confirmed IUU or legitimate
    activity, "false" when closed as a false positive. AI "yes" = opinion ≥ 0.5.
    ``opinion`` reads the probability from the target's properties (YOLO by default).
    """
    counts = {"tp": 0, "fp": 0, "tn": 0, "fn": 0}
    for feature in features:
        props = feature.get("properties") or {}
        value = opinion(props)
        if value is None or props.get("incident_status") != "closed":
            continue
        real = props.get("incident_resolution") in ("confirmed_iuu", "legitimate")
        false = props.get("incident_resolution") == "false_positive"
        if not (real or false):
            continue
        said_yes = float(value) >= 0.5
        counts["tp" if real and said_yes else "fn" if real else "fp" if said_yes else "tn"] += 1
    total = sum(counts.values())
    precision = counts["tp"] / (counts["tp"] + counts["fp"]) if counts["tp"] + counts["fp"] else None
    recall = counts["tp"] / (counts["tp"] + counts["fn"]) if counts["tp"] + counts["fn"] else None
    return {
        **counts,
        "judged": total,
        "precision": round(precision, 3) if precision is not None else None,
        "recall": round(recall, 3) if recall is not None else None,
        "agreement": round((counts["tp"] + counts["tn"]) / total, 3) if total else None,
        "enough_to_decide": total >= 30,
    }
