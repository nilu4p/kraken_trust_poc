"""xView3 second opinion on radar targets — information only, never a decision.

The optional ``xview3`` service (``make up-ai``) runs one model of the xView3
first-place solution (MIT), trained on Sentinel-1 VH/VV to find dark vessels.
For each target the CFAR detector already kept, KRAKEN sends a 2.56 km radar
crop of both polarisations; the service answers four numbers read at the
target's position:

- ``objectness`` — probability that there is a maritime object there at all;
- ``vessel`` — if there is one, probability it is a vessel (vs. a fixed
  structure: platform, buoy, rock);
- ``fishing`` — if it is a vessel, probability it is fishing;
- ``length_m`` — estimated length.

These numbers are displayed and sealed with the evidence. They are never read
by the quality score, the risk score, the rules or incident creation: the
operator decides. Any failure (service off, timeout, error) leaves the scan
unchanged and is reported in the scan statistics.

Calibration: the GRD pixels are amplitudes (DN). The model expects calibrated
σ⁰ in dB; ``20·log10(DN)`` is shifted per tile so that the tile's median sea
level matches the median of the xView3 training scenes (VH ≈ −26.3 dB,
VV ≈ −16.4 dB). This is an approximation of the radiometric calibration — good
enough for the contrast-based model, documented as such.
"""

from __future__ import annotations

import base64
import logging
from typing import Any, Dict, List, Optional

import httpx
import numpy as np

from app.config import Settings, get_settings
from app.sources import health

logger = logging.getLogger(__name__)

SOURCE = "xView3 (1re place, MIT) — EfficientNet-B4 U-Net"
MODEL_SHA256 = "1deeea5b372611b737edde9f73c6d1ff6728293e21bf987f67daa721987819cf"
SEA_DB = {"vh": -26.3, "vv": -16.4}
BATCH = 16


class Xview3Unavailable(RuntimeError):
    pass


def enabled(settings: Optional[Settings] = None) -> bool:
    return bool((settings or get_settings()).xview3_url)


def to_db(dn: np.ndarray, polarisation: str) -> np.ndarray:
    """Amplitude DN → approximately calibrated dB (tile median aligned on the xView3 sea level)."""
    values = np.asarray(dn, dtype=np.float32)
    with np.errstate(divide="ignore", invalid="ignore"):
        db = np.where(values > 0, 20.0 * np.log10(np.maximum(values, 1e-6)), np.nan)
    sea = np.nanmedian(db) if np.isfinite(db).any() else 0.0
    return (db - sea + SEA_DB[polarisation]).astype(np.float32)


def crop(array: np.ndarray, row: int, col: int, side: int) -> Dict[str, Any]:
    """``side`` × ``side`` crop around (row, col), clipped to the tile; position of the target inside it."""
    half = side // 2
    top, left = max(0, row - half), max(0, col - half)
    bottom, right = min(array.shape[0], top + side), min(array.shape[1], left + side)
    top, left = max(0, bottom - side), max(0, right - side)
    return {"data": array[top:bottom, left:right], "row": row - top, "col": col - left}


def _b64(array: np.ndarray) -> str:
    return base64.b64encode(np.ascontiguousarray(array, dtype=np.float16).tobytes()).decode()


def attach_crops(targets: List[Dict[str, Any]], vh_dn: np.ndarray, vv_dn: Optional[np.ndarray],
                 settings: Optional[Settings] = None) -> None:
    """Keep, on each target, the dB crops the service will score (both polarisations needed)."""
    cfg = settings or get_settings()
    if vv_dn is None or not targets:
        return
    vh_db, vv_db = to_db(vh_dn, "vh"), to_db(vv_dn, "vv")
    for target in targets:
        a = crop(vh_db, target["row"], target["col"], cfg.xview3_chip_px)
        b = crop(vv_db, target["row"], target["col"], cfg.xview3_chip_px)
        if min(a["data"].shape) < 32:
            continue
        target["_xview3_chip"] = {"shape": list(a["data"].shape), "row": a["row"], "col": a["col"],
                                  "vh": _b64(a["data"]), "vv": _b64(b["data"])}


def score(targets: List[Dict[str, Any]], settings: Optional[Settings] = None) -> int:
    """Ask the service for its opinion on the targets carrying a crop; returns how many were scored.

    Sets ``target["xview3"]`` and always removes the crops. Raises :class:`Xview3Unavailable`.
    """
    cfg = settings or get_settings()
    pending = [t for t in targets if t.get("_xview3_chip")]
    chips = [{"id": str(i), **t.pop("_xview3_chip")} for i, t in enumerate(pending)]
    for t in targets:
        t.pop("_xview3_chip", None)
    if not chips:
        return 0
    url = cfg.xview3_url.rstrip("/") + "/score"
    scored = 0
    try:
        with httpx.Client(timeout=cfg.xview3_timeout_s) as client:
            for start in range(0, len(chips), BATCH):
                response = client.post(url, json={"chips": chips[start:start + BATCH]})
                if response.status_code == 503:
                    raise Xview3Unavailable("modèle xView3 en cours de chargement ou indisponible")
                response.raise_for_status()
                for result in response.json().get("results", []):
                    target = pending[int(result["id"])]
                    target["xview3"] = {
                        "objectness": float(result["objectness"]),
                        "vessel": float(result["vessel"]),
                        "fishing": float(result["fishing"]),
                        "length_m": float(result["length_m"]),
                    }
                    scored += 1
    except Xview3Unavailable as exc:
        health.record("xview3", False, str(exc))
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        health.record("xview3", False, type(exc).__name__)
        raise Xview3Unavailable(f"service xView3 injoignable ou réponse invalide ({type(exc).__name__})") from exc
    health.record("xview3", True)
    return scored


def status(settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Service state for the AI panel (never raises)."""
    cfg = settings or get_settings()
    if not cfg.xview3_url:
        return {"enabled": False}
    try:
        response = httpx.get(cfg.xview3_url.rstrip("/") + "/health", timeout=4)
        body = response.json()
        return {"enabled": True, "ready": bool(body.get("ready")), "error": body.get("error"),
                "model": body.get("model"), "sha256": body.get("sha256"),
                "sha256_ok": body.get("sha256") == MODEL_SHA256}
    except (httpx.HTTPError, ValueError) as exc:
        return {"enabled": True, "ready": False, "error": f"injoignable ({type(exc).__name__})"}


def objectness_of(props: Dict[str, Any]) -> Optional[float]:
    """Opinion read by the agreement measure (Administration › Supervision): "is there an object?"."""
    return (props.get("xview3") or {}).get("objectness")


def summary(opinion: Optional[Dict[str, Any]]) -> Optional[str]:
    """One French line for the explanation panel."""
    if not opinion:
        return None
    pct = lambda v: f"{round(v * 100)} %"  # noqa: E731 — French typography
    text = f"objet {pct(opinion['objectness'])}"
    if opinion["objectness"] >= 0.3:
        text += f" · navire {pct(opinion['vessel'])} · pêche {pct(opinion['fishing'])} · ~{round(opinion['length_m'])} m"
    return text
