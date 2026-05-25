"""Celery application — broker/result backend from Settings (Redis).

Run worker (from `reroute-ai/backend`): `.venv/bin/celery -A worker.celery_app worker -l info`
Run beat (optional): `.venv/bin/celery -A worker.celery_app beat -l info`
Requires Redis (e.g. `docker compose -f ../docker-compose.yml up -d redis`).
"""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from config import get_settings


def _make_celery() -> Celery:
    s = get_settings()
    broker = s.resolved_celery_broker_url
    backend = s.resolved_celery_result_backend
    app = Celery(
        "reroute",
        broker=broker,
        backend=backend,
        include=["worker.tasks"],
    )
    app.conf.update(
        task_default_queue="reroute",
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        beat_schedule={
            "release-stale-applying": {
                "task": "reroute.agent.release_stale_applying",
                "schedule": crontab(minute="*/5"),
            },
            "monitor-cycle": {
                "task": "reroute.monitor.enqueue_cycle",
                "schedule": crontab(minute="*/10"),
            },
        },
    )
    return app


celery_app = _make_celery()
