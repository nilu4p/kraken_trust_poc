"""OpenStreetMap coastline and fixed structures: is an echo the coast, a pier or a buoy?

The global bathymetry grid (ETOPO, ~1.8 km cells) is too coarse at the coast:
on the Guiana mud coast it calls "land" points that lie 2–3 km at sea. The OSM
``natural=coastline`` lines are drawn at a few metres from aerial imagery and
include islets and rocks, which matters for radar echoes (an islet off Cayenne
is a bright, ship-sized echo).

The same download brings the mapped fixed structures that echo like a small
vessel: piers, breakwaters, groynes, quays, platforms, and the navigation aids
(lateral / cardinal buoys of the Cayenne and Kourou channels, beacons, lights).

OSM convention: walking along a coastline way, **land is on the left**. The side
of the nearest segment (with the vertex rule at corners) tells land from sea
without needing closed polygons.

Data: a snapshot of the French Guiana region ships with the code
(``app/reference/coastline_guyane.json.gz``, ODbL © OpenStreetMap contributors)
so the check works offline and from the first start; the hourly maintenance
refreshes it from the Overpass API every 30 days (``cache/coastline/``).
"""

from __future__ import annotations

import gzip
import json
import logging
import math
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
import numpy as np

from app.config import Settings, get_settings
from app.sources import health
from app.sources.http import USER_AGENT

logger = logging.getLogger(__name__)

BUNDLED = Path(__file__).resolve().parent.parent / "reference" / "coastline_guyane.json.gz"
OVERPASS = ("https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter")
REFRESH_DAYS = 30
MARGIN_DEG = 0.2
CELL_DEG = 0.02          # spatial index cell (~2.2 km)
SEARCH_RINGS = 5         # cells searched around the point (~11 km)
LICENSE = "© les contributeurs OpenStreetMap (ODbL)"
M_PER_DEG = 111_320.0

_lock = threading.Lock()
_state: Dict[str, Any] = {}


STRUCTURE_LABELS = {
    "pier": "appontement / ponton", "breakwater": "digue", "groyne": "épi", "quay": "quai",
    "offshore_platform": "plateforme", "lighthouse": "phare", "beacon": "balise", "mooring": "coffre d'amarrage",
}


def structure_label(kind: str) -> str:
    if kind.startswith("buoy"):
        return "bouée de balisage"
    if kind.startswith("beacon"):
        return "balise"
    if kind.startswith("light"):
        return "feu de signalisation"
    return STRUCTURE_LABELS.get(kind, kind)


class Coastline:
    """Segments + grid index; :meth:`locate` answers in about a millisecond."""

    def __init__(self, ways: List[List[List[float]]], meta: Dict[str, Any]) -> None:
        self.meta = meta
        # Fixed structures that echo like a small vessel: piers, breakwaters, buoys, beacons…
        self.structure_lines = [(line["kind"], np.array(line["coords"], dtype=np.float64))
                                for line in meta.get("structure_lines") or [] if len(line.get("coords") or []) >= 2]
        points = meta.get("structure_points") or []
        self.structure_xy = np.array([[p[0], p[1]] for p in points], dtype=np.float64).reshape(-1, 2)
        self.structure_kinds = [p[2] for p in points]
        starts, ends = [], []
        for way in ways:
            for a, b in zip(way, way[1:]):
                if a != b:
                    starts.append(a)
                    ends.append(b)
        self.a = np.array(starts, dtype=np.float64).reshape(-1, 2)
        self.b = np.array(ends, dtype=np.float64).reshape(-1, 2)
        self.index: Dict[Tuple[int, int], List[int]] = defaultdict(list)
        for i, (p, q) in enumerate(zip(self.a, self.b)):
            for cx in range(self._cell(min(p[0], q[0])), self._cell(max(p[0], q[0])) + 1):
                for cy in range(self._cell(min(p[1], q[1])), self._cell(max(p[1], q[1])) + 1):
                    self.index[(cx, cy)].append(i)

    @staticmethod
    def _cell(value: float) -> int:
        return int(math.floor(value / CELL_DEG))

    def covers(self, lon: float, lat: float) -> bool:
        w, s, e, n = self.meta["bbox"]
        return w <= lon <= e and s <= lat <= n

    def _candidates(self, lon: float, lat: float) -> List[int]:
        cx, cy = self._cell(lon), self._cell(lat)
        for ring in range(SEARCH_RINGS + 1):
            found = {i for dx in range(-ring, ring + 1) for dy in range(-ring, ring + 1)
                     for i in self.index.get((cx + dx, cy + dy), ())}
            if found:
                # One more ring: the nearest segment may sit in a neighbouring cell.
                return sorted({i for dx in range(-ring - 1, ring + 2) for dy in range(-ring - 1, ring + 2)
                               for i in self.index.get((cx + dx, cy + dy), ())})
        return []

    def locate(self, lon: float, lat: float) -> Optional[Dict[str, Any]]:
        """``{"distance_m", "on_land"}`` or None when no coastline within ~11 km (open sea, or outside the data)."""
        if not self.covers(lon, lat):
            return None
        ids = self._candidates(lon, lat)
        if not ids:
            return None
        kx, ky = M_PER_DEG * math.cos(math.radians(lat)), M_PER_DEG
        a = (self.a[ids] - (lon, lat)) * (kx, ky)
        b = (self.b[ids] - (lon, lat)) * (kx, ky)
        d = b - a
        length2 = np.maximum((d * d).sum(axis=1), 1e-9)
        t = np.clip(-(a * d).sum(axis=1) / length2, 0.0, 1.0)
        closest = a + d * t[:, None]
        dist = np.hypot(closest[:, 0], closest[:, 1])
        k = int(dist.argmin())
        # Left of the segment = land. Cross product of the segment with (point - start); the point is the origin.
        left = d[:, 0] * (-a[:, 1]) - d[:, 1] * (-a[:, 0]) > 0
        on_land = bool(left[k])
        if t[k] in (0.0, 1.0):
            # Nearest point is a vertex shared by two segments: convex corner (left turn) = land
            # only if left of both; reflex corner (right turn) = land if left of either.
            vertex = a[k] if t[k] == 0.0 else b[k]
            incoming = [j for j in range(len(ids)) if np.allclose(b[j], vertex, atol=1e-3)]
            outgoing = [j for j in range(len(ids)) if np.allclose(a[j], vertex, atol=1e-3)]
            if incoming and outgoing:
                i, o = incoming[0], outgoing[0]
                turn = d[i, 0] * d[o, 1] - d[i, 1] * d[o, 0]
                on_land = bool(left[i] and left[o]) if turn > 0 else bool(left[i] or left[o])
        return {"distance_m": round(float(dist[k])), "on_land": on_land}


    def nearest_structure(self, lon: float, lat: float, radius_m: float) -> Optional[Dict[str, Any]]:
        """Closest mapped fixed structure within ``radius_m`` (``{"kind", "label", "distance_m"}``) or None."""
        kx, ky = M_PER_DEG * math.cos(math.radians(lat)), M_PER_DEG
        best: Optional[Tuple[float, str]] = None
        if len(self.structure_xy):
            d = np.hypot((self.structure_xy[:, 0] - lon) * kx, (self.structure_xy[:, 1] - lat) * ky)
            i = int(d.argmin())
            best = (float(d[i]), self.structure_kinds[i])
        reach = radius_m / ky + 0.01
        for kind, coords in self.structure_lines:
            if not (coords[:, 0].min() - reach <= lon <= coords[:, 0].max() + reach
                    and coords[:, 1].min() - reach <= lat <= coords[:, 1].max() + reach):
                continue
            a = (coords[:-1] - (lon, lat)) * (kx, ky)
            seg = (coords[1:] - coords[:-1]) * (kx, ky)
            t = np.clip(-(a * seg).sum(axis=1) / np.maximum((seg * seg).sum(axis=1), 1e-9), 0.0, 1.0)
            dist = float(np.hypot(*(a + seg * t[:, None]).T).min())
            if best is None or dist < best[0]:
                best = (dist, kind)
        if best is None or best[0] > radius_m:
            return None
        return {"kind": best[1], "label": structure_label(best[1]), "distance_m": round(best[0])}


def _bbox(cfg: Settings) -> List[float]:
    w, s, e, n = cfg.eez_bbox
    return [round(w - MARGIN_DEG, 3), round(s - MARGIN_DEG, 3), round(e + MARGIN_DEG, 3), round(n + MARGIN_DEG, 3)]


def _cache_path(cfg: Settings) -> Path:
    w, s, e, n = _bbox(cfg)
    return Path(cfg.cache_dir) / "coastline" / f"osm_{w}_{s}_{e}_{n}.json.gz"


def _read(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def _contains(outer: List[float], inner: List[float]) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and outer[2] >= inner[2] and outer[3] >= inner[3]


def fetch(settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    """Download the coastline of the surveyed area from Overpass and cache it. None on failure."""
    cfg = settings or get_settings()
    w, s, e, n = _bbox(cfg)
    area = f"({s},{w},{n},{e})"
    query = (f'[out:json][timeout:180];(way["natural"="coastline"]{area};'
             f'way["man_made"~"^(pier|breakwater|groyne|quay|offshore_platform)$"]{area};'
             f'node["seamark:type"~"buoy|beacon|light|platform|mooring"]{area};'
             f'node["man_made"~"^(lighthouse|offshore_platform|beacon)$"]{area};);out geom;')
    for url in OVERPASS:
        try:
            response = httpx.post(url, data={"data": query}, headers={"User-Agent": USER_AGENT}, timeout=180)
            response.raise_for_status()
            elements = response.json().get("elements") or []
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Overpass coastline download failed on %s: %s", url, exc)
            continue
        def coords(el: Dict[str, Any]) -> List[List[float]]:
            return [[round(p["lon"], 6), round(p["lat"], 6)] for p in el.get("geometry") or []]

        def kind(el: Dict[str, Any]) -> str:
            tags = el.get("tags") or {}
            return tags.get("seamark:type") or tags.get("man_made") or "structure"

        ways = [coords(el) for el in elements if el.get("type") == "way" and (el.get("tags") or {}).get("natural") == "coastline"]
        ways = [way for way in ways if len(way) >= 2]
        if not ways:  # an empty answer is a failure (the area always has a coast), never cached
            continue
        payload = {"source": "OpenStreetMap — trait de côte, ouvrages portuaires, balisage (Overpass API)", "license": LICENSE,
                   "fetched": time.strftime("%Y-%m-%d", time.gmtime()), "bbox": [w, s, e, n], "ways": ways,
                   "structure_lines": [{"kind": kind(el), "coords": coords(el)} for el in elements
                                       if el.get("type") == "way" and (el.get("tags") or {}).get("natural") != "coastline"
                                       and len(el.get("geometry") or []) >= 2],
                   "structure_points": [[round(el["lon"], 6), round(el["lat"], 6), kind(el)] for el in elements
                                        if el.get("type") == "node" and "lon" in el]}
        path = _cache_path(cfg)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        with gzip.open(tmp, "wt", encoding="utf-8") as handle:
            json.dump(payload, handle, separators=(",", ":"))
        tmp.replace(path)
        health.record("osm_coastline", True)
        with _lock:
            _state.clear()
        return payload
    health.record("osm_coastline", False, "Overpass injoignable")
    return None


def load(settings: Optional[Settings] = None) -> Optional[Coastline]:
    """Cached download if any, else the bundled snapshot when it covers the area, else a download."""
    cfg = settings or get_settings()
    path = _cache_path(cfg)
    with _lock:
        if _state.get("key") == str(path):
            return _state.get("coast")
    payload = _read(path) if path.exists() else None
    if payload is None:
        bundled = _read(BUNDLED)
        if bundled is not None and _contains(bundled["bbox"], _bbox(cfg)):
            payload = bundled
    if payload is None and _state.get("failed_at", 0) < time.monotonic() - 600:
        payload = fetch(cfg)
        if payload is None:
            _state["failed_at"] = time.monotonic()
    coast = Coastline(payload["ways"], payload) if payload else None
    with _lock:
        _state.update(key=str(path), coast=coast)
    return coast


def refresh_if_stale(settings: Optional[Settings] = None) -> bool:
    """Hourly maintenance: re-download when the cached copy is older than 30 days (or missing). Never raises."""
    cfg = settings or get_settings()
    path = _cache_path(cfg)
    if path.exists() and time.time() - path.stat().st_mtime < REFRESH_DAYS * 86400:
        return False
    try:
        return fetch(cfg) is not None
    except Exception:  # pragma: no cover - maintenance must go on
        logger.exception("Coastline refresh failed")
        return False


def nearest_structure(lon: float, lat: float, radius_m: float = 150.0,
                      settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    coast = load(settings)
    return coast.nearest_structure(lon, lat, radius_m) if coast else None


def locate(lon: float, lat: float, settings: Optional[Settings] = None) -> Optional[Dict[str, Any]]:
    """Land / sea and distance to the shore (metres), None when unknown or > ~11 km from any coast."""
    coast = load(settings)
    return coast.locate(lon, lat) if coast else None
