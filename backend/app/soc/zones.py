"""Surveillance zones drawn on the map: named polygons usable as a custom-rule criterion.

A zone is a simple polygon (3 to 200 vertices, no crossing edges) in WGS84.
Zones used by an enabled custom rule cannot be deleted (the rule would silently
stop matching); every change is audited.
"""

from __future__ import annotations

import datetime
import json
import math
import re
from typing import Any, Dict, List, Optional

from app.config import Settings
from app.core import audit
from app.core.geo import point_in_geometry
from app.db import db, read_db, rows

MAX_VERTICES = 200
COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CACHE: Dict[Any, List[Dict[str, Any]]] = {}


class ZoneError(ValueError):
    pass


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _cross(a: List[float], b: List[float], c: List[float]) -> float:
    return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])


def _segments_cross(p1: List[float], p2: List[float], p3: List[float], p4: List[float]) -> bool:
    return _cross(p3, p4, p1) * _cross(p3, p4, p2) < 0 and _cross(p1, p2, p3) * _cross(p1, p2, p4) < 0


def validate_geometry(geometry: Any) -> Dict[str, Any]:
    """Normalised GeoJSON Polygon (closed ring), or ZoneError with an operator-readable message."""
    if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
        raise ZoneError("Polygone attendu")
    try:
        ring = [[round(float(p[0]), 6), round(float(p[1]), 6)] for p in geometry["coordinates"][0]]
    except (TypeError, ValueError, KeyError, IndexError) as exc:
        raise ZoneError("Coordonnées du polygone illisibles") from exc
    if ring and ring[0] == ring[-1]:
        ring = ring[:-1]
    if not 3 <= len(ring) <= MAX_VERTICES:
        raise ZoneError(f"Une zone a entre 3 et {MAX_VERTICES} sommets")
    if any(not (-180 <= lon <= 180 and -90 <= lat <= 90) for lon, lat in ring):
        raise ZoneError("Coordonnées hors limites")
    closed = ring + [ring[0]]
    edges = list(zip(closed, closed[1:]))
    for i in range(len(edges)):
        for j in range(i + 2, len(edges)):
            if i == 0 and j == len(edges) - 1:
                continue
            if _segments_cross(*edges[i], *edges[j]):
                raise ZoneError("Les côtés de la zone se croisent : redessinez-la sans croisement")
    area = abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in edges)) / 2
    if area * (111.0 ** 2) * math.cos(math.radians(ring[0][1])) < 0.01:  # < 0.01 km² ≈ 1 ha
        raise ZoneError("Zone trop petite")
    return {"type": "Polygon", "coordinates": [closed]}


def _row(record: Dict[str, Any]) -> Dict[str, Any]:
    return {**{k: record[k] for k in ("id", "name", "description", "color", "created_at", "created_by", "updated_at", "updated_by")},
            "geometry": json.loads(record["geometry"])}


def list_zones(settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    with read_db(settings) as conn:
        signature = conn.execute("SELECT COUNT(*), MAX(id), MAX(updated_at) FROM surveillance_zones").fetchone()
        key = tuple(signature)
        if key not in _CACHE:
            _CACHE.clear()
            _CACHE[key] = [_row(r) for r in rows(conn.execute("SELECT * FROM surveillance_zones ORDER BY name").fetchall())]
    return _CACHE[key]


def get(zone_id: int, settings: Optional[Settings] = None) -> Dict[str, Any]:
    for zone in list_zones(settings):
        if zone["id"] == zone_id:
            return zone
    raise KeyError(zone_id)


def containing(lon: float, lat: float, settings: Optional[Settings] = None) -> List[Dict[str, Any]]:
    """Zones containing a point."""
    return [z for z in list_zones(settings) if point_in_geometry(lon, lat, z["geometry"])]


def _clean(payload: Dict[str, Any], current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    name = str(payload.get("name", (current or {}).get("name", "")) or "").strip()
    if not 2 <= len(name) <= 80:
        raise ZoneError("Le nom doit faire entre 2 et 80 caractères")
    color = str(payload.get("color", (current or {}).get("color", "#22d3ee")))
    if not COLOR.match(color):
        raise ZoneError("Couleur invalide (#rrggbb)")
    geometry = validate_geometry(payload["geometry"]) if "geometry" in payload else (current or {}).get("geometry")
    if geometry is None:
        raise ZoneError("Dessinez la zone sur la carte")
    description = str(payload.get("description", (current or {}).get("description", "")) or "").strip()[:1000]
    return {"name": name, "color": color, "geometry": geometry, "description": description}


def create(payload: Dict[str, Any], actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    clean = _clean(payload)
    now = _now()
    with db(settings) as conn:
        zone_id = conn.execute(
            "INSERT INTO surveillance_zones (name, description, color, geometry, created_at, created_by, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (clean["name"], clean["description"], clean["color"], json.dumps(clean["geometry"]), now, actor, now, actor),
        ).fetchone()[0]
    audit.record("zones.create", actor=actor, target=f"zone:{zone_id}",
                 details={"name": clean["name"], "vertices": len(clean["geometry"]["coordinates"][0]) - 1}, settings=settings)
    return get(zone_id, settings)


def update(zone_id: int, payload: Dict[str, Any], actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    clean = _clean(payload, get(zone_id, settings))
    with db(settings) as conn:
        conn.execute(
            "UPDATE surveillance_zones SET name = ?, description = ?, color = ?, geometry = ?, updated_at = ?, updated_by = ? "
            "WHERE id = ?",
            (clean["name"], clean["description"], clean["color"], json.dumps(clean["geometry"]), _now(), actor, zone_id),
        )
    audit.record("zones.update", actor=actor, target=f"zone:{zone_id}", details={"name": clean["name"]}, settings=settings)
    return get(zone_id, settings)


def used_by(zone_id: int, settings: Optional[Settings] = None) -> List[str]:
    """Names of the enabled custom rules using this zone."""
    from app.soc import custom_rules

    names = []
    for rule in custom_rules.list_rules(settings):
        if rule["enabled"] and any(c["field"] == "drawn_zone" and zone_id in c["value"]
                                   for group in custom_rules.groups_of(rule["definition"]) for c in group):
            names.append(rule["name"])
    return names


def delete(zone_id: int, actor: str, settings: Optional[Settings] = None) -> None:
    zone = get(zone_id, settings)
    users = used_by(zone_id, settings)
    if users:
        raise ZoneError(f"Zone utilisée par la règle active « {users[0]} »" + (f" (+{len(users) - 1})" if len(users) > 1 else "")
                        + " : désactivez ou modifiez la règle d'abord")
    with db(settings) as conn:
        conn.execute("DELETE FROM surveillance_zones WHERE id = ?", (zone_id,))
    audit.record("zones.delete", actor=actor, target=f"zone:{zone_id}", details={"name": zone["name"]}, settings=settings)
