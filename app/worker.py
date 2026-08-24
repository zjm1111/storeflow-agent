"""Celery worker entrypoint. The API can still use inline execution in test mode."""
from __future__ import annotations

def execute_task(task_id: str, checkpoint_version: int | None = None, workspace_id: str = "demo", state_version: int | None = None) -> bool:
    """Run one durable task only if the queued snapshot is still current.

    Kept outside the optional Celery wrapper so recovery tests can exercise
    the exact worker guard without needing a broker or Celery installation.
    """
    from app.services.research import ResearchService

    service = ResearchService()
    task = service.get(task_id, workspace_id)
    if task is None:
        return False
    if checkpoint_version is not None and task.get("checkpoint", {}).get("version", 0) != checkpoint_version:
        return False
    if state_version is not None and task.get("state_version") != state_version:
        return False
    service.run(task_id, workspace_id)
    return True


try:
    from celery import Celery
except ImportError:  # local no-dependency test mode
    celery_app = None
    run_task = None
else:
    from app.core import get_settings
    settings = get_settings()
    celery_app = Celery("supplymind", broker=settings.celery_broker_url, backend=settings.celery_result_backend)
    celery_app.conf.update(task_acks_late=True, task_reject_on_worker_lost=True, task_track_started=True)

    @celery_app.task(name="supplymind.run_task", bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 2})
    def run_task(self, task_id: str, checkpoint_version: int | None = None, workspace_id: str = "demo", state_version: int | None = None) -> bool:
        return execute_task(task_id, checkpoint_version, workspace_id, state_version)
