"""Celery tasks: thin wrappers around the same functions the inline mode runs.

Every task first reloads the keys and operational settings saved from the
dashboard (another process may have changed them), then does its job. Rules
and SLA ticks are guarded by a shared lock so a slow tick never overlaps the
next one, even with several workers.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from app.config import get_settings
from app.core import shared
from app.worker import celery_app

logger = logging.getLogger(__name__)


def refresh_settings() -> Any:
    """Keys (encrypted vault) and runtime settings (database) as saved from the dashboard."""
    from app.core.credentials import apply_stored_credentials
    from app.soc import runtime

    cfg = get_settings()
    apply_stored_credentials(cfg)
    runtime.apply_stored(cfg)
    return cfg


def _exclusive(name: str, ttl: float, job: Any) -> Any:
    if not shared.acquire(f"tick:{name}", ttl):
        logger.info("Tick %s still running elsewhere: skipped", name)
        return None
    try:
        return job()
    finally:
        shared.release(f"tick:{name}")


@celery_app.task(name="kraken.scan", acks_late=True, time_limit=3 * 3600, soft_time_limit=3 * 3600 - 300)
def scan(scene_id: Optional[str] = None, actor: str = "system") -> None:
    from app.pipeline.orchestrator import run_kraken_pipeline

    run_kraken_pipeline(refresh_settings(), scene_id, actor)


@celery_app.task(name="kraken.rules")
def rules_tick() -> Any:
    from app.soc.rules import run_rules

    cfg = refresh_settings()
    return _exclusive("rules", 300, lambda: run_rules(cfg))


@celery_app.task(name="kraken.sla")
def sla_tick() -> Any:
    from app.soc.scheduler import check_sla

    cfg = refresh_settings()
    return _exclusive("sla", 300, lambda: check_sla(cfg))


@celery_app.task(name="kraken.sources")
def sources_tick() -> Any:
    from app.soc.scheduler import watch_sources

    return _exclusive("sources", 300, lambda: watch_sources(refresh_settings()))


@celery_app.task(name="kraken.hourly")
def hourly() -> None:
    from app.soc.scheduler import check_audit_chain
    from app.sources import ais_store

    from app.sources import coastline

    cfg = refresh_settings()
    ais_store.purge(cfg)
    check_audit_chain(cfg)
    coastline.refresh_if_stale(cfg)  # OSM coastline, every 30 days (bundled snapshot otherwise)


@celery_app.task(name="kraken.sensors", time_limit=50 * 60)
def sensors_tick() -> Any:
    """New Sentinel-2 / Landsat scenes and VIIRS nights over the EEZ (multi-sensor detection)."""
    from app.soc.sensors import watch_tick

    return _exclusive("sensors", 3000, lambda: watch_tick(refresh_settings()))


@celery_app.task(name="kraken.auto_scan_tick")
def auto_scan_tick() -> Optional[str]:
    """Enqueue the next unprocessed Sentinel-1 scene when the watch interval has elapsed."""
    from app.pipeline.scan_state import snapshot
    from app.soc.scheduler import next_unprocessed_scene

    cfg = refresh_settings()
    interval = cfg.auto_scan_interval_minutes
    if interval <= 0 or snapshot().get("status") in ("running", "queued"):
        return None
    # One enqueue per interval, whatever the number of beat / worker processes.
    if not shared.first_in("auto_scan", interval * 60):
        return None
    scene_id = next_unprocessed_scene(cfg)
    if scene_id:
        logger.info("Automatic surveillance: queueing scene %s", scene_id)
        auto_scan.apply_async(args=[scene_id], queue="scan")
    return scene_id


@celery_app.task(name="kraken.auto_scan", acks_late=True, time_limit=3 * 3600)
def auto_scan(scene_id: str) -> None:
    from app.soc.scheduler import auto_scan as run_auto_scan

    run_auto_scan(scene_id, refresh_settings())


@celery_app.task(name="kraken.notify", bind=True, max_retries=6, acks_late=True)
def notify(self: Any, url: str, payload: Dict[str, Any]) -> bool:
    """Webhook delivery; retried 30 s, 1, 2, 4, 8, 16 min before giving up."""
    from app.soc.notify import _post

    if _post(url, payload, get_settings()):
        return True
    raise self.retry(countdown=min(30 * 2 ** self.request.retries, 960))


def enqueue_scan(scene_id: Optional[str], actor: str) -> None:
    from app.pipeline.scan_state import mark_queued

    mark_queued(scene_id)
    scan.apply_async(args=[scene_id, actor], queue="scan")


