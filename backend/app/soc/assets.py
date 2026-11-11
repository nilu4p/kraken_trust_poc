"""Intervention means (patrol vessels, boats, aircraft) used by the decision support.

Each asset has a departure point at sea (harbour exit, so that the straight-line
route does not cross land), a transit speed, an endurance (total distance it can
cover) and a readiness delay (time to get under way). The values are operational
data owned by the unit: a generic fleet is installed once so that the decision
support works on a fresh instance, then supervisors edit it. Every change is
audited.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.core import audit
from app.db import db, read_db, row, rows

KINDS = {
    "patrol_vessel": "Patrouilleur",
    "fast_boat": "Vedette",
    "helicopter": "Hélicoptère",
    "aircraft": "Avion de surveillance",
}
STATUSES = {
    "available": "Disponible",
    "engaged": "Engagé",
    "unavailable": "Indisponible (maintenance, équipage)",
}
FIELDS = ("name", "kind", "base_name", "base_lat", "base_lon", "speed_kn", "endurance_nm", "readiness_min",
          "can_board", "status", "notes")
SEEDED_KEY = "assets_seeded"

_GENERIC = "Valeurs génériques à remplacer par celles du moyen réel."
DEFAULT_FLEET: List[Dict[str, Any]] = [
    {"name": "Patrouilleur hauturier", "kind": "patrol_vessel", "base_name": "Dégrad des Cannes (sortie du Mahury)",
     "base_lat": 4.93, "base_lon": -52.22, "speed_kn": 14, "endurance_nm": 4000, "readiness_min": 120, "can_board": True,
     "notes": _GENERIC},
    {"name": "Vedette côtière", "kind": "fast_boat", "base_name": "Dégrad des Cannes (sortie du Mahury)",
     "base_lat": 4.93, "base_lon": -52.22, "speed_kn": 22, "endurance_nm": 300, "readiness_min": 45, "can_board": True,
     "notes": _GENERIC},
    {"name": "Vedette du Maroni", "kind": "fast_boat", "base_name": "Saint-Laurent-du-Maroni (embouchure)",
     "base_lat": 5.78, "base_lon": -53.93, "speed_kn": 20, "endurance_nm": 250, "readiness_min": 60, "can_board": True,
     "notes": _GENERIC},
    {"name": "Avion de surveillance maritime", "kind": "aircraft", "base_name": "Cayenne – Félix Éboué",
     "base_lat": 4.82, "base_lon": -52.36, "speed_kn": 220, "endurance_nm": 1500, "readiness_min": 90, "can_board": False,
     "notes": "Levée de doute et identification : ne peut pas arraisonner. " + _GENERIC},
    {"name": "Hélicoptère", "kind": "helicopter", "base_name": "Cayenne – Félix Éboué",
     "base_lat": 4.82, "base_lon": -52.36, "speed_kn": 120, "endurance_nm": 400, "readiness_min": 60, "can_board": False,
     "notes": "Identification et appui d'une interception. " + _GENERIC},
]


class AssetError(ValueError):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def validate(data: Dict[str, Any], partial: bool = False) -> Dict[str, Any]:
    """Typed, bounded fields (operator-readable errors)."""
    clean: Dict[str, Any] = {}
    for name in FIELDS:
        if name not in data or data[name] is None:
            if not partial and name not in ("notes", "base_name", "status", "readiness_min", "can_board"):
                raise AssetError(f"Champ requis : {name}")
            continue
        value = data[name]
        try:
            if name in ("name", "base_name", "notes"):
                value = str(value).strip()[: 80 if name != "notes" else 500]
                if name == "name" and not value:
                    raise AssetError("Nom requis")
            elif name == "kind" and value not in KINDS:
                raise AssetError("Type de moyen inconnu")
            elif name == "status" and value not in STATUSES:
                raise AssetError("Disponibilité inconnue")
            elif name == "base_lat":
                value = float(value)
                if not -90 <= value <= 90:
                    raise AssetError("Latitude hors limites")
            elif name == "base_lon":
                value = float(value)
                if not -180 <= value <= 180:
                    raise AssetError("Longitude hors limites")
            elif name == "speed_kn":
                value = float(value)
                if not 1 <= value <= 600:
                    raise AssetError("Vitesse de transit entre 1 et 600 nœuds")
            elif name == "endurance_nm":
                value = float(value)
                if not 1 <= value <= 20000:
                    raise AssetError("Distance franchissable entre 1 et 20 000 NM")
            elif name == "readiness_min":
                value = int(value)
                if not 0 <= value <= 7 * 24 * 60:
                    raise AssetError("Délai d'appareillage entre 0 et 7 jours")
            elif name == "can_board":
                value = 1 if bool(value) else 0
        except (TypeError, ValueError) as exc:
            if isinstance(exc, AssetError):
                raise
            raise AssetError(f"Valeur invalide : {name}") from exc
        clean[name] = value
    return clean


def _decorate(asset: Dict[str, Any]) -> Dict[str, Any]:
    asset["can_board"] = bool(asset["can_board"])
    asset["kind_label"] = KINDS.get(asset["kind"], asset["kind"])
    asset["status_label"] = STATUSES.get(asset["status"], asset["status"])
    return asset


def _ensure_seed(settings: Optional[Settings]) -> None:
    """Install the generic fleet once per instance (a deliberately emptied list stays empty)."""
    with db(settings) as conn:
        if conn.execute("SELECT 1 FROM app_settings WHERE key = ?", (SEEDED_KEY,)).fetchone():
            return
        now = _now()
        if not conn.execute("SELECT 1 FROM intervention_assets LIMIT 1").fetchone():
            for asset in DEFAULT_FLEET:
                clean = validate(asset)
                columns = [*clean, "updated_at", "updated_by"]
                conn.execute(
                    f"INSERT INTO intervention_assets ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
                    (*clean.values(), now, "system"),
                )
        conn.execute(
            "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, 'true', ?, 'system') "
            "ON CONFLICT(key) DO NOTHING",
            (SEEDED_KEY, now),
        )


def list_assets(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    _ensure_seed(settings)
    with read_db(settings) as conn:
        return [_decorate(a) for a in rows(conn.execute("SELECT * FROM intervention_assets ORDER BY id").fetchall())]


def create(data: Dict[str, Any], actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    clean = validate(data)
    clean.setdefault("status", "available")
    columns = [*clean, "updated_at", "updated_by"]
    with db(settings) as conn:
        asset_id = conn.execute(
            f"INSERT INTO intervention_assets ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) RETURNING id",
            (*clean.values(), _now(), actor),
        ).fetchone()[0]
        asset = row(conn.execute("SELECT * FROM intervention_assets WHERE id = ?", (asset_id,)).fetchone())
    audit.record("asset.create", actor=actor, target=f"asset:{asset_id}", details={"name": clean["name"]}, settings=settings)
    return _decorate(asset)  # type: ignore[arg-type]


def update(asset_id: int, data: Dict[str, Any], actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    clean = validate(data, partial=True)
    if not clean:
        raise AssetError("Aucune modification")
    with db(settings) as conn:
        if conn.execute("SELECT 1 FROM intervention_assets WHERE id = ?", (asset_id,)).fetchone() is None:
            raise KeyError(asset_id)
        assignments = ", ".join(f"{k} = ?" for k in clean)
        conn.execute(
            f"UPDATE intervention_assets SET {assignments}, updated_at = ?, updated_by = ? WHERE id = ?",
            (*clean.values(), _now(), actor, asset_id),
        )
        asset = row(conn.execute("SELECT * FROM intervention_assets WHERE id = ?", (asset_id,)).fetchone())
    audit.record("asset.update", actor=actor, target=f"asset:{asset_id}", details=clean, settings=settings)
    return _decorate(asset)  # type: ignore[arg-type]


def delete(asset_id: int, actor: str, settings: Optional[Settings] = None) -> None:
    with db(settings) as conn:
        asset = row(conn.execute("SELECT name FROM intervention_assets WHERE id = ?", (asset_id,)).fetchone())
        if asset is None:
            raise KeyError(asset_id)
        conn.execute("DELETE FROM intervention_assets WHERE id = ?", (asset_id,))
    audit.record("asset.delete", actor=actor, target=f"asset:{asset_id}", details={"name": asset["name"]}, settings=settings)
