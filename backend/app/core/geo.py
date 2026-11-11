"""Small dependency-free geodesy helpers (lon/lat WGS84, GeoJSON ordering)."""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

EARTH_RADIUS_KM = 6371.0088
NM_TO_KM = 1.852

Ring = List[List[float]]
Polygon = List[Ring]


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = phi2 - phi1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def polygons_of(geometry: Optional[Dict[str, Any]]) -> List[Polygon]:
    """Normalise a GeoJSON Polygon / MultiPolygon geometry to a list of polygons."""
    if not geometry:
        return []
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Polygon":
        return [coords]
    if kind == "MultiPolygon":
        return list(coords)
    return []


def _point_in_ring(lon: float, lat: float, ring: Sequence[Sequence[float]]) -> bool:
    inside = False
    count = len(ring)
    j = count - 1
    for i in range(count):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi:
            inside = not inside
        j = i
    return inside


def point_in_geometry(lon: float, lat: float, geometry: Optional[Dict[str, Any]]) -> bool:
    """Even-odd ray casting with hole support for (Multi)Polygon geometries."""
    for polygon in polygons_of(geometry):
        if not polygon or not _point_in_ring(lon, lat, polygon[0]):
            continue
        if not any(_point_in_ring(lon, lat, hole) for hole in polygon[1:]):
            return True
    return False


def _point_segment_km(lat: float, lon: float, a: Sequence[float], b: Sequence[float]) -> float:
    # Local equirectangular projection is accurate enough at EEZ scale (< 400 km).
    kx = math.cos(math.radians(lat)) * 111.32
    ky = 110.57
    ax, ay = (a[0] - lon) * kx, (a[1] - lat) * ky
    bx, by = (b[0] - lon) * kx, (b[1] - lat) * ky
    dx, dy = bx - ax, by - ay
    length2 = dx * dx + dy * dy
    t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(ax * dx + ay * dy) / length2))
    px, py = ax + t * dx, ay + t * dy
    return math.hypot(px, py)


def distance_to_boundary_km(lon: float, lat: float, geometry: Optional[Dict[str, Any]]) -> Optional[float]:
    """Shortest distance from a point to the outline of a (Multi)Polygon."""
    best: Optional[float] = None
    for polygon in polygons_of(geometry):
        for ring in polygon:
            for a, b in zip(ring, ring[1:]):
                dist = _point_segment_km(lat, lon, a, b)
                if best is None or dist < best:
                    best = dist
    return best


def _rdp(points: Ring, tolerance: float) -> Ring:
    """Iterative Ramer-Douglas-Peucker simplification (degrees tolerance)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack: List[Tuple[int, int]] = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start][0], points[start][1]
        bx, by = points[end][0], points[end][1]
        dx, dy = bx - ax, by - ay
        norm = math.hypot(dx, dy)
        max_dist, index = 0.0, -1
        for i in range(start + 1, end):
            px, py = points[i][0], points[i][1]
            if norm < 1e-12:  # closed ring: first == last point
                dist = math.hypot(px - ax, py - ay)
            else:
                dist = abs(dy * px - dx * py + bx * ay - by * ax) / norm
            if dist > max_dist:
                max_dist, index = dist, i
        if index != -1 and max_dist > tolerance:
            keep[index] = True
            stack.append((start, index))
            stack.append((index, end))
    return [p for p, k in zip(points, keep) if k]


def simplify_geometry(geometry: Dict[str, Any], tolerance: float = 0.003) -> Dict[str, Any]:
    """Simplify a (Multi)Polygon for display; rings that collapse are dropped."""
    simplified: List[Polygon] = []
    for polygon in polygons_of(geometry):
        rings = [_rdp([list(map(float, pt[:2])) for pt in ring], tolerance) for ring in polygon]
        rings = [ring for ring in rings if len(ring) >= 4]
        if rings:
            simplified.append(rings)
    if not simplified:
        return geometry
    if len(simplified) == 1:
        return {"type": "Polygon", "coordinates": simplified[0]}
    return {"type": "MultiPolygon", "coordinates": simplified}


def bbox_of(geometry: Optional[Dict[str, Any]]) -> Optional[Tuple[float, float, float, float]]:
    """(west, south, east, north) of a (Multi)Polygon, or None."""
    points: Iterable[Sequence[float]] = (
        pt for polygon in polygons_of(geometry) for ring in polygon for pt in ring
    )
    lons: List[float] = []
    lats: List[float] = []
    for pt in points:
        lons.append(float(pt[0]))
        lats.append(float(pt[1]))
    if not lons:
        return None
    return (min(lons), min(lats), max(lons), max(lats))


def point_segment_km(lat: float, lon: float, a: Sequence[float], b: Sequence[float]) -> float:
    """Distance (km) from a point to the segment [a, b] (lon/lat pairs)."""
    return _point_segment_km(lat, lon, a, b)
