"""AIS collector service (``python -m app.collector``): aisstream.io stream + AISHub / VesselAPI pollers.

Runs outside the API in the scalable deployment so that the live AIS picture
does not depend on API restarts or load. Keys and settings saved from the
dashboard are reloaded every minute (a new key applies without restart).
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.config import get_settings
from app.db import init_db

logger = logging.getLogger("kraken.collector")
REFRESH_SECONDS = 60


async def _refresh_settings_forever() -> None:
    from app.tasks import refresh_settings

    while True:
        await asyncio.sleep(REFRESH_SECONDS)
        try:
            await asyncio.to_thread(refresh_settings)
        except Exception as exc:  # keep collecting with the previous keys
            logger.warning("Settings refresh failed: %s", type(exc).__name__)


async def main() -> None:
    from app.sources.aishub import run_poller
    from app.sources.aisstream import run_collector
    from app.sources.vesselapi import run_poller as run_vesselapi_poller
    from app.tasks import refresh_settings

    cfg = get_settings()
    init_db(cfg)
    refresh_settings()
    logger.info("AIS collector service started")
    tasks = [
        asyncio.create_task(run_collector(cfg)),
        asyncio.create_task(run_poller(cfg)),
        asyncio.create_task(run_vesselapi_poller(cfg)),
        asyncio.create_task(_refresh_settings_forever()),
    ]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    logger.info("AIS collector service stopped")


if __name__ == "__main__":
    logging.basicConfig(level=get_settings().log_level.upper(), format="%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    asyncio.run(main())
