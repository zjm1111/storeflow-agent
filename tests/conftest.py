"""Deterministic test runtime shared by API, worker and service tests.

Environment variables are set before any test module imports the application.
This prevents collection order from accidentally binding SQLAlchemy to a local
MySQL instance or sending test work to real external providers.
"""
import os

os.environ.update({
    "ENVIRONMENT": "test",
    "MYSQL_URL": "sqlite://",
    "REDIS_URL": "redis://localhost:6399/0",
    "QDRANT_URL": "http://127.0.0.1:1",
    "CELERY_BROKER_URL": "memory://",
    "CELERY_RESULT_BACKEND": "cache+memory://",
    "BAILIAN_API_KEY": "",
    "BAILIAN_BASE_URL": "",
    "BAILIAN_MODEL": "qwen3.7-plus",
    "TAVILY_API_KEY": "",
    "RERANK_PROVIDER": "local",
    "JWT_SECRET": "",
    "API_KEY": "",
})

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime():
    """Give every test a fresh task/memory database and eager worker."""
    from app.core.config import get_settings
    from app.repositories.database import Base, engine
    from app.worker import celery_app

    get_settings.cache_clear()
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    if celery_app is not None:
        previous_eager = celery_app.conf.task_always_eager
        previous_propagates = celery_app.conf.task_eager_propagates
        celery_app.conf.update(task_always_eager=True, task_eager_propagates=True)
    else:
        previous_eager = previous_propagates = None
    yield
    if celery_app is not None:
        celery_app.conf.update(task_always_eager=previous_eager, task_eager_propagates=previous_propagates)
    get_settings.cache_clear()
