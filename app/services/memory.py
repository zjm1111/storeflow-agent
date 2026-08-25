"""Reviewed, scoped business memory.

Only an explicit reviewer action can turn a candidate into cross-task memory.
Task working memory lives in the task snapshot and is never stored here.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.core import get_settings
from app.repositories.database import SessionLocal
from app.repositories.models import MemoryItemRecord
from app.services.context import estimate_tokens, truncate_to_token_budget


class MemoryService:
    _KIND_TTLS = {
        "episodic": "memory_episodic_ttl_days",
        "semantic": "memory_semantic_ttl_days",
        "procedural": "memory_procedural_ttl_days",
        # Kept only for records created before the explicit kind lifecycle.
        "risk_pattern": "memory_default_ttl_days",
    }

    def create_candidate(
        self, *, workspace_id: str, content: str, evidence_ids: list[str], scope: dict,
        confidence: float, kind: str = "episodic", human_initiated: bool = False,
        origin_task_id: str | None = None,
    ) -> dict:
        self._validate_candidate_kind(kind, human_initiated=human_initiated)
        with SessionLocal.begin() as session:
            relations = self._candidate_relations(session, workspace_id, content, scope, kind)
            record = MemoryItemRecord(
                memory_id=f"mem-{uuid4().hex[:12]}", workspace_id=workspace_id, content=content[:4000],
                summary=self._summary(content), evidence_ids=evidence_ids, scope=scope,
                confidence=max(0.0, min(1.0, confidence)), status="candidate", kind=kind,
                origin_task_id=origin_task_id, content_hash=self._content_hash(content), revision=1,
                possible_duplicate_of=relations["possible_duplicate_of"], conflicts_with=relations["conflicts_with"],
            )
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
                MemoryItemRecord.reviewed_by, MemoryItemRecord.review_action, MemoryItemRecord.review_comment,
                MemoryItemRecord.origin_task_id, MemoryItemRecord.reviewed_at,
                MemoryItemRecord.content_hash, MemoryItemRecord.revision, MemoryItemRecord.possible_duplicate_of,
                MemoryItemRecord.conflicts_with, MemoryItemRecord.expires_at,
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
                content = truncate_to_token_budget(full_content, remaining)
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

    def approve(self, memory_id: str, reviewer: str, workspace_id: str = "demo", comment: str | None = None) -> dict | None:
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
                previous.status, previous.reviewed_by, previous.reviewed_at = "superseded", reviewer, datetime.now(timezone.utc)
                previous.review_action, previous.review_comment = "superseded", f"replaced_by={record.memory_id}"
                superseded_memory_id = previous.memory_id
            reviewed_at = datetime.now(timezone.utc)
            record.status, record.reviewed_by, record.reviewed_at = "approved", reviewer, reviewed_at
            record.review_action, record.review_comment = "approve", comment
            record.expires_at = reviewed_at + timedelta(days=self._ttl_days(record.kind))
            session.flush()
            return {**self._dump(record), "superseded_memory_id": superseded_memory_id}

    def reject(self, memory_id: str, reviewer: str, comment: str, workspace_id: str = "demo") -> dict | None:
        """Fail closed: rejected candidates never become cross-task priors."""
        with SessionLocal.begin() as session:
            record = session.scalar(select(MemoryItemRecord).where(
                MemoryItemRecord.memory_id == memory_id,
                MemoryItemRecord.workspace_id == workspace_id,
            ).with_for_update())
            if record is None or record.status != "candidate":
                return None
            reviewed_at = datetime.now(timezone.utc)
            record.status, record.reviewed_by, record.reviewed_at = "rejected", reviewer, reviewed_at
            record.review_action, record.review_comment = "reject", comment.strip()
            session.flush()
            return self._dump(record)

    def expire(self, memory_id: str, reviewer: str, workspace_id: str = "demo", comment: str | None = None) -> dict | None:
        with SessionLocal.begin() as session:
            record = session.get(MemoryItemRecord, memory_id)
            if record is None or record.workspace_id != workspace_id:
                return None
            reviewed_at = datetime.now(timezone.utc)
            record.status, record.reviewed_by, record.reviewed_at, record.expires_at = "expired", reviewer, reviewed_at, reviewed_at
            record.review_action, record.review_comment = "expire", comment
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
            relations = self._candidate_relations(
                session, workspace_id, replacement_content, previous.scope or {}, previous.kind,
                exclude_memory_ids={previous.memory_id},
            )
            replacement = MemoryItemRecord(
                memory_id=f"mem-{uuid4().hex[:12]}", workspace_id=workspace_id, status="candidate",
                kind=previous.kind, content=replacement_content[:4000], evidence_ids=previous.evidence_ids,
                summary=self._summary(replacement_content), scope=previous.scope,
                confidence=previous.confidence, supersedes_id=previous.memory_id,
                origin_task_id=previous.origin_task_id, content_hash=self._content_hash(replacement_content),
                revision=(previous.revision or 1) + 1,
                possible_duplicate_of=relations["possible_duplicate_of"], conflicts_with=relations["conflicts_with"],
            )
            session.add(replacement); session.flush()
            return self._dump(previous), self._dump(replacement)

    @staticmethod
    def _dump(record: MemoryItemRecord) -> dict:
        return {"memory_id": record.memory_id, "workspace_id": record.workspace_id, "status": record.status,
                "kind": record.kind, "summary": record.summary or MemoryService._legacy_summary(record.memory_id),
                "content": record.content, "evidence_ids": record.evidence_ids or [],
                "scope": record.scope or {}, "confidence": record.confidence, "reviewed_by": record.reviewed_by,
                "review_action": record.review_action, "review_comment": record.review_comment,
                "origin_task_id": record.origin_task_id,
                "reviewed_at": record.reviewed_at.isoformat() if record.reviewed_at else None,
                "content_hash": record.content_hash or MemoryService._content_hash(record.content),
                "revision": record.revision or 1, "possible_duplicate_of": record.possible_duplicate_of,
                "conflicts_with": record.conflicts_with or [],
                "expires_at": record.expires_at.isoformat() if record.expires_at else None,
                "supersedes_id": record.supersedes_id, "created_at": record.created_at.isoformat() if record.created_at else None}

    @staticmethod
    def _summary(content: str, max_chars: int = 240) -> str:
        """Deterministic catalog text; it never invents a business claim."""
        normalized = " ".join(content.split())
        return normalized[:max_chars] + ("…" if len(normalized) > max_chars else "")

    @staticmethod
    def _normalized_content(content: str) -> str:
        return " ".join(content.lower().split())

    @classmethod
    def _content_hash(cls, content: str) -> str:
        return hashlib.sha256(cls._normalized_content(content).encode("utf-8")).hexdigest()

    @staticmethod
    def _token_set(content: str) -> set[str]:
        # Chinese characters and alphanumeric terms both become stable, local
        # reviewer hints. This is deliberately not an LLM-based auto-merge.
        return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", content.lower()))

    @classmethod
    def _candidate_relations(
        cls, session, workspace_id: str, content: str, scope: dict, kind: str,
        *, exclude_memory_ids: set[str] | None = None,
    ) -> dict:
        """Return conservative reviewer hints; never mutate another memory.

        Exact hash or high lexical overlap is a possible duplicate. A conflict
        needs an explicit opposite operational claim, avoiding speculative
        semantic conflict detection by an LLM.
        """
        excluded = exclude_memory_ids or set()
        candidates = session.scalars(select(MemoryItemRecord).where(
            MemoryItemRecord.workspace_id == workspace_id,
            MemoryItemRecord.kind == kind,
            MemoryItemRecord.status.in_(("candidate", "approved")),
        ).order_by(MemoryItemRecord.created_at.desc())).all()
        incoming_hash, incoming_tokens = cls._content_hash(content), cls._token_set(content)
        duplicates, conflicts = [], []
        for item in candidates:
            if item.memory_id in excluded or (item.scope or {}) != (scope or {}):
                continue
            existing_hash = item.content_hash or cls._content_hash(item.content)
            existing_tokens = cls._token_set(item.content)
            union = incoming_tokens | existing_tokens
            similarity = len(incoming_tokens & existing_tokens) / len(union) if union else 0.0
            if existing_hash == incoming_hash or similarity >= 0.86:
                duplicates.append(item.memory_id)
            if cls._has_explicit_conflict(content, item.content):
                conflicts.append(item.memory_id)
        # Approved records are more important reviewer references than pending
        # candidates; SQL order already makes the result deterministic enough.
        return {"possible_duplicate_of": duplicates[0] if duplicates else None, "conflicts_with": sorted(set(conflicts))}

    @classmethod
    def _has_explicit_conflict(cls, incoming: str, existing: str) -> bool:
        left, right = cls._normalized_content(incoming), cls._normalized_content(existing)
        opposite_pairs = (("增加", "减少"), ("提前", "延后"), ("正常配送", "配送延迟"), ("必须", "无需"))
        if any((a in left and b in right) or (b in left and a in right) for a, b in opposite_pairs):
            return True
        # Same timing policy with a different stated number is a transparent,
        # conservative conflict hint (for example "提前 1 天" vs "提前 2 天").
        pattern = re.compile(r"(提前|延后|延迟|安全库存)[^0-9一二三四五六七八九十]{0,8}([0-9一二三四五六七八九十]+)\s*天")
        left_claims = {(name, value) for name, value in pattern.findall(left)}
        right_claims = {(name, value) for name, value in pattern.findall(right)}
        return any(name == other_name and value != other_value for name, value in left_claims for other_name, other_value in right_claims)

    @staticmethod
    def _legacy_summary(memory_id: str) -> str:
        return f"已批准历史业务记忆（{memory_id}；待下次修订生成摘要）"

    @staticmethod
    def _token_estimate(content: str) -> int:
        """Compatibility method delegating to the shared context estimator."""
        return estimate_tokens(content)

    @classmethod
    def _validate_candidate_kind(cls, kind: str, *, human_initiated: bool) -> None:
        if kind not in cls._KIND_TTLS:
            raise ValueError(f"unsupported memory kind: {kind}")
        # An Agent can summarize a reviewed task as an episodic case, but it
        # must never promote one task into a durable business fact or policy.
        if not human_initiated and kind != "episodic":
            raise ValueError("Agent-created candidates must use kind=episodic")

    @classmethod
    def _ttl_days(cls, kind: str) -> int:
        settings = get_settings()
        attribute = cls._KIND_TTLS.get(kind, "memory_default_ttl_days")
        fallback = getattr(settings, "memory_default_ttl_days", 90)
        return max(1, int(getattr(settings, attribute, fallback)))

    @classmethod
    def _catalog_dump(cls, item: dict) -> dict:
        """Serialize a catalog row without touching its long-form content."""
        return {
            "memory_id": item["memory_id"], "workspace_id": item["workspace_id"], "status": item["status"],
            "kind": item["kind"], "summary": item["summary"] or cls._legacy_summary(item["memory_id"]),
            "evidence_ids": item["evidence_ids"] or [], "scope": item["scope"] or {},
            "confidence": float(item["confidence"]), "reviewed_by": item["reviewed_by"],
            "review_action": item["review_action"], "review_comment": item["review_comment"],
            "origin_task_id": item["origin_task_id"],
            "reviewed_at": item["reviewed_at"].isoformat() if item["reviewed_at"] else None,
            "content_hash": item["content_hash"], "revision": item["revision"] or 1,
            "possible_duplicate_of": item["possible_duplicate_of"], "conflicts_with": item["conflicts_with"] or [],
            "expires_at": item["expires_at"].isoformat() if item["expires_at"] else None,
            "supersedes_id": item["supersedes_id"],
            "created_at": item["created_at"].isoformat() if item["created_at"] else None,
        }
