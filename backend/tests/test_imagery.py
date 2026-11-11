"""Sentinel-2 imagery: scenes before the instant, one per tile, and tiles only from the public bucket."""

from __future__ import annotations

import datetime
from typing import Any, Dict

import httpx
import pytest

from app.config import get_settings
from app.sources import sentinel2

NOW = datetime.datetime.now(datetime.timezone.utc)


def _item(item_id: str, days_ago: float, cloud: float, href: str = "") -> Dict[str, Any]:
    tile = item_id.split("_")[1]
    return {
        "id": item_id, "bbox": [-53, 5, -52, 6], "geometry": {"type": "Point", "coordinates": [-52.5, 5.5]},
        "properties": {"datetime": (NOW - datetime.timedelta(days=days_ago)).isoformat(), "eo:cloud_cover": cloud,
                       "grid:code": f"MGRS-{tile}", "platform": "sentinel-2c"},
        "assets": {"visual": {"href": href or f"https://sentinel-cogs.s3.us-west-2.amazonaws.com/x/{item_id}/TCI.tif"}},
    }


def test_scenes_latest_per_tile_before_the_instant(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies = []

    def fake_post(url: str, json: Dict[str, Any] = None, **_kw: Any) -> httpx.Response:
        bodies.append(json)
        items = [_item("S2C_22NBJ_20261020_0_L2A", 1, 10), _item("S2B_22NBJ_20261015_0_L2A", 6, 5),
                 _item("S2C_21NZF_20261018_0_L2A", 3, 20)]
        return httpx.Response(200, request=httpx.Request("POST", url), json={"features": items})

    monkeypatch.setattr(httpx, "post", fake_post)
    data = sentinel2.scenes(max_cloud=40)
    assert [f["properties"]["item_id"] for f in data["features"]] == ["S2C_21NZF_20261018_0_L2A", "S2C_22NBJ_20261020_0_L2A"]
    at = NOW - datetime.timedelta(days=2)
    sentinel2.scenes(at=at, max_cloud=40)
    assert bodies[-1]["datetime"].endswith(at.strftime("%Y-%m-%dT%H:%M:%SZ"))  # nothing after the instant
    assert bodies[-1]["query"] == {"eo:cloud_cover": {"lt": 40}}


def test_tiles_only_from_the_public_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_get(url: str, **_kw: Any) -> httpx.Response:
        return httpx.Response(200, request=httpx.Request("GET", url),
                              json={"assets": {"visual": {"href": "http://169.254.169.254/latest/meta-data.tif"}}})

    monkeypatch.setattr(httpx, "get", fake_get)
    with pytest.raises(sentinel2.Sentinel2Error, match="dépôt"):
        sentinel2.render_tile("S2C_22NBJ_20261020_0_L2A", 10, 370, 492)
    with pytest.raises(sentinel2.Sentinel2Error):
        sentinel2.render_tile("../../etc/passwd", 10, 370, 492)
    with pytest.raises(sentinel2.Sentinel2Error, match="limites"):
        sentinel2.render_tile("S2C_22NBJ_20261020_0_L2A", 3, 0, 0)


def test_tile_bounds_are_web_mercator() -> None:
    minx, miny, maxx, maxy = sentinel2.tile_bounds(0, 0, 0)
    assert minx == pytest.approx(-20037508.34, abs=1) and maxy == pytest.approx(20037508.34, abs=1)
    x, y = sentinel2.lonlat_to_tile(-52.5, 5.5, 10)
    bx0, by0, bx1, by1 = sentinel2.tile_bounds(10, x, y)
    assert bx0 < -52.5 * 20037508.34 / 180 < bx1


def test_imagery_api(client_as: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sentinel2, "render_tile", lambda *_a, **_kw: b"\x89PNG fake")
    monkeypatch.setattr(sentinel2, "scenes", lambda at=None, **_kw: {"type": "FeatureCollection", "features": [], "available": True})
    viewer = client_as("viewer")
    tile = viewer.get("/api/v1/imagery/sentinel2/tiles/S2C_22NBJ_20261020_0_L2A/10/370/492.png")
    assert tile.status_code == 200 and tile.headers["content-type"] == "image/png"
    assert viewer.get("/api/v1/imagery/sentinel2/tiles/evil/10/1/1.png").status_code == 422
    replay = viewer.get("/api/v1/imagery/sentinel2/scenes", params={"at": (NOW - datetime.timedelta(days=3)).isoformat()}).json()
    assert replay["time"]["mode"] == "replay"
