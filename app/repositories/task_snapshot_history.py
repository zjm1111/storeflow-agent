"""Append-oriented business snapshot history and execution audit.

This repository is intentionally *not* a LangGraph recovery mechanism. Native
LangGraph MySQL checkpoints own graph durable execution. These records retain
business snapshots, action phases and review history for support, UI audit and
legacy-task investigation.
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import desc, select

from app.repositories.database import SessionLocal
from app.repositories.models import CheckpointRecord, ReviewRecord


class TaskSnapshotHistoryRepository:
    """Store inspectable task-state history without participating in routing."""

    def record_snapshot(self, state: dict) -> None:
        """Persist the newest business projection for an action audit version.

        The underlying ``checkpoints`` table keeps its legacy physical name so
        existing demo data remains readable. Its records are task snapshots,
        not LangGraph checkpointer rows.
        """
        action_phase = state.get("checkpoint", {})
        task_id, workspace_id = state["task_id"], state.get("workspace_id", "demo")
        version = int(action_phase.get("version", 0))
        with SessionLocal.begin() as session:
            existing = session.scalar(select(CheckpointRecord).where(
                CheckpointRecord.task_id == task_id, CheckpointRecord.workspace_id == workspace_id,
                CheckpointRecord.version == version,
            ))
            if existing is None:
                session.add(CheckpointRecord(
                    checkpoint_id=f"snap-{uuid4().hex[:16]}", workspace_id=workspace_id, task_id=task_id,
                    version=version, node=str(action_phase.get("node", "unknown")), payload=state,
                ))
            elif int((existing.payload or {}).get("state_version", -1)) < int(state.get("state_version", -1)):
                # A review or terminal-state projection can change business
                # state without changing an action phase. Keep the latest
                # projection for audit; never use it to restart the graph.
                existing.node = str(action_phase.get("node", existing.node))
                existing.payload = state

    def latest_snapshot(self, task_id: str, workspace_id: str = "demo") -> dict | None:
        """Return a diagnostic snapshot only; callers must not route from it."""
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
