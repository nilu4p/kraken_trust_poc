"""Live health of every external source (last success / failure, consecutive errors).

Shared between processes (API, workers, collector) through :mod:`app.core.shared`:
the dashboard shows the health seen by whichever process called the source.
"""

from __future__ import annotations

import datetime
import threading
from typing import Any, Dict

from app.core import shared

HASH = "health"
_lock = threading.Lock()


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def record(source: str, ok: bool, message: str = "") -> None:
    """Record the outcome of a call to ``source`` (message must never contain secrets). Never raises."""
    try:
        with _lock:
            entry = shared.hget_json(HASH, source) or {
                "last_success": None, "last_failure": None, "last_error": "", "consecutive_failures": 0,
            }
            if ok:
                entry["last_success"] = _now()
                entry["consecutive_failures"] = 0
            else:
                entry["last_failure"] = _now()
                entry["last_error"] = message[:200]
                entry["consecutive_failures"] = int(entry.get("consecutive_failures") or 0) + 1
            shared.hset_json(HASH, source, entry)
    except Exception:  # health bookkeeping must never break a data call (Redis down…)
        pass


def snapshot() -> Dict[str, Dict[str, Any]]:
    try:
        entries = shared.hgetall_json(HASH)
    except Exception:
        return {}
    result = {}
    for name, entry in entries.items():
        failures = int(entry.get("consecutive_failures") or 0)
        result[name] = {**entry, "status": "ok" if failures == 0 else ("degraded" if failures < 3 else "down")}
    return result
