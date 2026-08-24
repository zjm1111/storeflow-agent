from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, select, update
from sqlalchemy.orm import Mapped, mapped_column

from app.repositories.database import Base, SessionLocal


class TaskRecord(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(String(64), nullable=False, default="demo", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    # Database concurrency version. This is deliberately independent from the
    # Agent checkpoint version, which describes execution position.
    state_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    result: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False
    )

    __table_args__ = (
        # Situational-memory recall filters these columns and orders by update
        # time.  Without a covering index MySQL may sort large JSON snapshots,
        # which is both slow and vulnerable to a small sort buffer.
        Index("ix_tasks_workspace_status_updated_at", "workspace_id", "status", "updated_at"),
    )


class StateConflictError(RuntimeError):
    """A stale task snapshot attempted to overwrite a newer state."""


class TaskRepository:
    """MySQL-backed task repository for the Week-2 task API."""

    def save(self, task_id: str, state: dict) -> dict:
        """Persist a complete state snapshot using optimistic compare-and-swap.

        ``checkpoint.version`` tracks Agent progress. ``state_version`` tracks
        concurrent database writes and is incremented for every successful
        snapshot write. A stale worker must reload rather than overwrite a
        reviewer or newer worker update.
        """
        with SessionLocal.begin() as session:
            task = session.get(TaskRecord, task_id)
            if task is None:
                state["state_version"] = 1
                session.add(TaskRecord(
                    task_id=task_id, workspace_id=state.get("workspace_id", "demo"),
                    idempotency_key=state.get("idempotency_key"), question=state["question"],
                    status=state["status"], state_version=1, result=state,
                ))
            else:
                expected = int(state.get("state_version", task.state_version))
                next_version = expected + 1
                snapshot = {**state, "state_version": next_version}
                result = session.execute(
                    update(TaskRecord)
                    .where(
                        TaskRecord.task_id == task_id,
                        TaskRecord.workspace_id == state.get("workspace_id", "demo"),
                        TaskRecord.state_version == expected,
                    )
                    .values(
                        status=snapshot["status"],
                        result=snapshot,
                        state_version=next_version,
                        updated_at=datetime.now(timezone.utc),
                    )
                )
                if result.rowcount != 1:
                    raise StateConflictError(f"task {task_id} state version conflict: expected {expected}")
                state["state_version"] = next_version
        return state

    def get(self, task_id: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal() as session:
            record = session.scalar(select(TaskRecord).where(TaskRecord.task_id == task_id, TaskRecord.workspace_id == workspace_id))
            if record is None:
                return None
            snapshot = dict(record.result or {})
            snapshot["state_version"] = record.state_version
            return snapshot

    def find_idempotent(self, key: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal() as session:
            record = session.scalar(select(TaskRecord).where(TaskRecord.workspace_id == workspace_id, TaskRecord.idempotency_key == key).order_by(TaskRecord.created_at.desc()))
            if record is None:
                return None
            snapshot = dict(record.result or {})
            snapshot["state_version"] = record.state_version
            return snapshot

    def list_situational_memories(self, workspace_id: str, scope: dict, *, exclude_task_id: str | None = None, limit: int = 5) -> list[dict]:
        """Completed task snapshots are read-only situational memory, not rules."""
        with SessionLocal() as session:
            # Sort only compact primary keys through the composite index, then
            # fetch JSON snapshots by primary key.  This prevents MySQL from
            # placing large ``result`` payloads in its sort buffer.
            task_ids = session.scalars(
                select(TaskRecord.task_id)
                .where(TaskRecord.workspace_id == workspace_id, TaskRecord.status.in_(("completed", "approved")))
                .order_by(TaskRecord.updated_at.desc())
                .limit(30)
            ).all()
            records = [record for task_id in task_ids if (record := session.get(TaskRecord, task_id)) is not None]
        matches = []
        for record in records:
            if record.task_id == exclude_task_id:
                continue
            payload, prior_scope = record.result or {}, (record.result or {}).get("scope", {})
            keys = ("region", "warehouse", "store", "category", "sku")
            if all(not prior_scope.get(key) or not scope.get(key) or prior_scope.get(key) == scope.get(key) for key in keys):
                matches.append({"task_id": record.task_id, "question": record.question, "events": payload.get("events", []), "decision": payload.get("decision"), "review": payload.get("human_review"), "scope": prior_scope, "created_at": record.created_at.isoformat()})
        return matches[:limit]
