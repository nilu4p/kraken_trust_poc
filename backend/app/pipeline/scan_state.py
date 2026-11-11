"""Scan job status, shared between the API and the scan worker (Redis, or memory in one process).

A cross-process lock guarantees a single scan at a time. The scanning process
refreshes it every ``HEARTBEAT_S`` seconds; it lapses ``LOCK_TTL`` seconds after
that process dies (OOM, container restart), so the status is then reported as
interrupted within minutes instead of staying "running" for hours. A scan still
"queued" after ``QUEUE_TIMEOUT_S`` means no scan worker is consuming the queue.
"""

from __future__ import annotations

import json

import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional

from app.core import shared

Status = Literal["idle", "queued", "running", "done", "error"]

KEY = "scan:state"
LOCK = "scan"
LOCK_TTL = 120
HEARTBEAT_S = 30
QUEUE_TIMEOUT_S = 10 * 60
_local = threading.Lock()  # serialises read-modify-write inside one process
_heartbeat_stop: Optional[threading.Event] = None

IDLE: Dict[str, Any] = {
    "status": "idle",
    "message": "Aucun scan en cours",
    "started_at": None,
    "finished_at": None,
    "dark_vessel_count": 0,
    "stac_id": None,
    "step": None,
    "stats": {},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> Dict[str, Any]:
    return {**IDLE, **(shared.get_json(KEY) or {})}


def _update(**changes: Any) -> None:
    with _local:
        shared.set_json(KEY, {**_read(), **changes})


def snapshot() -> Dict[str, Any]:
    state = _read()
    if state["status"] == "running" and not shared.held(LOCK):
        # The scanning process died (container restart, OOM…): do not stay "running" forever.
        state.update(status="error", message="Scan interrompu (processus arrêté) : relancez le scan")
    elif state["status"] == "queued" and not shared.held(LOCK) and _older_than(state.get("started_at"), QUEUE_TIMEOUT_S):
        # Nobody took the task: worker-scan stopped, restarting in a loop (memory) or not consuming "scan".
        state.update(status="error", message="Scan jamais pris en charge : le worker de scan ne répond pas "
                     "(docker compose ps worker-scan / docker compose logs worker-scan) — relancez le scan")
    return state


def _older_than(stamp: Optional[str], seconds: float) -> bool:
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(stamp) > timedelta(seconds=seconds)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False


def _start_heartbeat() -> None:
    global _heartbeat_stop
    stop = threading.Event()
    _heartbeat_stop = stop

    def beat() -> None:
        while not stop.wait(HEARTBEAT_S):
            try:
                shared.refresh(LOCK, LOCK_TTL)
            except Exception:  # Redis blip: the next beat retries before the lock lapses
                pass

    threading.Thread(target=beat, name="scan-heartbeat", daemon=True).start()


def _stop_heartbeat() -> None:
    global _heartbeat_stop
    if _heartbeat_stop is not None:
        _heartbeat_stop.set()
        _heartbeat_stop = None


def mark_queued(scene_id: Optional[str]) -> None:
    """Accepted by the API, waiting for a scan worker."""
    _update(status="queued", message="Scan en file d'attente (worker de scan)", step="queued",
            started_at=_now(), finished_at=None, stats={}, stac_id=scene_id)


def mark_running() -> bool:
    """Take the scan lock; False if a scan is already running anywhere."""
    if not shared.acquire(LOCK, LOCK_TTL):
        return False
    _start_heartbeat()
    _update(status="running", message="Scan Sentinel-1 en cours", started_at=_now(), finished_at=None,
            step="init", stats={})
    return True


def mark_step(step: str, message: str, **stats: Any) -> None:
    """Record pipeline progress (shown live in the dashboard)."""
    with _local:
        state = _read()
        shared.set_json(KEY, {**state, "step": step, "message": message, "stats": {**state.get("stats", {}), **stats}})


def add_stats(**stats: Any) -> None:
    """Merge counters into the current scan report (used for the end-of-scan notification)."""
    with _local:
        state = _read()
        shared.set_json(KEY, {**state, "stats": {**state.get("stats", {}), **stats}})


def mark_done(*, dark_vessel_count: int, stac_id: Optional[str]) -> None:
    stats = _read().get("stats", {})
    rescan = stats.get("rescan")
    coverage = stats.get("sar_coverage_pct")
    _update(
        step="done",
        status="done",
        message=f"{dark_vessel_count} navire(s) sans AIS"
        + (f" sur {coverage:g} % de la partie ZEE de la scène ({stats.get('sar_tiles')}/{stats.get('sar_tiles_in_eez')} tuiles)"
           if coverage is not None and coverage < 100 else "")
        + (f" · {stats['coast_rejected']} écho(s) sur la côte écarté(s)" if stats.get("coast_rejected") else "")
        + (f" · {stats['artifact_rejected']} artefact(s) radar écarté(s) (ambiguïtés, lobes)" if stats.get("artifact_rejected") else "")
        + (" — scène déjà analysée : cibles connues non dupliquées" if rescan else ""),
        finished_at=_now(),
        dark_vessel_count=dark_vessel_count,
        stac_id=stac_id,
    )
    _record_run("done")
    _stop_heartbeat()
    shared.release(LOCK)


def mark_error(message: str) -> None:
    _update(status="error", message=message, finished_at=_now())
    _record_run("error")
    _stop_heartbeat()
    shared.release(LOCK)


RUNS_KEY = "scan_runs"
MAX_RUNS = 30


def _record_run(status: str) -> None:
    """Keep the last scan runs (duration, outcome) for the technical supervision board. Never raises."""
    try:
        from app.db import db

        state = _read()
        started, finished = state.get("started_at"), state.get("finished_at")
        duration = None
        if started and finished:
            duration = round((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds(), 1)
        stats = state.get("stats") or {}
        run = {"status": status, "scene": state.get("stac_id"), "started_at": started, "finished_at": finished,
               "duration_s": duration, "message": (state.get("message") or "")[:200],
               "dark_vessels": state.get("dark_vessel_count"), "tiles": stats.get("sar_tiles"),
               "coverage_pct": stats.get("sar_coverage_pct"),
               "xview3_scored": stats.get("xview3_scored"), "xview3_error": stats.get("xview3_error")}
        with db() as conn:
            row = conn.execute("SELECT value FROM rule_state WHERE key = ?", (RUNS_KEY,)).fetchone()
            runs = json.loads(row[0]) if row else []
            runs = ([run] + runs)[:MAX_RUNS]
            conn.execute(
                "INSERT INTO rule_state (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (RUNS_KEY, json.dumps(runs), _now()),
            )
    except Exception:  # bookkeeping must never break a scan
        pass
