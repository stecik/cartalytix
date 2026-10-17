from uuid import UUID

from celery import Celery
from sqlalchemy import create_engine

from cartalytix.settings import Settings
from cartalytix.sync import publish_pending, run_sync, schedule_due

settings = Settings()
celery_app = Celery("cartalytix", broker=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=1740,
    task_time_limit=1800,
    beat_schedule={"dispatch-due": {"task": "cartalytix.dispatch_due", "schedule": 60.0}},
)


def _send_run(identifier: str) -> None:
    sync_run.delay(identifier)


@celery_app.task(name="cartalytix.dispatch_due")
def dispatch_due() -> dict[str, str | int]:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        scheduled = schedule_due(engine)
        publish_pending(engine, _send_run)
        return {"scheduled": scheduled, "status": "dispatched"}
    finally:
        engine.dispose()


@celery_app.task(name="cartalytix.sync_run")
def sync_run(run_id: str) -> None:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        run_sync(engine, UUID(run_id), settings)
    finally:
        engine.dispose()
