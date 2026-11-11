"""Celery application: background work outside the API process (scalable deployment).

Queues
------
``scan``     Sentinel-1 analysis — CPU / memory heavy, one at a time (dedicated worker)
``default``  24/7 rules, SLA watch, source health, hourly maintenance, automatic watch
``notify``   webhook deliveries, retried with exponential backoff

Broker: ``CELERY_BROKER_URL`` if set (e.g. ``amqp://`` for RabbitMQ), otherwise
Redis (``REDIS_URL``). No result backend: tasks report through the database,
the shared state and the audit trail.

Start::

    celery -A app.worker worker -Q default,notify   # general worker
    celery -A app.worker worker -Q scan -c 1         # scan worker
    celery -A app.worker beat                        # scheduler
"""

from __future__ import annotations

from celery import Celery

from app.config import get_settings
from app.core.shared import redis_url


def broker_url() -> str:
    cfg = get_settings()
    return cfg.celery_broker_url or redis_url(cfg) or "memory://"


celery_app = Celery("kraken", broker=broker_url(), include=["app.tasks"])
celery_app.conf.update(
    task_default_queue="default",
    task_routes={"kraken.scan": {"queue": "scan"}, "kraken.notify": {"queue": "notify"}},
    task_acks_late=True,               # a task killed with its worker is redelivered
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,      # long scans must not hold other tasks hostage
    task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 4 * 3600},  # > longest scan
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # A tick not started within its period is dropped (expires): no pile-up after an outage.
        "rules": {"task": "kraken.rules", "schedule": 60.0, "options": {"expires": 55}},
        "sla": {"task": "kraken.sla", "schedule": 60.0, "options": {"expires": 55}},
        "sources": {"task": "kraken.sources", "schedule": 60.0, "options": {"expires": 55}},
        "auto-scan": {"task": "kraken.auto_scan_tick", "schedule": 60.0, "options": {"expires": 55}},
        "hourly": {"task": "kraken.hourly", "schedule": 3600.0, "options": {"expires": 3000}},
        "sensors": {"task": "kraken.sensors", "schedule": 3600.0, "options": {"expires": 3000}},
    },
    beat_schedule_filename="/tmp/celerybeat-schedule",
    worker_hijack_root_logger=False,
)
