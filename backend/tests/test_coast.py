"""Coastal echoes: land masking, OSM coastline and structures, coastal reliability bands."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import numpy as np
import pytest

from app.config import Settings
from app.pipeline import detection, quality
from app.sources import coastline

# A square island, counter-clockwise (OSM: land on the left of the way).
ISLAND = [[-52.0, 5.0], [-51.9, 5.0], [-51.9, 5.1], [-52.0, 5.1], [-52.0, 5.0]]


def _coast(**extra: Any) -> coastline.Coastline:
    meta = {"bbox": [-53.5, 4.5, -51.5, 6.5], **extra}
    return coastline.Coastline([ISLAND], meta)


def test_coastline_side_and_distance() -> None:
    coast = _coast()
    inside = coast.locate(-51.95, 5.05)
    assert inside["on_land"] is True and inside["distance_m"] == pytest.approx(5540, rel=0.02)
    outside = coast.locate(-51.95, 4.99)
    assert outside["on_land"] is False and outside["distance_m"] == pytest.approx(1113, rel=0.02)
    # Diagonally off a convex corner: the nearest point is the vertex itself.
    corner = coast.locate(-51.895, 4.995)
    assert corner["on_land"] is False
    assert coast.locate(-52.6, 5.5) is None  # > 11 km from any coast: open sea
    assert coast.locate(10.0, 10.0) is None  # outside the data


def test_bundled_snapshot_knows_guiana() -> None:
    coast = coastline.load(Settings())
    assert coast is not None and coast.meta["license"].startswith("ODbL")
    assert coast.locate(-52.645, 5.159)["on_land"] is True       # Kourou
    assert coast.locate(-52.7417, 5.2868)["on_land"] is False    # 2.6 km off the mud coast
    islet = coast.locate(-52.169, 4.8882)                         # islet off Cayenne
    assert islet["on_land"] is True and islet["distance_m"] < 100
    assert any(k.startswith("buoy") for k in coast.structure_kinds)


def test_nearest_structure() -> None:
    coast = _coast(structure_points=[[-52.5, 5.5, "buoy_lateral"]],
                   structure_lines=[{"kind": "pier", "coords": [[-52.3, 5.3], [-52.3, 5.31]]}])
    buoy = coast.nearest_structure(-52.5005, 5.5, 300)
    assert buoy["label"] == "bouée de balisage" and buoy["distance_m"] == pytest.approx(55, abs=3)
    assert coast.nearest_structure(-52.301, 5.305, 300)["label"] == "appontement / ponton"
    assert coast.nearest_structure(-52.4, 5.4, 300) is None


def test_radar_land_mask_keeps_large_ships_out() -> None:
    rng = np.random.default_rng(0)
    tile = rng.rayleigh(40.0, (400, 400)).astype(np.float32)
    tile[:, :150] = rng.rayleigh(400.0, (400, 150))      # land: +20 dB, textured
    tile[200:240, 300:308] = 3000.0                       # 400 m vessel at sea
    land = detection.radar_land_mask(tile)
    assert land[:, :130].mean() > 0.98                    # land interior
    assert not land[:, 175:].any()                        # sea, vessel included
    assert detection.shore_distance_px(land, 100, 160) is not None
    assert detection.shore_distance_px(land, 220, 304) is None  # > 1 km from land


@pytest.mark.parametrize("coast, reason", [
    ({"radar_on_land": True}, "radar_land"),
    ({"radar_shore_m": 60}, "shoreline"),
    ({"osm_on_land": True, "osm_shore_m": 5}, "osm_land"),
    ({"osm_on_land": False, "osm_shore_m": 30}, "osm_land"),
    ({"structure": {"kind": "buoy_lateral", "distance_m": 40}}, "structure"),
    ({"radar_shore_m": 300, "osm_on_land": False, "osm_shore_m": 400, "structure": {"kind": "pier", "distance_m": 150}}, None),
])
def test_on_land_rules(coast: Dict[str, Any], reason: Any) -> None:
    assert detection.on_land_reason(coast) == reason


def _det(**coast: Any) -> Dict[str, Any]:
    return {"lat": 5.5, "lon": -52.5, "snr": 15.0, "pixels": 10, "elongation": 3.0, "dual_pol": True,
            "coast": {"radar_checked": True, "osm_known": True, **coast}}


def test_coastal_bands_and_coarse_bathymetry() -> None:
    open_sea = quality.assess(_det(shore_m=None), settings=Settings())
    fringe = quality.assess(_det(shore_m=150), settings=Settings())
    near = quality.assess(_det(shore_m=350), settings=Settings())
    coastal = quality.assess(_det(shore_m=800), settings=Settings())
    assert open_sea["score"] - fringe["score"] == pytest.approx(0.45, abs=0.01)
    assert open_sea["score"] - near["score"] == pytest.approx(0.25, abs=0.01)
    assert open_sea["score"] - coastal["score"] == pytest.approx(0.1, abs=0.01)
    assert any("Au ras du rivage (~150 m)" in f for f in fringe["flags"])
    # ETOPO says "land" 2 km out on the mud coast: with a known coastline it only means shallow water.
    mud = quality.assess({**_det(shore_m=None), "ocean": {"on_land": True, "depth_m": 0.0}}, settings=Settings())
    assert open_sea["score"] - mud["score"] == pytest.approx(0.15, abs=0.01)
    assert not any("terre émergée" in f for f in mud["flags"])
    # Without any coastline knowledge, the bathymetry rule stays as before.
    legacy = {"lat": 5.5, "lon": -52.5, "snr": 15.0, "pixels": 10, "elongation": 3.0, "dual_pol": True}
    blind = quality.assess({**legacy, "ocean": {"on_land": True, "depth_m": 0.0}}, settings=Settings())
    assert quality.assess(legacy, settings=Settings())["score"] - blind["score"] == pytest.approx(0.4, abs=0.01)


def test_scan_discards_shore_and_buoy_echoes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Radar-geometry GeoTIFF: land on the west, an echo on the shore, one on a buoy, one ship at sea."""
    import rasterio
    from rasterio.control import GroundControlPoint
    from rasterio.crs import CRS

    size = 512
    rng = np.random.default_rng(5)
    data = rng.rayleigh(40.0, (size, size)).astype(np.float32)
    data[:, :200] = rng.rayleigh(400.0, (size, 200))   # land
    data[100:103, 203:206] = 6000.0                      # shore fringe
    data[300:303, 450:456] = 6000.0                      # vessel at sea
    data[401, 351:353] = (6500.0, 6000.0)                 # channel buoy (peak on the buoy)
    lon = lambda col: -53.0 + col * 1.2 / size  # noqa: E731
    lat = lambda row: 6.0 - row * 1.2 / size    # noqa: E731
    gcps = [GroundControlPoint(row=r, col=c, x=lon(c), y=lat(r)) for r in (0, size) for c in (0, size)]
    path = tmp_path / "coast.tif"
    with rasterio.open(path, "w", driver="GTiff", width=size, height=size, count=1, dtype="float32") as dst:
        dst.write(data, 1)
        dst.gcps = (gcps, CRS.from_epsg(4326))
    # Coastline along column 200, walked northwards: land (west) on the left.
    coast = coastline.Coastline([[[lon(200), 4.7], [lon(200), 6.1]]],
                                {"bbox": [-53.5, 4.5, -51.5, 6.5], "structure_points": [[lon(351.5), lat(401.5), "buoy_lateral"]]})
    monkeypatch.setattr(coastline, "load", lambda *_a, **_k: coast)
    stats: Dict[str, Any] = {}
    monkeypatch.setattr("app.pipeline.scan_state.add_stats", lambda **kw: stats.update(kw))
    eez = {"type": "Polygon", "coordinates": [[[-53.1, 4.7], [-51.7, 4.7], [-51.7, 6.1], [-53.1, 6.1], [-53.1, 4.7]]]}

    cfg = Settings(sar_window_size=512, sar_max_windows=1, access_key="", secret_key="")
    detections, _ = detection.detect_ships_with_window(str(path), cfg, eez=eez)

    assert len(detections) == 1, [(d["row"], d["col"]) for d in detections]
    assert detections[0]["col"] >= 450 and detections[0]["coast"]["radar_on_land"] is False
    assert stats["coast_rejected"] == 2 and set(stats["coast_rejected_by"]) == {"shoreline", "structure"}
    # Masked only near the mapped coast (deep inland is outside the EEZ anyway; at open sea a
    # bright rain cell must never hide a vessel).
    assert 3 < stats["land_masked_pct"] < 39
