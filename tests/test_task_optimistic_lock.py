from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.agent.state import initial_state
from app.repositories import StateConflictError, TaskRepository
from app.repositories.tasks import TaskRecord
import app.repositories.tasks as task_repository_module


def test_task_snapshot_compare_and_swap_blocks_stale_writer(monkeypatch):
    """The CAS behavior is independent of the process-global application DB."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TaskRecord.__table__.create(bind=engine)
    monkeypatch.setattr(task_repository_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    repository = TaskRepository()
    task_id = str(uuid4())
    created = initial_state(task_id, "测试乐观锁的门店补货任务")
    created["status"] = "queued"
    repository.save(task_id, created)
    assert created["state_version"] == 1

    stale = deepcopy(repository.get(task_id))
    current = repository.get(task_id)
    current["status"] = "running"
    repository.save(task_id, current)
    assert current["state_version"] == 2

    stale["status"] = "completed"
    with pytest.raises(StateConflictError):
        repository.save(task_id, stale)

    latest = repository.get(task_id)
    assert latest["status"] == "running"
    assert latest["state_version"] == 2
