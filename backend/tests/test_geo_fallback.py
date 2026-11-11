"""Offline EEZ fallback polygon: a valid ring (no self-intersection) close to the real EEZ."""

from __future__ import annotations

from typing import List

from app.config import Settings
from app.core.geo import point_in_geometry

Point = List[float]


def _cross(a: Point, b: Point, c: Point) -> float:
    return (c[1] - a[1]) * (b[0] - a[0]) - (b[1] - a[1]) * (c[0] - a[0])


def _intersect(p1: Point, p2: Point, p3: Point, p4: Point) -> bool:
    return _cross(p3, p4, p1) * _cross(p3, p4, p2) < 0 and _cross(p1, p2, p3) * _cross(p1, p2, p4) < 0


def test_fallback_eez_is_a_simple_closed_ring() -> None:
    ring = Settings().eez_polygon
    assert ring[0] == ring[-1] and len(ring) >= 4
    edges = list(zip(ring, ring[1:]))
    crossings = [
        (i, j) for i in range(len(edges)) for j in range(i + 2, len(edges))
        if not (i == 0 and j == len(edges) - 1) and _intersect(*edges[i], *edges[j])
    ]
    assert not crossings


def test_fallback_eez_contains_offshore_and_excludes_land() -> None:
    eez = {"type": "Polygon", "coordinates": [Settings().eez_polygon]}
    assert point_in_geometry(-52.0, 6.5, eez)        # offshore, inside the EEZ
    assert not point_in_geometry(-53.0, 4.0, eez)    # inland French Guiana
    assert not point_in_geometry(-48.0, 6.0, eez)    # beyond the outer limit
