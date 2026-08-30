"""Durable review audit independent of graph checkpoint storage."""
from __future__ import annotations

from uuid import uuid4

from app.repositories.database import SessionLocal
from app.repositories.models import ReviewRecord


class ReviewRepository:
    def record(self, task: dict, action: str, reviewer: str, payload: dict | None = None) -> None:
        with SessionLocal.begin() as session:
            session.add(ReviewRecord(
                review_id=f"rev-{uuid4().hex[:16]}", workspace_id=task.get("workspace_id", "demo"),
                task_id=task["task_id"], reviewer=reviewer, action=action, payload=payload or {},
            ))
