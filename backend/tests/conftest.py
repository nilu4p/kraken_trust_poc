"""Test setup: importable backend, isolated data dirs, no network, authenticated clients."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable, Dict, Iterator

import os

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_PASSWORD = "Test-Passw0rd!long"
# KRAKEN_TEST_DATABASE_URL=postgresql://… runs the whole suite against PostgreSQL.
PG_URL = os.environ.get("KRAKEN_TEST_DATABASE_URL", "")
# Before any application import (settings, Celery broker): never the deployment's services.
os.environ.update({"DATABASE_URL": PG_URL, "DATABASE_PASSWORD_FILE": "", "REDIS_URL": "", "REDIS_PASSWORD_FILE": "",
                   "CELERY_BROKER_URL": "", "BACKGROUND_MODE": "inline"})
postgres_only = pytest.mark.skipif(not PG_URL, reason="PostgreSQL test database not configured")
sqlite_only = pytest.mark.skipif(bool(PG_URL), reason="SQLite-specific behaviour")


def _reset_postgres() -> None:
    from app.config import get_settings
    from app.db import TABLES, init_db, is_postgres, _pool

    cfg = get_settings()
    if not is_postgres(cfg):
        return
    init_db(cfg)
    with _pool(cfg).connection() as conn:
        conn.execute(f"TRUNCATE {', '.join(TABLES)} RESTART IDENTITY CASCADE")
        conn.commit()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Block outbound HTTP, isolate every on-disk store, reset rate limiters."""
    import httpx

    from app.config import get_settings
    from app.core.ratelimit import api_limiter, login_limiter

    def _offline(*_args: Any, **_kwargs: Any) -> Any:
        raise httpx.ConnectError("network disabled in tests")

    monkeypatch.setattr(httpx, "get", _offline)
    monkeypatch.setattr(httpx, "post", _offline)

    def _offline_transport(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("network disabled in tests")

    monkeypatch.setattr("app.sources.custom._TRANSPORT", httpx.MockTransport(_offline_transport))
    # Settings() built inside tests must not default to the real repository folders either.
    for env in ("DATA_DIR", "CACHE_DIR", "KEYS_DIR", "PREUVES_DIR", "BACKUP_DIR"):
        monkeypatch.setenv(env, str(tmp_path / "env" / env.lower()))
    live = get_settings()
    for name in ("cache_dir", "data_dir", "keys_dir", "preuves_dir", "backup_dir"):
        monkeypatch.setattr(live, name, tmp_path / "live" / name)
    for name in ("access_key", "secret_key", "gfw_token", "aisstream_api_key", "aishub_username", "opensanctions_api_key", "vesselapi_api_key",
                 "alert_webhook_url", "master_key", "master_key_file", "xview3_url"):
        monkeypatch.setattr(live, name, "")
    monkeypatch.setattr(live, "auto_scan_interval_minutes", 0)
    # Never touch the deployment's databases (``make test`` runs inside the backend container,
    # whose environment points at the production PostgreSQL / Redis): SQLite in tmp_path,
    # or the dedicated test database given by KRAKEN_TEST_DATABASE_URL.
    for env, value in (("DATABASE_URL", PG_URL), ("DATABASE_PASSWORD_FILE", ""), ("REDIS_URL", ""),
                       ("REDIS_PASSWORD_FILE", ""), ("CELERY_BROKER_URL", ""), ("BACKGROUND_MODE", "inline")):
        monkeypatch.setenv(env, value)
    for name, value in (("database_url", PG_URL), ("database_password_file", ""), ("redis_url", ""),
                        ("redis_password_file", ""), ("celery_broker_url", ""), ("background_mode", "inline")):
        monkeypatch.setattr(live, name, value)
    if PG_URL:
        _reset_postgres()
    monkeypatch.setattr("app.sources.stac.time.sleep", lambda _seconds: None)
    api_limiter.reset()
    login_limiter.reset()
    from app.core import shared

    shared._memory.flush()  # scan state, source health, locks, throttles of the previous test
    yield


@pytest.fixture()
def make_user() -> Callable[..., Dict[str, Any]]:
    from app.core import identity

    def _make(username: str, role: str, password: str = TEST_PASSWORD) -> Dict[str, Any]:
        return identity.create_user(username, password, role, must_change_password=False)

    return _make


@pytest.fixture()
def client_as(make_user: Callable[..., Dict[str, Any]]) -> Callable[[str], Any]:
    """``client_as("analyst")`` → TestClient logged in with that role (CSRF header set)."""
    from fastapi.testclient import TestClient

    from server import app

    def _client(role: str, username: str = "") -> TestClient:
        name = username or f"{role}-user"
        make_user(name, role)
        client = TestClient(app)
        response = client.post("/api/v1/auth/login", json={"username": name, "password": TEST_PASSWORD})
        assert response.status_code == 200, response.text
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        client.user = response.json()["user"]  # type: ignore[attr-defined]
        return client

    return _client
