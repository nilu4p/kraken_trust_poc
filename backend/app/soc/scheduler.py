"""Background surveillance loop: SLA watch + automatic Sentinel-1 scanning of new scenes."""

from __future__ import annotations

import asyncio
import datetime
import logging
from typing import Optional

from app.config import Settings, get_settings
from app.core import shared
from app.db import read_db
from app.soc import incidents, notify

logger = logging.getLogger(__name__)

TICK_SECONDS = 60


def check_sla(settings: Optional[Settings] = None) -> int:
    """Flag (once) every open incident whose SLA is breached; returns how many were new."""
    cfg = settings or get_settings()
    flagged = 0
    for incident in incidents.list_incidents(open_only=True, settings=cfg):
        if not incident["sla"]["breached"]:
            continue
        stage = "triage" if not incident["acknowledged_at"] else "resolution"
        with read_db(cfg) as conn:
            already = conn.execute(
                "SELECT 1 FROM incident_events WHERE incident_id = ? AND kind = 'sla_breach' AND payload LIKE ?",
                (incident["id"], f'%"{stage}"%'),
            ).fetchone()
        if already:
            continue
        incidents.record_event(incident["id"], "system", "sla_breach", {"stage": stage}, cfg)
        notify.notify_incident(
            incident,
            f"Échéance de {'triage' if stage == 'triage' else 'résolution'} dépassée",
            cfg,
            channel="sla",
            extra=[{"title": "Étape en retard", "value": "prise en charge (triage)" if stage == "triage" else "résolution", "short": True}],
        )
        flagged += 1
    return flagged


def next_unprocessed_scene(settings: Optional[Settings] = None) -> Optional[str]:
    from app.pipeline.ingestion import search_recent_scenes

    cfg = settings or get_settings()
    scenes = search_recent_scenes(cfg, limit=10).get("features", [])
    with read_db(cfg) as conn:
        done = {r[0] for r in conn.execute("SELECT scene_id FROM processed_scenes").fetchall()}
    for feature in scenes:  # most recent first
        scene_id = feature["properties"]["scene_id"]
        if scene_id not in done:
            return scene_id
    return None


SOURCE_STATUS = "source_status"  # last status seen per source (shared: beat may run anywhere)


COLLECTION_STATE = "ais_collection_state"
COLLECTION_WINDOW = datetime.timedelta(minutes=30)
COLLECTION_ALERT_BELOW = 0.5     # degraded: less than half of the last 30 minutes listened to
COLLECTION_RECOVER_ABOVE = 0.8   # hysteresis: back to normal only once clearly recovered


def watch_ais_collection(settings: Optional[Settings] = None, now: Optional[datetime.datetime] = None) -> Optional[str]:
    """Alert #operations when the AIS collection listens to less than 50 % of the last 30 minutes.

    Below that, an empty sea on the map means "no data", radar echoes are reported
    "not identified" instead of "without AIS", and the AIS-gap rule is blind.
    Returns "degraded" / "recovered" when a notification was sent, else None.
    """
    from app.sources import feed_log, health

    cfg = settings or get_settings()
    if not cfg.ais_collector_enabled or not (cfg.aisstream_api_key or cfg.aishub_username
                                             or (cfg.vesselapi_api_key and cfg.vesselapi_poll_minutes)):
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    start = now - COLLECTION_WINDOW
    if not feed_log.logged_before(start, settings=cfg):
        return None  # collection just started (or never ran): nothing to compare yet
    coverage = feed_log.coverage(start, now, settings=cfg) or 0.0
    state = shared.get_json(COLLECTION_STATE, {}) or {}
    health.record("ais_collection", coverage >= COLLECTION_ALERT_BELOW,
                  f"{round(coverage * 100)} % des 30 dernières minutes écoutées")
    if not state.get("degraded") and coverage < COLLECTION_ALERT_BELOW:
        per_feed = {feed: feed_log.coverage(start, now, feeds=(feed,), settings=cfg) for feed in feed_log.AIS_FEEDS}
        gaps = feed_log.gaps(now - datetime.timedelta(hours=6), now, min_minutes=10, settings=cfg)
        shared.set_json(COLLECTION_STATE, {"degraded": True, "since": now.isoformat(), "coverage": coverage})
        notify.notify_collection(True, coverage, per_feed, gaps, None, cfg)
        return "degraded"
    if state.get("degraded") and coverage >= COLLECTION_RECOVER_ABOVE:
        since = state.get("since")
        minutes = None
        if since:
            minutes = round((now - datetime.datetime.fromisoformat(since)).total_seconds() / 60)
        shared.set_json(COLLECTION_STATE, {"degraded": False, "since": now.isoformat(), "coverage": coverage})
        notify.notify_collection(False, coverage, {}, [], minutes, cfg)
        return "recovered"
    return None


def watch_sources(settings: Optional[Settings] = None) -> int:
    """Notify #operations when a data source goes down (3 failures in a row) or recovers."""
    from app.sources import health

    changes = 0
    try:
        if watch_ais_collection(settings):
            changes += 1
    except Exception:  # the collection check must never stop the source watch
        logger.exception("AIS collection check failed")
    for name, entry in health.snapshot().items():
        previous = shared.hget_json(SOURCE_STATUS, name)
        status = entry["status"]
        shared.hset_json(SOURCE_STATUS, name, status)
        if previous is None:
            continue
        if status == "down" and previous != "down":
            notify.notify_source_health(name, "down", entry.get("last_error", ""), settings)
            changes += 1
        elif status == "ok" and previous == "down":
            notify.notify_source_health(name, "ok", "", settings)
            changes += 1
    return changes


def check_audit_chain(settings: Optional[Settings] = None) -> bool:
    """Hourly integrity check of the audit chain; alert #securite if broken."""
    from app.core import audit

    result = audit.verify_chain(settings)
    if not result["valid"]:
        notify.notify_security(
            "Journal d'audit ALTÉRÉ : chaîne de hachage rompue",
            level="danger",
            details={"Première entrée invalide": f"#{result['broken_at']}", "Entrées": result["entries"]},
            key="audit-broken",
            cooldown=6 * 3600,
            settings=settings,
        )
    return result["valid"]


MAX_SCENE_ATTEMPTS = 3


def auto_scan(scene_id: str, settings: Optional[Settings] = None) -> None:
    """Run the pipeline on one scene; give up on it after MAX_SCENE_ATTEMPTS transient failures."""
    from app.pipeline.orchestrator import _mark_processed, run_kraken_pipeline

    cfg = settings or get_settings()
    logger.info("Automatic surveillance: scanning new scene %s", scene_id)
    run_kraken_pipeline(cfg, scene_id, "auto-surveillance")
    with read_db(cfg) as conn:
        done = conn.execute("SELECT 1 FROM processed_scenes WHERE scene_id = ?", (scene_id,)).fetchone()
    counter = f"scene_failures:{scene_id}"
    if done:
        shared.delete(counter)
        return
    if shared.incr(counter) >= MAX_SCENE_ATTEMPTS:
        logger.warning("Scene %s abandoned after %s failed attempts", scene_id, MAX_SCENE_ATTEMPTS)
        _mark_processed(scene_id, "abandoned", cfg)
        shared.delete(counter)


async def surveillance_loop(settings: Optional[Settings] = None) -> None:
    from app.pipeline.scan_state import snapshot

    cfg = settings or get_settings()
    scan_task: Optional[asyncio.Task] = None
    last_scan: Optional[datetime.datetime] = None
    last_audit_check: Optional[datetime.datetime] = None
    last_purge: Optional[datetime.datetime] = None
    sensor_task: Optional[asyncio.Task] = None
    last_sensors: Optional[datetime.datetime] = None
    while True:
        try:
            await asyncio.to_thread(check_sla, cfg)
            await asyncio.to_thread(watch_sources, cfg)
            # 24/7 AIS rules (foreign fishing, watchlist, gaps, encounters) between satellite passes.
            from app.soc.rules import run_rules
            from app.sources import ais_store

            await asyncio.to_thread(run_rules, cfg)
            now_tick = datetime.datetime.now(datetime.timezone.utc)
            if last_purge is None or (now_tick - last_purge).total_seconds() >= 3600:
                last_purge = now_tick
                await asyncio.to_thread(ais_store.purge, cfg)
            if last_audit_check is None or (datetime.datetime.now(datetime.timezone.utc) - last_audit_check).total_seconds() >= 3600:
                last_audit_check = datetime.datetime.now(datetime.timezone.utc)
                await asyncio.to_thread(check_audit_chain, cfg)
            # Optical / thermal / night-light watch: hourly, in the background (remote image reads).
            if cfg.sensor_watch_enabled and (sensor_task is None or sensor_task.done()) and (
                    last_sensors is None or (now_tick - last_sensors).total_seconds() >= 3600):
                last_sensors = now_tick
                from app.soc.sensors import watch_tick

                sensor_task = asyncio.create_task(asyncio.to_thread(watch_tick, cfg))
            interval = cfg.auto_scan_interval_minutes
            now = datetime.datetime.now(datetime.timezone.utc)
            due = interval > 0 and (last_scan is None or (now - last_scan).total_seconds() >= interval * 60)
            idle = scan_task is None or scan_task.done()
            if due and idle and snapshot().get("status") != "running":
                last_scan = now
                scene_id = await asyncio.to_thread(next_unprocessed_scene, cfg)
                if scene_id:
                    # Background task: a 1-3 min scan must not pause SLA checks and 24/7 rules.
                    scan_task = asyncio.create_task(asyncio.to_thread(auto_scan, scene_id, cfg))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Surveillance loop error: %s", exc)
        await asyncio.sleep(TICK_SECONDS)
