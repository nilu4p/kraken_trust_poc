"""State shared between processes: Redis when configured, process memory otherwise.

The API, the Celery workers, the scheduler (beat) and the AIS collector are
separate processes in the scalable deployment: the running scan, the health of
data sources, the collector counters, notification throttling and locks must
be visible to all of them. Without ``REDIS_URL`` (single-container deployment,
tests) the same calls work on an in-memory store.

Keys are namespaced ``kraken:``; values are JSON.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

PREFIX = "kraken:"


def redis_url(cfg: Settings) -> str:
    """REDIS_URL with the password from REDIS_PASSWORD_FILE (docker secret) when given."""
    url = cfg.redis_url
    path = cfg.redis_password_file
    if url and path and os.path.isfile(path):
        from urllib.parse import quote, urlsplit, urlunsplit

        password = Path(path).read_text(encoding="utf-8").strip()
        parts = urlsplit(url)
        port = f":{parts.port}" if parts.port else ""
        url = urlunsplit((parts.scheme, f":{quote(password, safe='')}@{parts.hostname}{port}", parts.path, parts.query, ""))
    return url


class MemoryStore:
    """Process-local fallback with the same semantics (expiry included)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: Dict[str, Any] = {}
        self._expiry: Dict[str, float] = {}
        self._hashes: Dict[str, Dict[str, str]] = {}

    def _alive(self, key: str) -> bool:
        deadline = self._expiry.get(key)
        if deadline is not None and deadline < time.monotonic():
            self._values.pop(key, None)
            self._expiry.pop(key, None)
            return False
        return key in self._values

    def get(self, key: str) -> Optional[str]:
        with self._lock:
            return self._values.get(key) if self._alive(key) else None

    def set(self, key: str, value: str, ex: Optional[float] = None, nx: bool = False) -> bool:
        with self._lock:
            if nx and self._alive(key):
                return False
            self._values[key] = value
            if ex:
                self._expiry[key] = time.monotonic() + ex
            else:
                self._expiry.pop(key, None)
            return True

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)
            self._expiry.pop(key, None)
            self._hashes.pop(key, None)

    def incr(self, key: str) -> int:
        with self._lock:
            value = int(self._values.get(key, 0) if self._alive(key) else 0) + 1
            self._values[key] = str(value)
            return value

    def expire(self, key: str, seconds: float) -> None:
        with self._lock:
            if key in self._values:
                self._expiry[key] = time.monotonic() + seconds

    def hset(self, name: str, field: str, value: str) -> None:
        with self._lock:
            self._hashes.setdefault(name, {})[field] = value

    def hget(self, name: str, field: str) -> Optional[str]:
        with self._lock:
            return self._hashes.get(name, {}).get(field)

    def hgetall(self, name: str) -> Dict[str, str]:
        with self._lock:
            return dict(self._hashes.get(name, {}))

    def flush(self) -> None:
        with self._lock:
            self._values.clear()
            self._expiry.clear()
            self._hashes.clear()


_memory = MemoryStore()
_clients: Dict[Any, Any] = {}
_clients_lock = threading.Lock()


def store(settings: Optional[Settings] = None) -> Any:
    """Redis client (one per process) or the in-memory store."""
    cfg = settings or get_settings()
    if not cfg.redis_url:
        return _memory
    key = (os.getpid(), cfg.redis_url)
    with _clients_lock:
        client = _clients.get(key)
        if client is None:
            import redis

            client = redis.Redis.from_url(redis_url(cfg), decode_responses=True, socket_timeout=5,
                                          socket_connect_timeout=5, health_check_interval=30)
            _clients[key] = client
    return client


# --------------------------------------------------------------------------- helpers


def get_json(key: str, default: Any = None, settings: Optional[Settings] = None) -> Any:
    raw = store(settings).get(PREFIX + key)
    return json.loads(raw) if raw else default


def set_json(key: str, value: Any, ttl: Optional[float] = None, settings: Optional[Settings] = None) -> None:
    store(settings).set(PREFIX + key, json.dumps(value, default=str), ex=int(ttl) if ttl else None)


def hset_json(name: str, field: str, value: Any, settings: Optional[Settings] = None) -> None:
    store(settings).hset(PREFIX + name, field, json.dumps(value, default=str))


def hget_json(name: str, field: str, settings: Optional[Settings] = None) -> Any:
    raw = store(settings).hget(PREFIX + name, field)
    return json.loads(raw) if raw else None


def hgetall_json(name: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    return {k: json.loads(v) for k, v in (store(settings).hgetall(PREFIX + name) or {}).items()}


def acquire(key: str, ttl: float, settings: Optional[Settings] = None) -> bool:
    """Cross-process lock (expires after ``ttl`` s even if its holder crashes)."""
    return bool(store(settings).set(PREFIX + "lock:" + key, str(os.getpid()), ex=int(ttl), nx=True))


def refresh(key: str, ttl: float, settings: Optional[Settings] = None) -> None:
    """Extend a held lock (heartbeat of a long job): it lapses ``ttl`` s after its holder dies."""
    store(settings).expire(PREFIX + "lock:" + key, int(ttl))


def release(key: str, settings: Optional[Settings] = None) -> None:
    store(settings).delete(PREFIX + "lock:" + key)


def held(key: str, settings: Optional[Settings] = None) -> bool:
    return store(settings).get(PREFIX + "lock:" + key) is not None


def first_in(key: str, seconds: float, settings: Optional[Settings] = None) -> bool:
    """True for the first caller in a ``seconds`` window (notification anti-repeat)."""
    return bool(store(settings).set(PREFIX + "throttle:" + key, "1", ex=max(1, int(seconds)), nx=True))


def incr(key: str, settings: Optional[Settings] = None) -> int:
    return int(store(settings).incr(PREFIX + key))


def delete(key: str, settings: Optional[Settings] = None) -> None:
    store(settings).delete(PREFIX + key)


def ping(settings: Optional[Settings] = None) -> bool:
    try:
        client = store(settings)
        return True if client is _memory else bool(client.ping())
    except Exception as exc:
        logger.warning("Redis unreachable: %s", type(exc).__name__)
        return False
