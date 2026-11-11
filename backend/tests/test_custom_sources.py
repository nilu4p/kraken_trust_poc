"""Custom sources: SSRF guard, request building, response mapping, pagination, tiles, API rights."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List

import httpx
import pytest

from app.config import get_settings
from app.sources import custom

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 2000


@pytest.fixture()
def remote(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[httpx.Request], httpx.Response]], List[httpx.Request]]:
    """Route custom-source calls to ``handler``; every host resolves to a public address."""

    def _install(handler: Callable[[httpx.Request], httpx.Response]) -> List[httpx.Request]:
        seen: List[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(custom, "_TRANSPORT", httpx.MockTransport(_record))
        monkeypatch.setattr(custom, "_resolve", lambda host, port: ["10.0.0.5"] if host == "intranet.local" else ["93.184.216.34"])
        return seen

    return _install


def _defn(**overrides: Any) -> Dict[str, Any]:
    return custom.SourceDefinition(**{"name": "Test", "url": "https://api.example.org/v1/items", **overrides}).model_dump()


def test_ssrf_guard_blocks_internal_hosts_and_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(custom, "_resolve", lambda host, port: ["127.0.0.1"] if host == "localhost" else ["93.184.216.34"])
    with pytest.raises(custom.CustomSourceError, match="interne"):
        custom.check_url("http://localhost:8000/api", carries_secret=False)
    with pytest.raises(custom.CustomSourceError, match="Identifiants"):
        custom.check_url("https://user:pw@api.example.org/", carries_secret=False)
    with pytest.raises(custom.CustomSourceError, match="en clair"):
        custom.check_url("http://api.example.org/", carries_secret=True)
    with pytest.raises(custom.CustomSourceError):
        custom.check_url("file:///etc/passwd", carries_secret=False)
    custom.check_url("http://api.example.org/", carries_secret=False)
    monkeypatch.setattr(get_settings(), "custom_source_allowed_hosts", ["localhost"])
    custom.check_url("http://localhost:8000/api", carries_secret=True)


def test_redirect_to_internal_address_is_blocked(remote: Any) -> None:
    remote(lambda r: httpx.Response(302, headers={"location": "http://intranet.local/admin"}))
    result = custom.test_source(_defn(), "")
    assert not result["ok"] and result["stage"] == "url" and "interne" in result["message"]


def test_placeholders_auth_and_secret_redaction(remote: Any) -> None:
    seen = remote(lambda r: httpx.Response(200, json={"data": {"items": [{"id": 1, "position": {"lat": 5.1, "lon": -52.3}}]}}))
    defn = _defn(
        params=[{"key": "bbox", "value": "{bbox}"}, {"key": "from", "value": "{start}"}],
        headers=[{"key": "X-Client", "value": "kraken-{secret}"}],
        auth={"type": "header", "name": "X-API-Key"},
        mapping={"records_path": "data.items", "label": "id"},
    )
    result = custom.test_source(defn, "s3cr3t-value")
    assert result["ok"], result
    sent = seen[0]
    assert sent.headers["X-API-Key"] == "s3cr3t-value" and sent.headers["X-Client"] == "kraken-s3cr3t-value"
    assert sent.url.params["bbox"] == ",".join(str(v) for v in get_settings().eez_bbox)
    assert result["features"] == 1 and result["inside_zone"] == 1
    assert result["mapping_used"]["lat"] == "position.lat" and result["sample"][0]["properties"]["_label"] == 1
    # The secret is never echoed back in the test report.
    assert "s3cr3t-value" not in json.dumps(result)


def test_json_body_braces_survive_rendering(remote: Any) -> None:
    seen = remote(lambda r: httpx.Response(200, json={"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-52, 6]}, "properties": {"name": "A"}}]}))
    defn = _defn(method="POST", body='{"area": {"bbox": [{west}, {south}]}, "q": "{unknown}"}')
    result = custom.test_source(defn, "")
    assert result["ok"] and result["records_path"] == "features"
    body = json.loads(seen[0].content)
    assert body["area"]["bbox"] == [get_settings().eez_bbox[0], get_settings().eez_bbox[1]] and body["q"] == "{unknown}"


def test_csv_with_auto_detected_columns(remote: Any) -> None:
    remote(lambda r: httpx.Response(200, text="name;Latitude;Longitude\nA;5,2;-52,1\nB;;\n", headers={"content-type": "text/csv"}))
    result = custom.test_source(_defn(), "")
    assert result["ok"] and result["records"] == 2 and result["features"] == 1
    assert any("sans position" in w for w in result["warnings"])


def test_single_object_answer_is_one_record(remote: Any) -> None:
    remote(lambda r: httpx.Response(200, json={"latitude": 5.6, "longitude": -52.4, "current": {"wave_height": 1.4}}))
    result = custom.test_source(_defn(mapping={"label": "current.wave_height"}), "")
    assert result["ok"] and result["records_path"] == "." and result["features"] == 1
    assert result["sample"][0]["properties"]["current.wave_height"] == 1.4


def test_page_pagination_stops_on_empty_page(remote: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["p"])
        items = [{"lat": 5, "lon": -52, "n": page}] if page <= 2 else []
        return httpx.Response(200, json={"results": items})

    seen = remote(handler)
    defn = _defn(pagination={"type": "page", "param": "p", "start": 1, "max_pages": 5})
    collection = custom.fetch_features(defn, "")
    assert len(collection["features"]) == 2 and len(seen) == 3


def test_http_errors_and_html_are_explained(remote: Any) -> None:
    remote(lambda r: httpx.Response(401, text="bad key"))
    result = custom.test_source(_defn(), "")
    assert result["stage"] == "http" and "401" in result["message"] and "authentification" in result["message"]
    remote(lambda r: httpx.Response(200, text="<html>login</html>", headers={"content-type": "text/html"}))
    result = custom.test_source(_defn(), "")
    assert result["stage"] == "parse" and "HTML" in result["message"]


def test_wms_tile_request_and_non_image_answer(remote: Any) -> None:
    seen = remote(lambda r: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    defn = _defn(kind="wms", url="https://maps.example.org/wms", wms={"layers": "sst", "time": True})
    result = custom.test_source(defn, "", date="2026-10-19")
    assert result["ok"] and result["tile"]["preview"].startswith("data:image/png;base64,")
    params = seen[0].url.params
    assert params["REQUEST"] == "GetMap" and params["CRS"] == "EPSG:3857" and params["TIME"] == "2026-10-19"
    remote(lambda r: httpx.Response(200, text="<ServiceException>Layer not defined</ServiceException>", headers={"content-type": "text/xml"}))
    result = custom.test_source(defn, "")
    assert not result["ok"] and "Layer not defined" in result["message"]


def test_xyz_template_with_tms_y(remote: Any) -> None:
    seen = remote(lambda r: httpx.Response(200, content=PNG, headers={"content-type": "image/png"}))
    body, content_type = custom.fetch_tile(_defn(kind="tiles", url="https://t.example.org/{date}/{z}/{x}/{-y}.png"), "", 3, 2, 1, "2026-10-01")
    assert content_type == "image/png" and seen[0].url.path == "/2026-10-01/3/2/6.png"


def test_store_is_encrypted_and_secret_write_only(client_as: Callable[[str], Any], remote: Any) -> None:
    admin = client_as("admin")
    definition = {"name": "Bouées Météo", "url": "https://api.example.org/buoys", "auth": {"type": "bearer"}}
    created = admin.post("/api/v1/custom-sources", json={"definition": definition, "secret": "tok-123456789"})
    assert created.status_code == 200, created.text
    source = created.json()["source"]
    assert source["id"] == "bouees-meteo"
    assert source["has_secret"] and source["secret_hint"] == "••••6789" and "tok-123456789" not in created.text
    raw = (get_settings().keys_dir / custom.STORE_NAME).read_bytes()
    assert b"tok-123456789" not in raw and b"buoys" not in raw
    # Update without secret keeps it; the list never returns it.
    updated = admin.put(f"/api/v1/custom-sources/{source['id']}", json={"definition": {**definition, "enabled": False}})
    assert updated.status_code == 200 and updated.json()["source"]["has_secret"]
    listing = admin.get("/api/v1/custom-sources").json()
    assert "tok-123456789" not in json.dumps(listing) and listing["layers"] == []
    # Saved test uses the stored secret.
    seen = remote(lambda r: httpx.Response(200, json=[{"lat": 5, "lon": -52}]))
    assert admin.post(f"/api/v1/custom-sources/{source['id']}/test").json()["ok"]
    assert seen[0].headers["Authorization"] == "Bearer tok-123456789"
    assert admin.delete(f"/api/v1/custom-sources/{source['id']}").status_code == 200


def test_operators_see_layers_only_and_cannot_edit(client_as: Callable[[str], Any], remote: Any) -> None:
    custom.save_source(custom.SourceDefinition(name="Balises", url="https://api.example.org/b"), "secret-xyz-123")
    custom.save_source(custom.SourceDefinition(name="Tuiles", kind="tiles", url="https://t.example.org/{z}/{x}/{y}.png"), None)
    analyst = client_as("analyst")
    listing = analyst.get("/api/v1/custom-sources").json()
    assert "sources" not in listing and {l["id"] for l in listing["layers"]} == {"balises", "tuiles"}
    assert "url" not in listing["layers"][0]
    assert analyst.post("/api/v1/custom-sources", json={"definition": {"name": "x", "url": "https://a.example.org"}}).status_code == 403
    assert analyst.post("/api/v1/custom-sources/test", json={"definition": {"name": "x", "url": "https://a.example.org"}}).status_code == 403
    remote(lambda r: httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
           if r.url.host == "t.example.org" else httpx.Response(200, json={"items": [{"lat": 5.5, "lon": -52.5}]}))
    data = analyst.get("/api/v1/custom-sources/balises/data")
    assert data.status_code == 200 and data.json()["features"][0]["geometry"]["coordinates"] == [-52.5, 5.5]
    tile = analyst.get("/api/v1/custom-sources/tuiles/tiles/5/10/15")
    assert tile.status_code == 200 and tile.headers["content-type"] == "image/png" and tile.content == PNG
    assert analyst.get("/api/v1/custom-sources/inconnue/data").status_code == 404


def test_stale_copy_served_when_source_goes_down(remote: Any) -> None:
    custom.save_source(custom.SourceDefinition(name="Flux", url="https://api.example.org/f", refresh_minutes=1), None)
    remote(lambda r: httpx.Response(200, json=[{"lat": 5, "lon": -52}]))
    assert not custom.cached_features("flux")["stale"]
    for path in (get_settings().cache_dir / "custom_flux").glob("*.json"):
        os.utime(path, (0, 0))  # cache expired
    remote(lambda r: httpx.Response(503))
    stale = custom.cached_features("flux")
    assert stale["stale"] and len(stale["features"]) == 1 and "503" in stale["error"]


def test_invalid_definitions_rejected(client_as: Callable[[str], Any]) -> None:
    admin = client_as("admin")
    bad = [
        {"name": "x", "url": "https://a.example.org", "headers": [{"key": "Host", "value": "evil"}]},
        {"name": "x", "url": "https://a.example.org", "id": "UPPER"},
        {"name": "x", "url": "https://a.example.org", "unexpected": 1},
        {"name": "x", "url": "https://a.example.org", "style": {"color": "red"}},
    ]
    for definition in bad:
        assert admin.post("/api/v1/custom-sources", json={"definition": definition}).status_code == 422, definition


def test_docs_session_probe(client_as: Callable[[str], Any]) -> None:
    from fastapi.testclient import TestClient

    from server import app

    assert TestClient(app).get("/api/v1/auth/check").status_code == 401
    assert client_as("viewer").get("/api/v1/auth/check").status_code == 204
