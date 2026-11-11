"""Scalable deployment: shared state in Redis, Celery tasks, workers mode of the API."""

from __future__ import annotations

from typing import Any, Dict, List

import fakeredis
import pytest

from app.config import get_settings
from app.core import shared
from app.pipeline import scan_state


@pytest.fixture()
def redis_mode(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Every process of the deployment talks to the same (fake) Redis server."""
    server = fakeredis.FakeServer()
    client = fakeredis.FakeRedis(server=server, decode_responses=True)
    monkeypatch.setattr(get_settings(), "redis_url", "redis://redis:6379/0")
    monkeypatch.setattr(shared, "store", lambda settings=None: client)
    return client


@pytest.fixture()
def eager_celery(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.worker import celery_app

    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)


def test_scan_lock_and_state_are_shared(redis_mode: Any) -> None:
    assert scan_state.mark_running()
    assert not scan_state.mark_running()  # a second process cannot start another scan
    scan_state.mark_step("sar", "Analyse SAR", radar_targets=3)
    assert redis_mode.get("kraken:lock:scan") is not None
    state = scan_state.snapshot()
    assert state["status"] == "running" and state["stats"]["radar_targets"] == 3
    scan_state.mark_done(dark_vessel_count=1, stac_id="S1")
    assert scan_state.snapshot()["status"] == "done" and scan_state.mark_running()


def test_interrupted_scan_is_reported(redis_mode: Any) -> None:
    assert scan_state.mark_running()
    redis_mode.delete("kraken:lock:scan")  # lock expired: the worker died mid-scan
    state = scan_state.snapshot()
    assert state["status"] == "error" and "interrompu" in state["message"]


def test_scan_lock_is_short_lived_and_kept_alive(redis_mode: Any) -> None:
    assert scan_state.mark_running()
    ttl = redis_mode.ttl("kraken:lock:scan")
    assert 0 < ttl <= scan_state.LOCK_TTL  # a dead worker frees the platform within minutes
    redis_mode.expire("kraken:lock:scan", 5)
    shared.refresh(scan_state.LOCK, scan_state.LOCK_TTL)  # what the heartbeat does
    assert redis_mode.ttl("kraken:lock:scan") > 5
    scan_state.mark_error("x")
    assert redis_mode.get("kraken:lock:scan") is None and scan_state._heartbeat_stop is None


def test_scan_never_picked_up_is_reported(redis_mode: Any) -> None:
    import datetime as dt
    import json

    scan_state.mark_queued("S1")
    assert scan_state.snapshot()["status"] == "queued"  # just queued: waiting for the worker
    state = json.loads(redis_mode.get("kraken:" + scan_state.KEY))
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=scan_state.QUEUE_TIMEOUT_S + 60)
    redis_mode.set("kraken:" + scan_state.KEY, json.dumps({**state, "started_at": old.isoformat()}))
    snap = scan_state.snapshot()
    assert snap["status"] == "error" and "worker de scan" in snap["message"]


def test_health_and_throttle_are_shared(redis_mode: Any) -> None:
    from app.sources import health

    health.record("gfw", False, "HTTP 503")
    health.record("gfw", False, "HTTP 503")
    assert health.snapshot()["gfw"]["consecutive_failures"] == 2
    assert shared.first_in("notify:x", 60) and not shared.first_in("notify:x", 60)


def test_redis_password_comes_from_the_secret_file(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = tmp_path / "redis_password"
    secret.write_text("s3cr3t/+\n")
    cfg = get_settings().model_copy(update={"redis_url": "redis://redis:6379/0", "redis_password_file": str(secret)})
    assert shared.redis_url(cfg) == "redis://:s3cr3t%2F%2B@redis:6379/0"
    from app.db import database_dsn

    db_secret = tmp_path / "pg"
    db_secret.write_text("pa ss\n")
    pg = get_settings().model_copy(update={"database_url": "postgresql://kraken@postgres:5432/kraken",
                                           "database_password_file": str(db_secret)})
    assert database_dsn(pg) == "postgresql://kraken:pa%20ss@postgres:5432/kraken"


def test_workers_mode_api_queues_scans(client_as: Any, redis_mode: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "background_mode", "workers")
    sent: List[Any] = []
    from app import tasks

    monkeypatch.setattr(tasks.scan, "apply_async", lambda args, queue: sent.append((args, queue)))
    analyst = client_as("analyst")
    response = analyst.post("/api/v1/scan")
    assert response.status_code == 200 and "file" in response.json()["message"]
    assert sent == [([None, "analyst-user"], "scan")]
    assert analyst.get("/api/v1/scan/status").json()["status"] == "queued"
    assert analyst.post("/api/v1/scan").status_code == 409


def test_scan_task_runs_the_pipeline(eager_celery: None, redis_mode: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: List[Any] = []
    monkeypatch.setattr("app.pipeline.orchestrator.run_kraken_pipeline", lambda cfg, scene, actor: calls.append((scene, actor)))
    from app import tasks

    tasks.enqueue_scan("S1A_X", "analyste")
    assert calls == [("S1A_X", "analyste")]


def test_rule_ticks_never_overlap(eager_celery: None, redis_mode: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    runs: List[int] = []
    monkeypatch.setattr("app.soc.rules.run_rules", lambda cfg=None: runs.append(1) or {})
    from app import tasks

    tasks.rules_tick.delay()
    assert shared.acquire("tick:rules", 60)  # simulate a slow tick still running in another worker
    tasks.rules_tick.delay()
    assert runs == [1]


def test_auto_scan_enqueues_once_per_interval(eager_celery: None, redis_mode: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import tasks
    from app.soc import runtime

    runtime.update({"auto_scan_interval_minutes": 60}, "admin")  # saved from the dashboard by another process
    queued: List[Any] = []
    monkeypatch.setattr("app.soc.scheduler.next_unprocessed_scene", lambda cfg=None: "S1A_NEW")
    monkeypatch.setattr(tasks.auto_scan, "apply_async", lambda args, queue: queued.append((args, queue)))
    assert tasks.auto_scan_tick.delay() is not None
    tasks.auto_scan_tick.delay()
    assert queued == [(["S1A_NEW"], "scan")]


def test_notifications_go_through_the_retry_queue(redis_mode: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.soc.notify as notify_module

    cfg = get_settings()
    monkeypatch.setattr(cfg, "background_mode", "workers")
    monkeypatch.setattr(cfg, "alert_webhook_url", "https://hooks.example.org/{channel}")
    queued: List[Dict[str, Any]] = []
    from app import tasks

    monkeypatch.setattr(tasks.notify, "apply_async", lambda args, queue: queued.append({"url": args[0], "queue": queue}))
    assert notify_module.publish("alertes", "test")
    assert queued == [{"url": "https://hooks.example.org/alertes", "queue": "notify"}]


def test_readiness_probe(client_as: Any) -> None:
    from fastapi.testclient import TestClient

    from server import app

    body = TestClient(app).get("/health/ready").json()
    assert body == {"status": "ok", "database": True, "shared_state": True}


def test_login_limits_are_shared_between_api_processes(redis_mode: Any) -> None:
    from app.core.ratelimit import SlidingWindowLimiter

    process_a, process_b = SlidingWindowLimiter(3, name="login"), SlidingWindowLimiter(3, name="login")
    assert process_a.hit("ip:1.2.3.4") and process_b.hit("ip:1.2.3.4") and process_a.hit("ip:1.2.3.4")
    assert not process_b.hit("ip:1.2.3.4")  # 4th attempt refused, whichever process receives it
