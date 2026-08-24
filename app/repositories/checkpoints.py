"""Durable task checkpoints used to recover a worker after interruption."""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import desc, select

from app.repositories.database import SessionLocal
from app.repositories.models import CheckpointRecord, ReviewRecord


class CheckpointRepository:
    def save(self, state: dict) -> None:
        checkpoint = state.get("checkpoint", {})
        task_id, workspace_id = state["task_id"], state.get("workspace_id", "demo")
        version = int(checkpoint.get("version", 0))
        with SessionLocal.begin() as session:
            existing = session.scalar(select(CheckpointRecord).where(
                CheckpointRecord.task_id == task_id, CheckpointRecord.workspace_id == workspace_id,
                CheckpointRecord.version == version,
            ))
            if existing is None:
                session.add(CheckpointRecord(
                    checkpoint_id=f"cp-{uuid4().hex[:16]}", workspace_id=workspace_id, task_id=task_id,
                    version=version, node=str(checkpoint.get("node", "unknown")), payload=state,
                ))
            elif int((existing.payload or {}).get("state_version", -1)) < int(state.get("state_version", -1)):
                # Review transitions may update durable business state without
                # advancing the graph-node checkpoint. Preserve the newest
                # snapshot for crash inspection, never regress it.
                existing.node = str(checkpoint.get("node", existing.node))
                existing.payload = state

    def latest(self, task_id: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal() as session:
            record = session.scalar(select(CheckpointRecord).where(
                CheckpointRecord.task_id == task_id, CheckpointRecord.workspace_id == workspace_id,
            ).order_by(desc(CheckpointRecord.version)))
            return record.payload if record else None

    def record_review(self, task: dict, action: str, reviewer: str, payload: dict | None = None) -> None:
        with SessionLocal.begin() as session:
            session.add(ReviewRecord(
                review_id=f"rev-{uuid4().hex[:16]}", workspace_id=task.get("workspace_id", "demo"),
                task_id=task["task_id"], reviewer=reviewer, action=action, payload=payload or {},
            ))
