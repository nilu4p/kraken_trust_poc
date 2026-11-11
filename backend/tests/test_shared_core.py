"""Security core of the platform (authentication, sessions, audit, custom sources).

Client address behind proxies, login lockout, role-aware session probe, password-guessing limit,
session cookie prefix, security headers, audit chain verified in batches and partial actor filter,
custom-source secret never sent to a foreign host, DNS rebinding, imagery tiles bound to the
territory, and the reverse-proxy configuration (nginx) that fronts the API.
"""

from __future__ import annotations

import math
import re
from pathlib import Path
from typing import Any, Callable, Dict, List

import httpx
import pytest

from app.config import get_settings
from app.core import audit, identity
from app.sources import custom, sentinel2
from tests.conftest import TEST_PASSWORD

NGINX_CONF = Path(__file__).resolve().parents[2] / "frontend" / "nginx.conf"


def _territory_bbox() -> Any:
    cfg = get_settings()
    return getattr(cfg, "territory_bbox", None) or cfg.eez_bbox


# --------------------------------------------------------------------------- identity, sessions


def test_spoofed_forwarded_for_is_not_the_client_address() -> None:
    from app.core.ratelimit import client_ip

    # nginx appends the address it saw: whatever the client put on the left is not trusted.
    assert client_ip("172.18.0.5", "6.6.6.6, 203.0.113.7") == "203.0.113.7"
    assert client_ip("172.18.0.5", "203.0.113.7, 172.18.0.9") == "203.0.113.7"  # two trusted hops
    assert client_ip("172.18.0.5", "garbage, 203.0.113.7") == "203.0.113.7"
    assert client_ip("172.18.0.5", "203.0.113.7, garbage") == "172.18.0.5"  # malformed last hop: peer kept
    assert client_ip("198.51.100.9", "6.6.6.6") == "198.51.100.9"  # untrusted peer: header ignored


def test_failed_logins_lock_the_account(make_user: Callable[..., Dict[str, Any]]) -> None:
    cfg = get_settings()
    make_user("lockme", "viewer")
    for _ in range(cfg.login_max_attempts):
        with pytest.raises(identity.AuthError):
            identity.authenticate("lockme", "wrong-password", ip="203.0.113.1")
    with pytest.raises(identity.AuthError) as exc:
        identity.authenticate("lockme", TEST_PASSWORD, ip="203.0.113.1")
    assert exc.value.status == 423


def test_session_probe_checks_the_requested_role(client_as: Callable[[str], Any]) -> None:
    viewer, supervisor = client_as("viewer"), client_as("supervisor")
    assert viewer.get("/api/v1/auth/check").status_code == 204
    assert viewer.get("/api/v1/auth/check", params={"role": "supervisor"}).status_code == 403
    assert supervisor.get("/api/v1/auth/check", params={"role": "supervisor"}).status_code == 204
    assert supervisor.get("/api/v1/auth/check", params={"role": "root"}).status_code == 422


def test_current_password_guessing_is_rate_limited(client_as: Callable[[str], Any]) -> None:
    client = client_as("viewer")
    codes = [client.post("/api/v1/auth/password", json={"current_password": f"nope-{n}", "new_password": "x"}).status_code
             for n in range(12)]
    assert codes[0] == 400 and codes[-1] == 429


def test_session_cookie_is_host_bound_over_https(make_user: Callable[..., Dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from server import app

    make_user("secure-user", "viewer")
    monkeypatch.setattr(get_settings(), "cookie_secure", True)
    client = TestClient(app, base_url="https://testserver")
    response = client.post("/api/v1/auth/login", json={"username": "secure-user", "password": TEST_PASSWORD})
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("__Host-") and "Secure" in cookie and "Path=/" in cookie and "Domain" not in cookie
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert response.headers["strict-transport-security"].startswith("max-age=")
    csrf = response.json()["csrf_token"]
    assert client.get("/api/v1/auth/me").status_code == 200
    logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 200 and "__Host-" in logout.headers["set-cookie"] and "Secure" in logout.headers["set-cookie"]
    assert client.get("/api/v1/auth/me").status_code == 401


def test_api_security_headers(client_as: Callable[[str], Any]) -> None:
    response = client_as("viewer").get("/api/v1/auth/me")
    headers = response.headers
    assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"
    assert headers["referrer-policy"] == "no-referrer" and headers["cache-control"] == "no-store"
    assert headers["cross-origin-resource-policy"] == "same-origin"
    assert headers["x-permitted-cross-domain-policies"] == "none"
    assert "camera=()" in headers["permissions-policy"] and "geolocation=()" in headers["permissions-policy"]
    assert "default-src 'none'" in headers["content-security-policy"]


# --------------------------------------------------------------------------- audit trail


def test_audit_chain_is_verified_in_batches(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.db import db

    monkeypatch.setattr(audit, "VERIFY_BATCH", 7)
    for n in range(30):
        audit.record("test.batch", actor="t", details={"n": n})
    result = audit.verify_chain()
    assert result["valid"] and result["entries"] == 30
    with db() as conn:
        conn.execute("UPDATE audit_log SET actor = 'mallory' WHERE id = 16")  # third batch
    broken = audit.verify_chain()
    assert not broken["valid"] and broken["broken_at"] == 16 and broken["entries"] == 30


def test_audit_filters_by_action_family_and_partial_actor(client_as: Callable[..., Any]) -> None:
    supervisor = client_as("supervisor", "chef-de-service")
    audit.record("export.test", actor="analyste-ouest", target="test:1")
    by_family = supervisor.get("/api/v1/audit", params={"action": "export."}).json()["entries"]
    assert [e["action"] for e in by_family] == ["export.test"]
    by_actor = supervisor.get("/api/v1/audit", params={"actor": "OUEST"}).json()["entries"]
    assert by_actor and all("ouest" in e["actor"].lower() for e in by_actor)
    # LIKE wildcards typed by the user are not wildcards.
    assert supervisor.get("/api/v1/audit", params={"actor": "%"}).json()["entries"] == supervisor.get("/api/v1/audit").json()["entries"]


# --------------------------------------------------------------------------- pluggable sources (SSRF, secrets)


@pytest.fixture()
def remote(monkeypatch: pytest.MonkeyPatch) -> Callable[[Callable[[httpx.Request], httpx.Response]], List[httpx.Request]]:
    def _install(handler: Callable[[httpx.Request], httpx.Response]) -> List[httpx.Request]:
        seen: List[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return handler(request)

        monkeypatch.setattr(custom, "_TRANSPORT", httpx.MockTransport(_record))
        monkeypatch.setattr(custom, "_resolve", lambda host, port: ["93.184.216.34"])
        return seen

    return _install


@pytest.mark.parametrize("auth", [{"type": "query", "name": "key"}, {"type": "header", "name": "X-Key"}, {"type": "basic", "username": "u"}])
def test_next_page_on_another_host_never_receives_the_secret(remote: Any, auth: Dict[str, str]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.example.org":
            return httpx.Response(200, json={"items": [{"lat": 5, "lon": -52}], "next": "https://evil.example.net/page2"})
        return httpx.Response(200, json={"items": []})

    seen = remote(handler)
    defn = custom.SourceDefinition(name="T", url="https://api.example.org/v1/items", auth=auth,
                                   pagination={"type": "next", "next_path": "next", "max_pages": 3}).model_dump()
    custom.fetch_features(defn, "S3CRET-VALUE")
    assert [r.url.host for r in seen] == ["api.example.org", "evil.example.net"]
    foreign = seen[1]
    assert "S3CRET-VALUE" not in str(foreign.url)
    assert all("S3CRET-VALUE" not in v for v in foreign.headers.values())
    assert "authorization" not in {k.lower() for k in foreign.headers}


def test_next_page_on_the_same_host_keeps_the_query_secret(remote: Any) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/items":
            return httpx.Response(200, json={"items": [{"lat": 5, "lon": -52}], "next": "/v1/items2?cursor=abc"})
        return httpx.Response(200, json={"items": []})

    seen = remote(handler)
    defn = custom.SourceDefinition(name="T", url="https://api.example.org/v1/items", auth={"type": "query", "name": "key"},
                                   pagination={"type": "next", "next_path": "next", "max_pages": 3}).model_dump()
    custom.fetch_features(defn, "S3CRET-VALUE")
    assert seen[1].url.params["key"] == "S3CRET-VALUE" and seen[1].url.params["cursor"] == "abc"


def test_query_written_in_the_url_survives_declared_parameters(remote: Any) -> None:
    seen = remote(lambda r: httpx.Response(200, json={"items": [{"lat": 5, "lon": -52}]}))
    defn = custom.SourceDefinition(name="T", url="https://api.example.org/v1/items?format=json&lang=fr",
                                   params=[{"key": "limit", "value": "50"}]).model_dump()
    custom.fetch_features(defn, "")
    assert dict(seen[0].url.params) == {"format": "json", "lang": "fr", "limit": "50"}


class _Stream:
    def __init__(self, address: str) -> None:
        self.address = address

    def get_extra_info(self, name: str) -> Any:
        return (self.address, 443) if name == "server_addr" else None


def test_connection_to_an_internal_address_is_refused_after_resolution() -> None:
    """DNS rebinding: the name resolved public for the check, the socket ended up on an internal address."""
    response = httpx.Response(200, extensions={"network_stream": _Stream("10.1.2.3")})
    with pytest.raises(custom.CustomSourceError, match="interne"):
        custom._check_peer(response, "https://api.example.org/x", get_settings())
    custom._check_peer(httpx.Response(200, extensions={"network_stream": _Stream("93.184.216.34")}), "https://api.example.org/x",
                       get_settings())
    custom._check_peer(httpx.Response(200), "https://api.example.org/x", get_settings())  # no socket info (mock transport)


# --------------------------------------------------------------------------- imagery tiles


def test_sentinel2_tiles_are_served_over_the_territory_only() -> None:
    west, south, east, north = _territory_bbox()
    x, y = sentinel2.lonlat_to_tile((west + east) / 2, (south + north) / 2, 12)
    assert sentinel2.tile_in_territory(12, x, y)
    px, py = sentinel2.lonlat_to_tile(2.35, 48.85, 12)  # Paris
    assert not sentinel2.tile_in_territory(12, px, py)
    with pytest.raises(sentinel2.Sentinel2Error, match="territoire"):
        sentinel2.render_tile("S2A_22NCL_20250101_0_L2A", 12, px, py)
    with pytest.raises(sentinel2.Sentinel2Error, match="identifiant"):
        sentinel2.render_tile("../../keys", 12, x, y)
    assert math.isfinite(x) and math.isfinite(y)


# --------------------------------------------------------------------------- reverse proxy (nginx)


def _nginx() -> str:
    return NGINX_CONF.read_text(encoding="utf-8")


def test_console_csp_has_no_third_party_scripts_or_fonts() -> None:
    conf = _nginx()
    spa_csp = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', conf).group(1)
    assert "script-src 'self';" in spa_csp and "'unsafe-eval'" not in spa_csp
    assert "font-src 'self';" in spa_csp and "fonts.g" not in conf
    assert "frame-ancestors 'none'" in spa_csp and "object-src 'none'" in spa_csp


def test_login_is_throttled_and_notifications_need_a_supervisor() -> None:
    conf = _nginx()
    assert "limit_req_zone $binary_remote_addr zone=login" in conf
    login = conf[conf.index("location = /api/v1/auth/login"):]
    assert "limit_req zone=login" in login[: login.index("}")]
    notifs = conf[conf.index("location /notifs/ {"):]
    assert "auth_request /_auth/check_supervisor;" in notifs[: notifs.index("}")]
    assert "location ^~ /notifs/hooks/ { return 403; }" in conf
    assert "/api/v1/auth/check?role=supervisor" in conf
    assert "server_tokens off;" in conf and "ssl_protocols TLSv1.2 TLSv1.3;" in conf
