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
            summary=self._summary(content), evidence_ids=evidence_ids, scope=scope,
            confidence=max(0.0, min(1.0, confidence)), status="candidate",
        )
        with SessionLocal.begin() as session:
            session.add(record)
        return self._dump(record)

    _SCOPE_KEYS = ("region", "warehouse", "store", "category", "sku", "channel")

    def retrieve_approved_priors(self, workspace_id: str, scope: dict, limit: int | None = None) -> dict:
        """Select a scoped catalog first, then load bounded prior content.

        This is intentionally not a RAG vector/BM25 path.  At the current
        project scale, deterministic scope + TTL filtering is more auditable
        than semantic similarity.  Stage one reads lightweight catalog fields
        only; stage two loads the selected bodies under a separate memory
        budget.  Returned priors are never current factual evidence.
        """
        now = datetime.now(timezone.utc)
        settings = get_settings()
        requested_limit = limit if limit is not None else settings.memory_catalog_limit
        catalog_limit = max(1, min(int(requested_limit), settings.memory_catalog_limit))
        content_budget = max(1, int(settings.memory_context_token_budget))
        with SessionLocal() as session:
            # Do not fetch ``content`` during catalog selection.  This keeps
            # recall bounded even after many reviewed memories have accrued.
            catalog_rows = session.execute(select(
                MemoryItemRecord.memory_id, MemoryItemRecord.workspace_id,
                MemoryItemRecord.status, MemoryItemRecord.kind, MemoryItemRecord.summary,
                MemoryItemRecord.evidence_ids, MemoryItemRecord.scope, MemoryItemRecord.confidence,
                MemoryItemRecord.reviewed_by, MemoryItemRecord.expires_at,
                MemoryItemRecord.supersedes_id, MemoryItemRecord.created_at,
            ).where(
                MemoryItemRecord.workspace_id == workspace_id,
                MemoryItemRecord.status == "approved",
            ).order_by(MemoryItemRecord.created_at.desc())).mappings().all()
        matched, expired_count, scope_mismatch_count = [], 0, 0
        for item in catalog_rows:
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
            dumped = self._catalog_dump(item)
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
        selected_catalog = matched[:catalog_limit]
        selected_ids = [item["memory_id"] for item in selected_catalog]
        with SessionLocal() as session:
            records = session.scalars(select(MemoryItemRecord).where(
                MemoryItemRecord.workspace_id == workspace_id,
                MemoryItemRecord.memory_id.in_(selected_ids),
            )).all() if selected_ids else []
        by_id = {record.memory_id: record for record in records}
        loaded, used_tokens, budget_excluded = [], 0, 0
        for catalog_item in selected_catalog:
            record = by_id.get(catalog_item["memory_id"])
            if record is None:
                continue
            full_content = record.content
            full_cost = self._token_estimate(full_content)
            remaining = content_budget - used_tokens
            if remaining <= 0:
                budget_excluded += 1
                continue
            truncated = full_cost > remaining
            if truncated:
                # Character slicing is only a budget guard.  The summary is
                # still present, and the full reviewed record remains available
                # to a reviewer through the memory API.
                content = full_content[:max(1, remaining * 4 - 1)].rstrip() + "…"
            else:
                content = full_content
            actual_cost = self._token_estimate(content)
            dumped = {
                **self._dump(record),
                **{key: catalog_item[key] for key in ("prior_rank_score", "match_reason")},
                "content": content,
                "content_loaded": True,
                "content_truncated": truncated,
                "content_token_estimate": actual_cost,
            }
            loaded.append(dumped)
            used_tokens += actual_cost
        return {
            "items": loaded,
            "retrieval": {
                "strategy": "approved_scope_ttl_catalog_then_load",
                "catalog_limit": catalog_limit,
                "approved_candidates": len(catalog_rows),
                "scope_ttl_matches": len(matched),
                "catalog_selected": len(selected_catalog),
                "content_loaded": len(loaded),
                "content_budget_excluded": budget_excluded,
                "content_budget_tokens": content_budget,
                "content_used_tokens": used_tokens,
                "expired_excluded": expired_count,
                "scope_mismatch_excluded": scope_mismatch_count,
                "fact_boundary": "Historical priors cannot become current RiskEvent evidence or citations.",
            },
        }

    def list_for_task(self, workspace_id: str, scope: dict, limit: int = 5) -> list[dict]:
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
                summary=self._summary(replacement_content), scope=previous.scope,
                confidence=previous.confidence, supersedes_id=previous.memory_id,
            )
            session.add(replacement); session.flush()
            return self._dump(previous), self._dump(replacement)

    @staticmethod
    def _dump(record: MemoryItemRecord) -> dict:
        return {"memory_id": record.memory_id, "workspace_id": record.workspace_id, "status": record.status,
                "kind": record.kind, "summary": record.summary or MemoryService._legacy_summary(record.memory_id),
                "content": record.content, "evidence_ids": record.evidence_ids or [],
                "scope": record.scope or {}, "confidence": record.confidence, "reviewed_by": record.reviewed_by,
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                "supersedes_id": record.supersedes_id, "created_at": record.created_at.isoformat() if record.created_at else None}

    @staticmethod
    def _summary(content: str, max_chars: int = 240) -> str:
        """Deterministic catalog text; it never invents a business claim."""
        normalized = " ".join(content.split())
        return normalized[:max_chars] + ("…" if len(normalized) > max_chars else "")

    @staticmethod
    def _legacy_summary(memory_id: str) -> str:
        return f"已批准历史业务记忆（{memory_id}；待下次修订生成摘要）"

    @staticmethod
    def _token_estimate(content: str) -> int:
        return max(1, (len(content) + 3) // 4)

    @classmethod
    def _catalog_dump(cls, item: dict) -> dict:
        """Serialize a catalog row without touching its long-form content."""
        return {
            "memory_id": item["memory_id"], "workspace_id": item["workspace_id"], "status": item["status"],
            "kind": item["kind"], "summary": item["summary"] or cls._legacy_summary(item["memory_id"]),
            "evidence_ids": item["evidence_ids"] or [], "scope": item["scope"] or {},
            "confidence": float(item["confidence"]), "reviewed_by": item["reviewed_by"],
            "expires_at": item["expires_at"].isoformat() if item["expires_at"] else None,
            "supersedes_id": item["supersedes_id"],
            "created_at": item["created_at"].isoformat() if item["created_at"] else None,
        }
