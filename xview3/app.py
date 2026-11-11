"""xView3 second-opinion service for KRAKEN-Trust (information only).

Wraps one model of the xView3 first-place solution (Eugene Khvedchenya, MIT,
https://github.com/BloodAxe/xView3-The-First-Place-Solution): a CircleNet
(EfficientNet-B4 U-Net) trained on Sentinel-1 GRD VH/VV to find "dark"
vessels and tell vessels from fixed structures and fishing from non-fishing.

KRAKEN sends radar crops (VH and VV, calibrated in dB) centred on the targets
its own CFAR detector already found; this service answers, for each crop, the
model's percentages at the centre. It never adds or removes a target: the
answer is shown to the operator as an additional opinion, nothing more.

    GET  /health   -> {"ready": true, "model": ..., "sha256": ...}
    POST /score    {"chips": [{"id": "SAR-001", "shape": [h, w], "vh": b64 float16, "vv": b64 float16}]}
               -> {"results": [{"id", "objectness", "vessel", "fishing", "length_m", "offset_px"}], "elapsed_ms"}

Weights are downloaded once to ``XVIEW3_WEIGHTS`` and checked by SHA-256
before being unpickled (a .pth is a pickle: an unverified file is never loaded).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, str(Path(__file__).resolve().parent / "vendor"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("xview3")

MODEL_NAME = "211117_10_52_b4_unet_s2_leaky_valid_4fold_rfl_soft_bce_mse_regularized_flips_light_medium_fold0.pth"
MODEL_URL = f"https://github.com/BloodAxe/xView3-The-First-Place-Solution/releases/download/1.0/{MODEL_NAME}"
MODEL_SHA256 = "1deeea5b372611b737edde9f73c6d1ff6728293e21bf987f67daa721987819cf"
WEIGHTS = Path(os.environ.get("XVIEW3_WEIGHTS", "/models/b4_fold0.pth"))
PORT = int(os.environ.get("XVIEW3_PORT", "8091"))
MAX_CHIPS = 64          # per request
MAX_SIDE = 256          # px per chip side
SEARCH_RADIUS = 3       # output px (stride 2) around the centre = ±60 m
PIXEL_M = 10.0          # xView3 GRD pixel spacing (sizes are learnt in 10 m units)

_model = None
_lock = threading.Lock()
_state = {"ready": False, "error": None}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _fetch_weights() -> None:
    if WEIGHTS.exists() and _sha256(WEIGHTS) == MODEL_SHA256:
        return
    WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    partial = WEIGHTS.with_suffix(".part")
    logger.info("Downloading xView3 weights (79 MB) from %s", MODEL_URL)
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "kraken-xview3"})
    with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as out:
        while block := response.read(1 << 20):
            out.write(block)
    found = _sha256(partial)
    if found != MODEL_SHA256:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"xView3 weights rejected: SHA-256 {found} differs from the pinned {MODEL_SHA256}")
    partial.replace(WEIGHTS)


def build_model() -> torch.nn.Module:
    from pytorch_toolbelt.modules.encoders import TimmB4Encoder
    from xview3.centernet.models import CenterNetUNetModel, DecoupledHeadGroupNormLateShuffle

    encoder = TimmB4Encoder(pretrained=False, activation="silu", layers=[1, 2, 3, 4]).change_input_channels(2)
    head = DecoupledHeadGroupNormLateShuffle(channels=256, classifier_dim=128, objectness_dim=128, size_dim=64,
                                             offset_dim=16, dropout_rate=0.1, activation="silu", num_blocks=3)
    model = CenterNetUNetModel(encoder=encoder, decoder_features=[256, 384, 512], head=head, box_coder=None,
                               activation="silu", block_type="IRBlock", upsample_type="shuffle", num_extra_blocks=0)
    # Safe only because the file's SHA-256 was checked against the pinned value just before.
    checkpoint = torch.load(WEIGHTS, map_location="cpu", weights_only=False)
    model.load_state_dict(checkpoint["model_state_dict"], strict=True)
    return model.eval()


def load() -> None:
    global _model
    try:
        _fetch_weights()
        torch.set_num_threads(max(1, int(os.environ.get("XVIEW3_THREADS", "2"))))
        _model = build_model()
        _state["ready"] = True
        logger.info("xView3 model ready")
    except Exception as exc:  # the service stays up and reports why
        _state["error"] = f"{type(exc).__name__}: {exc}"
        logger.exception("xView3 model not loaded")


def normalize(db: np.ndarray) -> np.ndarray:
    """Input normalisation of the first-place solution (sigmoid around -20 dB)."""
    return 1.0 / (1.0 + np.exp(-(db + 20.0) * 0.18))


def _decode_array(text: str, shape: tuple) -> np.ndarray:
    raw = np.frombuffer(base64.b64decode(text), dtype=np.float16)
    if raw.size != shape[0] * shape[1]:
        raise ValueError("chip size does not match its shape")
    return raw.reshape(shape).astype(np.float32)


def _pad32(array: np.ndarray, side: int) -> np.ndarray:
    """Pad to ``side`` (multiple of 32) with the chip's median sea level, keeping the centre."""
    h, w = array.shape
    top, left = (side - h) // 2, (side - w) // 2
    out = np.full((side, side), float(np.nanmedian(array)), dtype=np.float32)
    out[top:top + h, left:left + w] = array
    return out


def score(chips: list) -> list:
    if not chips:
        return []
    side = 32 * int(np.ceil(max(max(c["shape"]) for c in chips) / 32))
    batch, centres = [], []
    for chip in chips:
        h, w = chip["shape"]
        vh, vv = _decode_array(chip["vh"], (h, w)), _decode_array(chip["vv"], (h, w))
        vh, vv = np.nan_to_num(vh, nan=-30.0), np.nan_to_num(vv, nan=-20.0)
        batch.append(np.stack([normalize(_pad32(vh, side)), normalize(_pad32(vv, side))]))
        top, left = (side - h) // 2, (side - w) // 2
        centres.append(((top + chip.get("row", h // 2)) // 2, (left + chip.get("col", w // 2)) // 2))
    with _lock, torch.no_grad():
        out = _model(torch.from_numpy(np.stack(batch).astype(np.float32)))
    objectness = out["CENTERNET_OUTPUT_OBJECTNESS_MAP"].sigmoid()[:, 0].numpy()
    vessel = out["CENTERNET_OUTPUT_VESSEL_MAP"].sigmoid()[:, 0].numpy()
    fishing = out["CENTERNET_OUTPUT_FISHING_MAP"].sigmoid()[:, 0].numpy()
    size = out["CENTERNET_OUTPUT_SIZE"][:, 0].numpy()
    results = []
    for index, (chip, (cy, cx)) in enumerate(zip(chips, centres)):
        r0, c0 = max(0, cy - SEARCH_RADIUS), max(0, cx - SEARCH_RADIUS)
        window = objectness[index, r0:cy + SEARCH_RADIUS + 1, c0:cx + SEARCH_RADIUS + 1]
        dy, dx = np.unravel_index(int(window.argmax()), window.shape)
        py, px = r0 + dy, c0 + dx
        results.append({
            "id": chip.get("id"),
            "objectness": round(float(objectness[index, py, px]), 3),
            "vessel": round(float(vessel[index, py, px]), 3),
            "fishing": round(float(fishing[index, py, px]), 3),
            "length_m": round(float(np.expm1(max(float(size[index, py, px]), 0.0)) * PIXEL_M), 1),
            "offset_px": [int((py - cy) * 2), int((px - cx) * 2)],
        })
    return results


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/health":
            return self._send(404, {"error": "not found"})
        self._send(200 if _state["ready"] else 503, {
            "ready": _state["ready"], "error": _state["error"], "model": MODEL_NAME, "sha256": MODEL_SHA256,
            "source": "xView3 first-place solution (MIT) — EfficientNet-B4 U-Net, fold 0",
        })

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/score":
            return self._send(404, {"error": "not found"})
        if not _state["ready"]:
            return self._send(503, {"error": _state["error"] or "model loading"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length > 40 * 1024 * 1024:
                return self._send(413, {"error": "request too large"})
            chips = json.loads(self.rfile.read(length)).get("chips", [])
            if not isinstance(chips, list) or len(chips) > MAX_CHIPS:
                return self._send(400, {"error": f"1 to {MAX_CHIPS} chips expected"})
            for chip in chips:
                h, w = (int(v) for v in chip["shape"])
                if not (16 <= h <= MAX_SIDE and 16 <= w <= MAX_SIDE):
                    return self._send(400, {"error": f"chip sides must be 16..{MAX_SIDE} px"})
                chip["shape"] = (h, w)
            started = time.monotonic()
            results = score(chips)
        except (KeyError, TypeError, ValueError) as exc:
            return self._send(400, {"error": f"bad request: {exc}"})
        self._send(200, {"results": results, "elapsed_ms": round((time.monotonic() - started) * 1000)})

    def log_message(self, fmt: str, *args) -> None:
        logger.info("%s " + fmt, self.address_string(), *args)


if __name__ == "__main__":
    threading.Thread(target=load, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
