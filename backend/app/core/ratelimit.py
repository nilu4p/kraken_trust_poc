"""Rate limiter (shared through Redis across API processes) and trusted-proxy aware client IP.

With several API processes, an in-process counter would multiply the allowed
login attempts by the number of processes: when Redis is configured the
counters are shared (fixed one-minute windows); otherwise an in-process
sliding window is used.

The client IP comes from ``X-Forwarded-For`` only when the direct peer is a
trusted reverse proxy (nginx container); otherwise the socket address is used,
so a caller cannot spoof the header to dodge limits or poison audit logs.
"""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Iterable, Optional

from app.config import get_settings


def _in_networks(ip: str, networks: Iterable[str]) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for net in networks:
        try:
            if address in ipaddress.ip_network(net, strict=False):
                return True
        except ValueError:
            continue
    return False


def client_ip(peer: Optional[str], forwarded_for: Optional[str]) -> str:
    """Address of the client as seen by the first trusted proxy.

    ``X-Forwarded-For`` is read from the right: each trusted proxy appends the address it
    received the request from, so the right-most hop that is *not* a trusted proxy is the
    real client. The left-most entries are whatever the client chose to send (spoofable).
    """
    peer = (peer or "").strip()
    trusted = get_settings().trusted_proxies
    if forwarded_for and _in_networks(peer, trusted):
        hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
        for hop in reversed(hops):
            try:
                ipaddress.ip_address(hop)
            except ValueError:
                break  # malformed hop: nothing to its left can be trusted either
            if not _in_networks(hop, trusted):
                return hop
        else:
            if hops:
                return hops[0]  # every hop is a trusted proxy: the origin is the first one
    return peer or "unknown"


class SlidingWindowLimiter:
    """At most ``limit`` hits per ``window`` seconds per key."""

    def __init__(self, limit: int, window: float = 60.0, name: str = "api") -> None:
        self.limit = limit
        self.window = window
        self.name = name
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """Record a hit; False when the key is over its limit."""
        if get_settings().redis_url:
            try:
                return self._shared_hit(key)
            except Exception:  # Redis down: fall back to the local window rather than refusing everyone
                pass
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 50_000:  # bound memory under address spraying
                self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
            return True

    def _shared_hit(self, key: str) -> bool:
        from app.core import shared

        window = int(time.time() // self.window)
        counter = f"{shared.PREFIX}rl:{self.name}:{key}:{window}"
        client = shared.store()
        if hasattr(client, "pipeline"):
            # INCR + EXPIRE in one round trip: a crash between the two can no longer leave
            # a counter without expiry (that client would then stay blocked for good).
            pipe = client.pipeline()
            pipe.incr(counter)
            pipe.expire(counter, int(self.window) + 5)
            count = pipe.execute()[0]
        else:
            count = client.incr(counter)
            if count == 1:
                client.expire(counter, int(self.window) + 5)
        return count <= self.limit

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


api_limiter = SlidingWindowLimiter(get_settings().rate_limit_per_minute, name="api")
login_limiter = SlidingWindowLimiter(10, name="login")
