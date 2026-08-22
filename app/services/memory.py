"""Reviewed, scoped business memory.

Only an explicit reviewer action can turn a candidate into cross-task memory.
Task working memory lives in the task snapshot and is never stored here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.core import get_settings
from app.repositories.database import SessionLocal
from app.repositories.models import MemoryItemRecord


class MemoryService:
    def create_candidate(self, *, workspace_id: str, content: str, evidence_ids: list[str], scope: dict, confidence: float) -> dict:
        record = MemoryItemRecord(
            memory_id=f"mem-{uuid4().hex[:12]}", workspace_id=workspace_id, content=content[:4000],
            evidence_ids=evidence_ids, scope=scope, confidence=max(0.0, min(1.0, confidence)), status="candidate",
        )
        with SessionLocal.begin() as session:
            session.add(record)
        return self._dump(record)

    def list_for_task(self, workspace_id: str, scope: dict, limit: int = 8) -> list[dict]:
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            records = session.scalars(select(MemoryItemRecord).where(
                MemoryItemRecord.workspace_id == workspace_id,
                MemoryItemRecord.status == "approved",
            ).order_by(MemoryItemRecord.created_at.desc())).all()
        matched = []
        for item in records:
            # MySQL DATETIME can be returned without tzinfo even when the ORM
            # column is declared timezone-aware. Treat such stored values as
            # UTC so cross-task memory recall never fails at the boundary.
            expires_at = item.expires_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at and expires_at <= now:
                continue
            item_scope = item.scope or {}
            if all(not item_scope.get(key) or not scope.get(key) or item_scope[key] == scope[key] for key in ("region", "warehouse", "store", "category", "sku", "channel")):
                matched.append(self._dump(item))
        return matched[:limit]

    def get(self, memory_id: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal() as session:
            record = session.get(MemoryItemRecord, memory_id)
            return self._dump(record) if record and record.workspace_id == workspace_id else None

    def approve(self, memory_id: str, reviewer: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal.begin() as session:
            record = session.get(MemoryItemRecord, memory_id)
            if record is None or record.workspace_id != workspace_id or record.status != "candidate":
                return None
            record.status, record.reviewed_by = "approved", reviewer
            record.expires_at = datetime.now(timezone.utc) + timedelta(days=get_settings().memory_default_ttl_days)
            session.flush()
            return self._dump(record)

    def expire(self, memory_id: str, reviewer: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal.begin() as session:
            record = session.get(MemoryItemRecord, memory_id)
            if record is None or record.workspace_id != workspace_id:
                return None
            record.status, record.reviewed_by, record.expires_at = "expired", reviewer, datetime.now(timezone.utc)
            session.flush()
            return self._dump(record)

    def supersede(self, memory_id: str, replacement_content: str, reviewer: str, workspace_id: str = "demo") -> tuple[dict, dict] | None:
        with SessionLocal.begin() as session:
            previous = session.get(MemoryItemRecord, memory_id)
            if previous is None or previous.workspace_id != workspace_id:
                return None
            previous.status, previous.reviewed_by = "superseded", reviewer
            replacement = MemoryItemRecord(
                memory_id=f"mem-{uuid4().hex[:12]}", workspace_id=workspace_id, status="candidate",
                kind=previous.kind, content=replacement_content[:4000], evidence_ids=previous.evidence_ids,
                scope=previous.scope, confidence=previous.confidence, supersedes_id=previous.memory_id,
            )
            session.add(replacement); session.flush()
            return self._dump(previous), self._dump(replacement)

    @staticmethod
    def _dump(record: MemoryItemRecord) -> dict:
        return {"memory_id": record.memory_id, "workspace_id": record.workspace_id, "status": record.status,
                "kind": record.kind, "content": record.content, "evidence_ids": record.evidence_ids or [],
                "scope": record.scope or {}, "confidence": record.confidence, "reviewed_by": record.reviewed_by,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                "supersedes_id": record.supersedes_id, "created_at": record.created_at.isoformat() if record.created_at else None}
