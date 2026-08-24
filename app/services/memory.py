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

    _SCOPE_KEYS = ("region", "warehouse", "store", "category", "sku", "channel")

    def retrieve_approved_priors(self, workspace_id: str, scope: dict, limit: int = 8) -> dict:
        """Return scoped, non-expired approved memories as historical priors.

        This is intentionally not a RAG vector/BM25 path.  At the current
        project scale, deterministic scope + TTL filtering is more auditable
        than semantic similarity.  The returned ranking explains why a prior
        was recalled and must not be used as current factual evidence.
        """
        now = datetime.now(timezone.utc)
        with SessionLocal() as session:
            records = session.scalars(select(MemoryItemRecord).where(
                MemoryItemRecord.workspace_id == workspace_id,
                MemoryItemRecord.status == "approved",
            ).order_by(MemoryItemRecord.created_at.desc())).all()
        matched, expired_count, scope_mismatch_count = [], 0, 0
        for item in records:
            # MySQL DATETIME can be returned without tzinfo even when the ORM
            # column is declared timezone-aware. Treat such stored values as
            # UTC so cross-task memory recall never fails at the boundary.
            expires_at = item.expires_at
            if expires_at and expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at and expires_at <= now:
                expired_count += 1
                continue
            item_scope = item.scope or {}
            scoped_keys = [key for key in self._SCOPE_KEYS if item_scope.get(key)]
            exact_keys = [key for key in scoped_keys if scope.get(key) == item_scope[key]]
            # A prior scoped to a store/SKU cannot be applied to a task that
            # lacks that field: unknown is not an implicit wildcard.
            if len(exact_keys) != len(scoped_keys):
                scope_mismatch_count += 1
                continue
            dumped = self._dump(item)
            # More exact scope is preferred, then reviewer confidence, then
            # recency. This is a ranking explanation, not a truth confidence.
            scope_score = len(exact_keys) / max(1, len(self._SCOPE_KEYS))
            age_days = max(0.0, (now - (item.created_at.replace(tzinfo=timezone.utc) if item.created_at.tzinfo is None else item.created_at)).total_seconds() / 86400)
            freshness_score = 1 / (1 + age_days / 90)
            prior_score = round(0.55 * scope_score + 0.30 * float(item.confidence) + 0.15 * freshness_score, 4)
            dumped["prior_rank_score"] = prior_score
            dumped["match_reason"] = {
                "exact_scope_keys": exact_keys,
                "scope_score": round(scope_score, 4),
                "reviewed_confidence": float(item.confidence),
                "freshness_score": round(freshness_score, 4),
                "ttl_valid": True,
            }
            matched.append(dumped)
        matched.sort(key=lambda item: (item["prior_rank_score"], item.get("created_at") or ""), reverse=True)
        selected = matched[:limit]
        return {
            "items": selected,
            "retrieval": {
                "strategy": "approved_scope_ttl_top_k",
                "limit": limit,
                "approved_candidates": len(records),
                "scope_ttl_matches": len(matched),
                "expired_excluded": expired_count,
                "scope_mismatch_excluded": scope_mismatch_count,
                "fact_boundary": "Historical priors cannot become current RiskEvent evidence or citations.",
            },
        }

    def list_for_task(self, workspace_id: str, scope: dict, limit: int = 8) -> list[dict]:
        """Compatibility list API; new callers should retain retrieval metadata."""
        return self.retrieve_approved_priors(workspace_id, scope, limit)["items"]

    def get(self, memory_id: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal() as session:
            record = session.get(MemoryItemRecord, memory_id)
            return self._dump(record) if record and record.workspace_id == workspace_id else None

    def approve(self, memory_id: str, reviewer: str, workspace_id: str = "demo") -> dict | None:
        with SessionLocal.begin() as session:
            # A replacement approval changes two records.  Lock the candidate
            # and its previous approved record in this transaction so two
            # reviewers cannot create a split lineage.
            record = session.scalar(select(MemoryItemRecord).where(
                MemoryItemRecord.memory_id == memory_id,
                MemoryItemRecord.workspace_id == workspace_id,
            ).with_for_update())
            if record is None or record.workspace_id != workspace_id or record.status != "candidate":
                return None
            superseded_memory_id = None
            if record.supersedes_id:
                previous = session.scalar(select(MemoryItemRecord).where(
                    MemoryItemRecord.memory_id == record.supersedes_id,
                    MemoryItemRecord.workspace_id == workspace_id,
                ).with_for_update())
                # Do not approve a replacement against an already changed
                # lineage.  The reviewer can create a new proposal from the
                # current approved memory instead.
                if previous is None or previous.status != "approved":
                    return None
                previous.status, previous.reviewed_by = "superseded", reviewer
                superseded_memory_id = previous.memory_id
            record.status, record.reviewed_by = "approved", reviewer
            record.expires_at = datetime.now(timezone.utc) + timedelta(days=get_settings().memory_default_ttl_days)
            session.flush()
            return {**self._dump(record), "superseded_memory_id": superseded_memory_id}

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
            previous = session.scalar(select(MemoryItemRecord).where(
                MemoryItemRecord.memory_id == memory_id,
                MemoryItemRecord.workspace_id == workspace_id,
            ).with_for_update())
            if previous is None or previous.status != "approved":
                return None
            # Creating a replacement is only a proposal.  The old reviewed
            # memory must remain recallable until the replacement is approved;
            # otherwise a rejected candidate would create a memory gap.
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
