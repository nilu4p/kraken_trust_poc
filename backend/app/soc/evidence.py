"""Evidence vault: listing, integrity verification, chain of custody and judicial export.

Evidence folders (``preuves/alerte_*``) are only reachable through the
authenticated API. Every view / verification / export is written to the
tamper-evident audit trail, which doubles as the chain of custody.
"""

from __future__ import annotations

import datetime
import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import Settings, get_settings
from app.core import audit
from app.core.c2pa import embedded_manifest_state, validate_proof
from app.core.pki import root_fingerprint_sha256
from app.pipeline.aggregation import cached_proof_status
from app.soc.incidents import incidents_for_dossier, record_event

DOSSIER_RE = re.compile(r"^alerte_\d{8}_\d{6}_[0-9a-f]{8}$")
FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,120}$")
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".json": "application/json", ".geojson": "application/geo+json"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dossier_path(dossier: str, settings: Optional[Settings] = None) -> Path:
    """Validated evidence folder path (LookupError if invalid or missing)."""
    cfg = settings or get_settings()
    if not DOSSIER_RE.match(dossier or ""):
        raise LookupError("Dossier de preuves introuvable")
    root = Path(cfg.preuves_dir).resolve()
    path = (root / dossier).resolve()
    if path.parent != root or not path.is_dir():
        raise LookupError("Dossier de preuves introuvable")
    return path


def file_path(dossier: str, name: str, settings: Optional[Settings] = None) -> Path:
    folder = dossier_path(dossier, settings)
    if not FILE_RE.match(name or "") or Path(name).suffix.lower() not in MEDIA_TYPES:
        raise LookupError("Fichier introuvable")
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise LookupError("Fichier introuvable")
    return path


def media_type(path: Path) -> str:
    return MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")


def _summary(folder: Path, settings: Settings) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {}
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    assertions = next(
        (a.get("data", {}) for a in manifest.get("assertions", []) if a.get("label") == "org.kraken.tactical"), {}
    )
    created = None
    match = re.match(r"alerte_(\d{8})_(\d{6})_", folder.name)
    if match:
        created = datetime.datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(
            tzinfo=datetime.timezone.utc
        ).isoformat()
    files = [p for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in MEDIA_TYPES]
    return {
        "dossier": folder.name,
        "alert_id": folder.name.rsplit("_", 1)[-1],
        "created_at": created,
        "stac_source": assertions.get("stac_source"),
        "targets": len(assertions.get("coordinates") or []),
        "integrity": cached_proof_status(folder, settings),  # recomputed only when a file changes
        "signature_status": (manifest.get("signature") or {}).get("status"),
        "pki_fingerprint": assertions.get("pki_fingerprint"),
        "files": [{"name": p.name, "size": p.stat().st_size} for p in files],
        "incidents": incidents_for_dossier(folder.name, settings),
    }


def list_dossiers(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    cfg = settings or get_settings()
    root = Path(cfg.preuves_dir)
    if not root.is_dir():
        return []
    folders = [p for p in root.iterdir() if p.is_dir() and DOSSIER_RE.match(p.name)]
    return [_summary(p, cfg) for p in sorted(folders, key=lambda p: p.name, reverse=True)]


def verify(dossier: str, user: Dict[str, Any], ip: Optional[str] = None, settings: Optional[Settings] = None) -> Dict[str, Any]:
    """Full integrity report: per-file SHA-256 vs. sealed manifest + embedded C2PA state."""
    cfg = settings or get_settings()
    folder = dossier_path(dossier, cfg)
    manifest: Dict[str, Any] = {}
    try:
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    expected = {}
    binding = manifest.get("hard_binding") or {}
    if binding.get("file"):
        expected[binding["file"]] = binding.get("hash")
    for ingredient in manifest.get("ingredients") or []:
        expected[str(ingredient.get("file"))] = ingredient.get("hash")
    checks = []
    for name, digest in expected.items():
        path = folder / Path(name).name
        actual = _sha256(path) if path.is_file() else None
        checks.append({"file": name, "expected": digest, "actual": actual, "match": actual == digest})
    status = validate_proof(folder, cfg)
    report = {
        "dossier": dossier,
        "verified_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "verified_by": user["username"],
        "integrity": status,
        "c2pa_embedded_state": embedded_manifest_state(folder / "radar_crop.jpg", cfg),
        "files": checks,
        "pki_fingerprint": (manifest.get("signature") or {}).get("pki_fingerprint"),
        "root_fingerprint": root_fingerprint_sha256(cfg),
    }
    audit.record(
        "evidence.verify", actor=user["username"], target=f"evidence:{dossier}", ip=ip, details={"integrity": status}, settings=cfg
    )
    return report


def export_pack(dossier: str, user: Dict[str, Any], ip: Optional[str] = None, settings: Optional[Settings] = None) -> bytes:
    """ZIP for judicial transmission: sealed files + verification + chain of custody."""
    cfg = settings or get_settings()
    folder = dossier_path(dossier, cfg)
    report = verify(dossier, user, ip, cfg)
    audit.record("evidence.export", actor=user["username"], target=f"evidence:{dossier}", ip=ip, settings=cfg)
    custody = [
        {k: entry[k] for k in ("at", "actor", "action", "outcome", "ip", "hash")}
        for entry in reversed(audit.search(target=f"evidence:{dossier}", limit=1000, settings=cfg))
    ]
    linked = incidents_for_dossier(dossier, cfg)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.iterdir()):
            if path.is_file() and path.suffix.lower() in MEDIA_TYPES:
                archive.write(path, f"{dossier}/{path.name}")
        archive.writestr(f"{dossier}/verification.json", json.dumps(report, indent=2, ensure_ascii=False))
        archive.writestr(f"{dossier}/chain_of_custody.json", json.dumps(custody, indent=2, ensure_ascii=False))
        archive.writestr(f"{dossier}/incidents.json", json.dumps(linked, indent=2, ensure_ascii=False))
        archive.writestr(
            f"{dossier}/LISEZMOI.txt",
            (
                "Dossier de preuves KRAKEN-Trust\n"
                f"Exporté le {report['verified_at']} par {user['username']}\n\n"
                "radar_crop.jpg : planche SAR scellée C2PA (manifeste embarqué, vérifiable avec c2patool).\n"
                "chip_*.jpg : vignettes par cible, liées par SHA-256 dans le manifeste signé.\n"
                "manifest.json : manifeste lisible (assertions, empreintes), signé ES256 (champ seal).\n"
                "verification.json : contrôle d'intégrité au moment de l'export.\n"
                "chain_of_custody.json : accès tracés (journal d'audit chaîné SHA-256).\n"
                "Racine de confiance : rootCA_cert.pem de l'instance, empreinte SHA-256 "
                f"{report['root_fingerprint'] or '—'} (à confronter à celle publiée par l'exploitant).\n"
                "Vérification : c2patool radar_crop.jpg avec rootCA_cert.pem comme ancre de confiance "
                "(état attendu : Trusted).\n"
            ),
        )
        root_cert = Path(cfg.keys_dir) / "rootCA_cert.pem"
        if root_cert.is_file():
            archive.write(root_cert, f"{dossier}/rootCA_cert.pem")
    for incident in linked:
        record_event(incident["id"], user["username"], "evidence_export", {"dossier": dossier}, cfg)
    return buffer.getvalue()
