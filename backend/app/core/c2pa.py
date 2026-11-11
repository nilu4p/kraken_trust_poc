"""C2PA v2.4 sealer with a signed sidecar fallback (never crash).

Verification only trusts what KRAKEN signed:
- the embedded C2PA manifest must chain to a KRAKEN root (current or archived,
  see :mod:`app.core.pki`): a manifest re-signed with any other key is rejected;
- ``manifest.json`` (readable sidecar) carries an ES256 ``seal`` by the sealer
  certificate; without C2PA, a valid seal + matching hashes gives ``fallback``.
Statuses: ``verified`` (C2PA trusted), ``fallback`` (sealed sidecar only),
``unverifiable`` (old unsealed sidecar, cannot be proven), ``tampered``.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from PIL import Image, ImageDraw, ImageFont

from app import __version__
from app.config import Settings, get_settings
from app.core.pki import leaf_fingerprint_sha256, root_fingerprint_sha256, signing_key_pem, trusted_roots, trusted_roots_pem

logger = logging.getLogger(__name__)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


CROP_PADDING_PX = 64


def _render_sar_crop(path: Path, sar_window: np.ndarray, vessels: List[Dict[str, Any]]) -> bool:
    """Crop the real SAR backscatter around the detections. Returns False if unusable."""
    pixels = [(int(v["row"]), int(v["col"])) for v in vessels if "row" in v and "col" in v]
    if sar_window.ndim != 2 or sar_window.size == 0 or not pixels:
        return False
    rows = [r for r, _ in pixels]
    cols = [c for _, c in pixels]
    top = max(0, min(rows) - CROP_PADDING_PX)
    left = max(0, min(cols) - CROP_PADDING_PX)
    bottom = min(sar_window.shape[0], max(rows) + CROP_PADDING_PX + 1)
    right = min(sar_window.shape[1], max(cols) + CROP_PADDING_PX + 1)
    crop = np.nan_to_num(sar_window[top:bottom, left:right].astype(np.float32))
    if crop.size == 0:
        return False

    low, high = np.percentile(crop, (2.0, 99.5))
    scaled = np.clip((crop - low) / ((high - low) or 1.0), 0.0, 1.0)
    img = Image.fromarray((scaled * 255).astype(np.uint8)).convert("RGB")
    draw = ImageDraw.Draw(img)
    for row, col in pixels:
        y, x = row - top, col - left
        draw.rectangle((x - 6, y - 6, x + 6, y + 6), outline=(255, 59, 59), width=1)

    scale = 640 / max(img.width, img.height)
    if scale > 1:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.Resampling.NEAREST)
    img.save(path, format="JPEG", quality=92)
    return True


def _normalise(chip: np.ndarray) -> np.ndarray:
    chip = np.nan_to_num(chip.astype(np.float32))
    valid = chip[chip > 0]
    if valid.size == 0:
        return np.zeros_like(chip, dtype=np.uint8)
    low, high = np.percentile(valid, (2.0, 99.5))
    return (np.clip((chip - low) / ((high - low) or 1.0), 0.0, 1.0) * 255).astype(np.uint8)


def _render_chip_sheet(path: Path, vessels: List[Dict[str, Any]]) -> bool:
    """Contact sheet of real SAR chips (one per target, up to 6). False if no chip."""
    chips = [(v, v["chip"]) for v in vessels if isinstance(v.get("chip"), np.ndarray) and v["chip"].size]
    if not chips:
        return False
    chips = chips[:6]
    tile, label_h = 300, 22
    columns = min(3, len(chips))
    rows = (len(chips) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * tile, rows * (tile + label_h)), color=(7, 11, 16))
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for index, (vessel, chip) in enumerate(chips):
        img = Image.fromarray(_normalise(chip)).convert("RGB")
        img = img.resize((tile, tile), Image.Resampling.NEAREST)
        x, y = (index % columns) * tile, (index // columns) * (tile + label_h)
        sheet.paste(img, (x, y + label_h))
        cx, cy = x + tile // 2, y + label_h + tile // 2
        draw.rectangle((cx - 18, cy - 18, cx + 18, cy + 18), outline=(255, 59, 59), width=2)
        draw.text(
            (x + 6, y + 5),
            f"{vessel.get('id', 'SAR')}  {vessel['lat']:.4f},{vessel['lon']:.4f}  "
            f"conf {float(vessel.get('confidence', 0)):.2f}",
            fill=(0, 255, 255),
            font=font,
        )
    sheet.save(path, format="JPEG", quality=92)
    return True


def _render_vessel_chip(path: Path, vessel: Dict[str, Any]) -> bool:
    """Single-target SAR chip (400 px) for the dashboard drawer. False if no chip."""
    chip = vessel.get("chip")
    if not isinstance(chip, np.ndarray) or chip.size == 0:
        return False
    img = Image.fromarray(_normalise(chip)).convert("RGB").resize((400, 400), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(img)
    draw.rectangle((176, 176, 224, 224), outline=(255, 59, 59), width=2)
    draw.text((8, 8), f"{vessel.get('id', 'SAR')}  {vessel['lat']:.4f}, {vessel['lon']:.4f}", fill=(0, 255, 255))
    img.save(path, format="JPEG", quality=92)
    return True


def _render_radar_crop(path: Path, alert_id: str, vessels: List[Dict[str, Any]], timestamp: str) -> None:
    """Render a tactical JPEG card (fallback when no SAR window is provided)."""
    img = Image.new("RGB", (640, 360), color=(12, 16, 20))
    draw = ImageDraw.Draw(img)
    font = ImageFont.load_default()
    draw.rectangle((8, 8, 632, 352), outline=(0, 255, 255), width=2)
    draw.text((24, 24), "ALERTE KRAKEN — ZEE GUYANE", fill=(255, 64, 64), font=font)
    draw.text((24, 56), f"ID: {alert_id}", fill=(200, 220, 230), font=font)
    draw.text((24, 80), f"Horodatage: {timestamp}", fill=(0, 255, 160), font=font)
    draw.text(
        (24, 104),
        f"Navires fantomes: {len(vessels)}  |  AIS: NEGATIF",
        fill=(255, 200, 80),
        font=font,
    )
    for index, vessel in enumerate(vessels[:6]):
        draw.text(
            (24, 150 + index * 22),
            f"  {vessel.get('id', 'SAR')}  {vessel.get('lat'):.4f}, {vessel.get('lon'):.4f}"
            f"  conf={vessel.get('confidence', 0):.2f}",
            fill=(230, 230, 230),
            font=font,
        )
    img.save(path, format="JPEG", quality=90)


def _try_c2pa_sign(source: Path, dest: Path, keys_dir: Path, assertions: Dict[str, Any]) -> bool:
    """Embed a C2PA manifest. Returns False on any library/tool failure."""
    try:
        from c2pa import Builder, C2paSignerInfo, Signer
    except Exception as exc:  # pragma: no cover - optional native lib
        logger.warning("c2pa-python unavailable: %s", exc)
        return False

    try:
        certs = (keys_dir / "fullchain.pem").read_bytes()
        private_key = signing_key_pem(get_settings().model_copy(update={"keys_dir": keys_dir}))
        manifest = {
            "claim_generator": f"KRAKEN-Trust/{__version__}",
            "title": f"Alerte Dark Vessel - {assertions.get('alert_id')}",
            "format": "image/jpeg",
            "assertions": [
                {
                    "label": "c2pa.actions",
                    "data": {
                        "actions": [
                            {
                                "action": "c2pa.created",
                                "softwareAgent": "KRAKEN-Trust SAR pipeline",
                                # Derived from Sentinel-1 radar measurements by an algorithm.
                                "digitalSourceType": "http://cv.iptc.org/newscodes/digitalsourcetype/computationalCapture",
                            }
                        ]
                    },
                },
                {
                    "label": "org.kraken.tactical",
                    "data": assertions,
                },
            ],
        }
        tsa = get_settings().c2pa_tsa_url
        signer_info = C2paSignerInfo(
            alg="es256",
            sign_cert=certs,
            private_key=private_key,
            # RFC 3161 timestamp authority: the signing time no longer rests on the server clock alone.
            ta_url=tsa.encode() if tsa else None,
        )
        signer = Signer.from_info(signer_info)
        builder = Builder.from_json(manifest)
        builder.sign_file(str(source), str(dest), signer)
        return dest.is_file() and dest.stat().st_size > 0
    except Exception as exc:
        logger.warning("C2PA signing failed, using SHA-256 sidecar: %s", exc)
        return False


def _canonical(payload: Dict[str, Any]) -> bytes:
    return json.dumps({k: v for k, v in payload.items() if k != "seal"}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False).encode()


def _seal(payload: Dict[str, Any], cfg: Settings) -> None:
    """ES256 signature of the sidecar by the sealer certificate (verifiable without C2PA)."""
    key = serialization.load_pem_private_key(signing_key_pem(cfg), password=None)
    signature = key.sign(_canonical(payload), ec.ECDSA(hashes.SHA256()))  # type: ignore[union-attr, call-arg]
    payload["seal"] = {
        "alg": "ES256",
        "certificate": (cfg.keys_dir / "cert.pem").read_text(encoding="utf-8"),
        "signature": base64.b64encode(signature).decode(),
    }


def _seal_valid(payload: Dict[str, Any], cfg: Settings) -> bool:
    """The seal was made by a certificate issued by a KRAKEN root, over this exact content."""
    seal = payload.get("seal") or {}
    try:
        cert = x509.load_pem_x509_certificate(str(seal["certificate"]).encode())
        if not any(_issued_by(cert, root) for root in trusted_roots(cfg)):
            return False
        cert.public_key().verify(  # type: ignore[call-arg]
            base64.b64decode(seal["signature"]), _canonical(payload), ec.ECDSA(hashes.SHA256()))  # type: ignore[arg-type]
        return True
    except Exception:
        return False


def _issued_by(cert: x509.Certificate, root: x509.Certificate) -> bool:
    try:
        cert.verify_directly_issued_by(root)
        return True
    except Exception:
        return False


def _files_match(dossier: Path, expected: Dict[str, Any]) -> bool:
    for name, digest in expected.items():
        path = dossier / Path(str(name)).name
        if not path.is_file() or _sha256_file(path) != digest:
            return False
    return True


def _write_sidecar(
    dossier: Path,
    image_path: Path,
    assertions: Dict[str, Any],
    status: str,
    cfg: Settings,
) -> Dict[str, Any]:
    payload = {
        "title": f"Alerte Dark Vessel - {assertions.get('alert_id')}",
        "claim_generator": f"KRAKEN-Trust/{__version__}",
        "format": "image/jpeg",
        "instance_id": f"xmp:iid:{uuid.uuid4()}",
        "hard_binding": {
            "alg": "SHA-256",
            "hash": _sha256_file(image_path),
            "file": image_path.name,
        },
        "signature": {
            "issuer": "KRAKEN Root CA",
            "status": status,
            "pki_fingerprint": assertions.get("pki_fingerprint", ""),
            "root_fingerprint": root_fingerprint_sha256(cfg),
            "timestamp": assertions.get("timestamp"),
        },
        "assertions": [
            {"label": "org.kraken.tactical", "data": assertions},
        ],
        # Per-target chips are bound by hash inside the signed assertions too.
        "ingredients": [
            {"file": name, "alg": "SHA-256", "hash": digest}
            for name, digest in (assertions.get("chips") or {}).items()
        ],
    }
    try:
        _seal(payload, cfg)
    except Exception as exc:  # proof still written; validate_proof reports it "unverifiable"
        logger.error("Sidecar seal failed (%s): proof %s left unsealed", type(exc).__name__, dossier.name)
    sidecar = dossier / "manifest.json"
    sidecar.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def validate_proof(dossier: Path, settings: Optional[Settings] = None) -> str:
    """Return ``verified``, ``fallback``, ``unverifiable`` or ``tampered`` for a proof folder."""
    cfg = settings or get_settings()
    image = dossier / "radar_crop.jpg"
    if not image.is_file():
        return "tampered"
    state, tactical = _read_embedded(image, cfg)
    if state == "Trusted":
        # Chips are bound by hash inside the signed assertion, not the editable sidecar.
        return "verified" if _files_match(dossier, tactical.get("chips") or {}) else "tampered"
    try:
        data = json.loads((dossier / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return "tampered"
    binding = data.get("hard_binding") or {}
    expected = {binding.get("file") or image.name: binding.get("hash")}
    expected.update({i.get("file"): i.get("hash") for i in data.get("ingredients") or []})
    if not _files_match(dossier, expected):
        return "tampered"
    if data.get("seal"):
        return "fallback" if _seal_valid(data, cfg) else "tampered"
    # Sealed before sidecar signatures existed: hashes match but nothing proves who wrote them.
    return "unverifiable"


def _read_embedded(image: Path, cfg: Settings) -> Tuple[Optional[str], Dict[str, Any]]:
    """(validation state against the KRAKEN roots, KRAKEN tactical assertion) of the embedded manifest."""
    anchors = trusted_roots_pem(cfg)
    try:
        from c2pa import Context, Reader
        from c2pa import Settings as C2paSettings

        context = Context(settings=C2paSettings.from_dict(
            {"trust": {"trust_anchors": anchors}, "verify": {"verify_trust": True}}))
        with image.open("rb") as handle:
            data = json.loads(Reader("image/jpeg", handle, context=context).json())
    except Exception:
        return None, {}
    state = data.get("validation_state") or ("Valid" if data.get("active_manifest") else None)
    if state == "Trusted" and not anchors:
        state = "Valid"
    manifest = (data.get("manifests") or {}).get(data.get("active_manifest") or "") or {}
    tactical = next((a.get("data") or {} for a in manifest.get("assertions") or [] if a.get("label") == "org.kraken.tactical"), {})
    return state, tactical


def embedded_manifest_state(image: Path, settings: Optional[Settings] = None) -> Optional[str]:
    """C2PA validation state of the embedded manifest, KRAKEN roots as the only trust anchors.

    ``Trusted`` = signed by a KRAKEN sealer certificate; ``Valid`` = well-formed
    but signed by someone else (not accepted as a proof); None = no manifest.
    """
    return _read_embedded(image, settings or get_settings())[0]


def generate_and_seal_alert(
    vessels: List[Dict[str, Any]],
    *,
    stac_source: str = "",
    settings: Optional[Settings] = None,
    sar_window: Optional[np.ndarray] = None,
    ais_tracks: Optional[List[Dict[str, Any]]] = None,
    scene: Optional[Dict[str, Any]] = None,
    sea_state: Optional[Dict[str, Any]] = None,
) -> Optional[Path]:
    """Write GeoJSON + sealed JPEG under ``preuves/``. Never raises to callers."""
    if not vessels:
        logger.info("No dark vessels to seal")
        return None

    cfg = settings or get_settings()
    cfg.preuves_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    alert_id = str(uuid.uuid4())[:8]
    dossier = cfg.preuves_dir / f"alerte_{timestamp}_{alert_id}"
    dossier.mkdir(parents=True, exist_ok=True)

    unsigned = dossier / "radar_crop_unsigned.jpg"
    sealed = dossier / "radar_crop.jpg"
    fingerprint = leaf_fingerprint_sha256(cfg)
    coordinates = [[float(v["lon"]), float(v["lat"])] for v in vessels]
    assertions: Dict[str, Any] = {
        "alert_id": alert_id,
        "stac_source": stac_source,
        # CFAR signal-to-noise mapped to 0.5–0.99 (not a machine-learning score); see detection_quality.
        "detection_confidence": [float(v.get("confidence", 0.0)) for v in vessels],
        "timestamp": now.isoformat(),
        "coordinates": coordinates,
        "pki_fingerprint": fingerprint,
        "zone": "EEZ Guyane française",
        "legal_zones": [v.get("zone") for v in vessels],
        "risk": [
            {"id": v.get("id"), "score": (v.get("risk") or {}).get("score"), "level": (v.get("risk") or {}).get("level")}
            for v in vessels
        ],
        "ais_source": vessels[0].get("ais_source"),
        "scene_datetime": ((scene or {}).get("properties") or {}).get("datetime"),
        # Same product either way; where its pixels were read (Copernicus S3 or Planetary Computer mirror).
        "pixel_source": ((scene or {}).get("properties") or {}).get("pixel_source"),
        "sea_state": {
            k: (sea_state or {}).get(k) for k in ("wind_speed_ms", "wave_height_m", "time", "source")
        },
        "detection_quality": [
            {"id": v.get("id"), "score": (v.get("quality") or {}).get("score")} for v in vessels
        ],
    }
    if any(v.get("ai_confidence") is not None for v in vessels):
        # Which model gave which opinion, sealed with the evidence (reproducible, auditable).
        assertions["ai_detector"] = {
            "mode": "active" if cfg.ai_detector_mode == 2 else "observation",
            "model_sha256": cfg.ai_model_sha256,
            "opinions": [{"id": v.get("id"), "confidence": v.get("ai_confidence"), "ai_only": bool(v.get("ai_only"))}
                         for v in vessels],
        }
    if any(v.get("xview3") for v in vessels):
        # Information-only opinion (never used in scores): sealed so it can be audited later.
        from app.sources import xview3

        assertions["xview3_opinion"] = {
            "role": "information only — no effect on detection, scores or incidents",
            "model": xview3.SOURCE,
            "model_sha256": xview3.MODEL_SHA256,
            "calibration": "20·log10(DN), tile median aligned on the xView3 sea level (VH -26.3 dB, VV -16.4 dB)",
            "opinions": [{"id": v.get("id"), **v["xview3"]} for v in vessels if v.get("xview3")],
        }

    try:
        chips: Dict[str, str] = {}
        for nav in vessels:
            chip_name = f"chip_{nav.get('id', 'SAR')}.jpg"
            if _render_vessel_chip(dossier / chip_name, nav):
                chips[chip_name] = _sha256_file(dossier / chip_name)
        assertions["chips"] = chips

        if not _render_chip_sheet(unsigned, vessels) and (
            sar_window is None or not _render_sar_crop(unsigned, sar_window, vessels)
        ):
            _render_radar_crop(unsigned, alert_id, vessels, timestamp)
        c2pa_ok = _try_c2pa_sign(unsigned, sealed, cfg.keys_dir, assertions)
        if not c2pa_ok:
            sealed.write_bytes(unsigned.read_bytes())
            status = "fallback"
        else:
            status = "VALID"
        if unsigned.exists() and unsigned != sealed:
            unsigned.unlink(missing_ok=True)
        sidecar = _write_sidecar(dossier, sealed, assertions, status, cfg)
        image_url = f"/preuves/{dossier.name}/radar_crop.jpg"

        geojson: Dict[str, Any] = {"type": "FeatureCollection", "features": []}
        scene_props = (scene or {}).get("properties") or {}
        for nav in vessels:
            risk = nav.get("risk") or {}
            quality = nav.get("quality") or {}
            chip_name = f"chip_{nav.get('id', 'SAR')}.jpg"
            vessel_image = f"/preuves/{dossier.name}/{chip_name}" if chip_name in chips else image_url
            geojson["features"].append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [nav["lon"], nav["lat"]]},
                    "properties": {
                        "type": "dark_vessel",
                        "alert_id": alert_id,
                        "vessel_id": nav.get("id", "SAR"),
                        "timestamp": timestamp,
                        "confidence": nav.get("confidence"),
                        "color": "#FF0000",
                        "description": (
                            f"Navire fantôme détecté par SAR ({nav.get('id', 'SAR')}). "
                            "Transpondeur AIS désactivé."
                        ),
                        "image_url": vessel_image,
                        "sheet_url": image_url,
                        "c2pa_sealed": status == "VALID",
                        "c2pa_status": "verified" if status == "VALID" else "fallback",
                        "pki_fingerprint": fingerprint,
                        "stac_source": stac_source,
                        "sha256": sidecar["hard_binding"]["hash"],
                        "zone": nav.get("zone"),
                        "length_m": nav.get("length_m"),
                        "snr": nav.get("snr"),
                        "ais_source": nav.get("ais_source"),
                        "size_mismatch": nav.get("size_mismatch"),
                        "risk_score": risk.get("score"),
                        "risk_level": risk.get("level"),
                        "risk_reasons": risk.get("reasons", []),
                        "quality_score": quality.get("score"),
                        "quality_level": quality.get("level"),
                        "quality_flags": quality.get("flags", []),
                        "dual_pol": nav.get("dual_pol"),
                        "ai_confidence": nav.get("ai_confidence"),
                        "ai_only": bool(nav.get("ai_only")),
                        "xview3": nav.get("xview3"),
                        "shore_m": (nav.get("coast") or {}).get("shore_m"),
                        "depth_m": (nav.get("ocean") or {}).get("depth_m"),
                        "chlorophyll_mg_m3": (nav.get("ocean") or {}).get("chlorophyll_mg_m3"),
                        "fishing_limits_km": nav.get("fishing_limits"),
                        "elongation": nav.get("elongation"),
                        "pixels": nav.get("pixels"),
                        "scene_datetime": scene_props.get("datetime"),
                        "sar_mode": scene_props.get("mode"),
                    },
                }
            )
        for ship in ais_tracks or []:
            geojson["features"].append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [ship["lon"], ship["lat"]]},
                    "properties": {
                        "type": "ais_vessel",
                        "mmsi": ship.get("mmsi", ""),
                        "name": ship.get("name", ""),
                        "timestamp": timestamp,
                        "time_utc": ship.get("time_utc"),
                        "sog_knots": ship.get("sog_knots"),
                        "cog_deg": ship.get("cog_deg"),
                        "source": ship.get("source", "mock"),
                        "watchlist": ship.get("watchlist"),
                        "color": "#3DFF8A",
                    },
                }
            )
        if scene and scene.get("geometry"):
            geojson["features"].append(scene)
        geojson_path = dossier / "kraken_alerts.geojson"
        geojson_path.write_text(json.dumps(geojson, indent=2), encoding="utf-8")
        logger.info("Tactical alert sealed at %s (c2pa=%s)", geojson_path, status)
        return geojson_path
    except Exception as exc:
        logger.exception("Alert sealing failed: %s", exc)
        return None
