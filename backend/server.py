"""FastAPI application entrypoint for KRAKEN-Trust."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator, Awaitable, Callable, Dict

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app import __version__
from app.api.deps import request_ip
from app.api.router import api_router
from app.config import get_settings
from app.core.credentials import apply_stored_credentials
from app.core.dbtransfer import migrate_sqlite_if_needed
from app.core.identity import bootstrap_admin
from app.core.pki import ensure_pki
from app.core.ratelimit import api_limiter
from app.db import init_db, is_postgres
from app.soc import runtime, supervision
from app.soc.scheduler import surveillance_loop
from app.sources.aishub import run_poller as run_aishub_poller
from app.sources.aisstream import run_collector
from app.sources.vesselapi import run_poller as run_vesselapi_poller

settings = get_settings()
logging.Formatter.converter = time.gmtime  # logs in UTC, like every timestamp of the platform
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
logger = logging.getLogger("kraken")
PRODUCTION = settings.environment == "production"
# Not counted against the per-client API budget: authenticated bursts (dozens of map tiles per
# pan / zoom, one docs session probe per page asset), cached or throttled on their own.
UNMETERED = re.compile(
    rf"^{re.escape(settings.api_prefix)}/(custom-sources/[a-z0-9_-]+/tiles/\d+/\d+/\d+"
    rf"|imagery/sentinel2/tiles/[A-Za-z0-9_]+/\d+/\d+/\d+\.png|auth/check)$"
)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "accelerometer=(), autoplay=(), camera=(), display-capture=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), midi=(), payment=(), serial=(), usb=(), xr-spatial-tracking=()",
    "X-Permitted-Cross-Domain-Policies": "none",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    # API responses are JSON / images: nothing may execute from them.
    "Content-Security-Policy": "default-src 'none'; img-src 'self'; frame-ancestors 'none'",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    for directory in (settings.preuves_dir, settings.keys_dir, settings.data_dir, settings.cache_dir):
        directory.mkdir(parents=True, exist_ok=True)
    init_db(settings)
    # First start on PostgreSQL: bring the previous SQLite data along (once).
    migrate_sqlite_if_needed(settings)
    ensure_pki(settings)
    apply_stored_credentials(settings)
    runtime.apply_stored(settings)
    bootstrap_admin(settings)
    tasks = []
    if settings.background_mode != "workers":
        # Single-container mode: the API also runs the 24/7 loop and the AIS collectors.
        # In "workers" mode, Celery workers / beat and the collector service do it.
        tasks = [
            asyncio.create_task(surveillance_loop(settings)),
            # Continuous AIS picture (idle until AISSTREAM_API_KEY is configured).
            asyncio.create_task(run_collector(settings)),
            # Second AIS feed fused in the same history (idle until AISHUB_USERNAME is configured).
            asyncio.create_task(run_aishub_poller(settings)),
            # Optional third feed (idle until VESSELAPI_API_KEY and vesselapi_poll_minutes are set).
            asyncio.create_task(run_vesselapi_poller(settings)),
        ]
    if PRODUCTION and not settings.cookie_secure:
        logger.warning("ENVIRONMENT=production with COOKIE_SECURE=false: session cookies travel in clear text — "
                       "serve the console over HTTPS and set COOKIE_SECURE=true before exposing it beyond this host")
    logger.info("KRAKEN-Trust API ready (env=%s, database=%s, background=%s)", settings.environment,
                "postgresql" if is_postgres(settings) else "sqlite", settings.background_mode)
    yield
    for task in tasks:
        task.cancel()
    for task in tasks:
        with contextlib.suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="KRAKEN-Trust API",
    version=__version__,
    lifespan=lifespan,
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None,
    openapi_url=None if PRODUCTION else "/openapi.json",
)


@app.middleware("http")
async def harden(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Per-client rate limit + security headers on every response."""
    limited = request.url.path.startswith(settings.api_prefix) and not UNMETERED.match(request.url.path)
    started = time.monotonic()
    if limited and not api_limiter.hit(request_ip(request)):
        response: Response = JSONResponse({"detail": "Trop de requêtes"}, status_code=429, headers={"Retry-After": "60"})
    else:
        response = await call_next(request)
    area = supervision.area_of(request.url.path, settings.api_prefix)
    if area:  # response times per API area, for the technical supervision board
        supervision.record_timing(area, time.monotonic() - started, response.status_code)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.url.path.startswith(settings.api_prefix):
        response.headers.setdefault("Cache-Control", "no-store")
    if settings.cookie_secure:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in settings.cors_origins if o != "*"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)
app.include_router(api_router)


@app.get("/health")
def health() -> Dict[str, str]:
    """Liveness probe (no sensitive information)."""
    return {"status": "ok", "service": settings.app_name}


@app.get("/health/ready")
def ready() -> JSONResponse:
    """Readiness: database and shared state reachable (503 otherwise). No sensitive information."""
    from app.core import shared
    from app.db import read_db

    checks = {"database": False, "shared_state": shared.ping(settings)}
    try:
        with read_db(settings) as conn:
            conn.execute("SELECT 1").fetchone()
        checks["database"] = True
    except Exception:
        pass
    ok = all(checks.values())
    return JSONResponse({"status": "ok" if ok else "degraded", **checks}, status_code=200 if ok else 503)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=not PRODUCTION)
