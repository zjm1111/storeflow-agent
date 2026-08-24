from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.services.memory as memory_module
from app.repositories.models import MemoryItemRecord
from app.services.memory import MemoryService


def _memory_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    MemoryItemRecord.__table__.create(bind=engine)
    monkeypatch.setattr(memory_module, "SessionLocal", sessionmaker(bind=engine, expire_on_commit=False))
    return engine


def test_approved_scope_ttl_priors_are_ranked_and_explainable(monkeypatch):
    _memory_session(monkeypatch)
    now = datetime.now(timezone.utc)
    with memory_module.SessionLocal.begin() as session:
        session.add_all([
            MemoryItemRecord(memory_id="exact", workspace_id="demo", status="approved", content="浦东饮料的审核经验", evidence_ids=["ev-1"], scope={"region": "上海", "store": "浦东", "sku": "drink-001"}, confidence=0.8, expires_at=now + timedelta(days=5)),
            MemoryItemRecord(memory_id="broad", workspace_id="demo", status="approved", content="上海区域的审核经验", evidence_ids=["ev-2"], scope={"region": "上海"}, confidence=0.95, expires_at=now + timedelta(days=5)),
            MemoryItemRecord(memory_id="expired", workspace_id="demo", status="approved", content="已过期经验", evidence_ids=["ev-3"], scope={"region": "上海"}, confidence=1.0, expires_at=now - timedelta(seconds=1)),
            MemoryItemRecord(memory_id="other-store", workspace_id="demo", status="approved", content="其他门店经验", evidence_ids=["ev-4"], scope={"region": "上海", "store": "徐汇"}, confidence=1.0, expires_at=now + timedelta(days=5)),
        ])

    result = MemoryService().retrieve_approved_priors("demo", {"region": "上海", "store": "浦东", "sku": "drink-001"})

    assert [item["memory_id"] for item in result["items"]] == ["exact", "broad"]
    assert result["items"][0]["match_reason"]["exact_scope_keys"] == ["region", "store", "sku"]
    assert result["retrieval"] == {
        "strategy": "approved_scope_ttl_top_k", "limit": 8,
        "approved_candidates": 4, "scope_ttl_matches": 2,
        "expired_excluded": 1, "scope_mismatch_excluded": 1,
        "fact_boundary": "Historical priors cannot become current RiskEvent evidence or citations.",
    }


def test_specific_memory_does_not_match_a_task_with_unknown_scope(monkeypatch):
    _memory_session(monkeypatch)
    with memory_module.SessionLocal.begin() as session:
        session.add(MemoryItemRecord(memory_id="store-only", workspace_id="demo", status="approved", content="门店经验", scope={"store": "浦东"}, confidence=0.8, expires_at=datetime.now(timezone.utc) + timedelta(days=1)))

    result = MemoryService().retrieve_approved_priors("demo", {"region": "上海"})

    assert result["items"] == []
    assert result["retrieval"]["scope_mismatch_excluded"] == 1


def test_replacement_candidate_keeps_previous_memory_until_approved(monkeypatch):
    _memory_session(monkeypatch)
    with memory_module.SessionLocal.begin() as session:
        session.add(MemoryItemRecord(
            memory_id="old", workspace_id="demo", status="approved", kind="risk_pattern",
            content="暴雨黄色预警时提前一天补货", evidence_ids=["ev-old"],
            scope={"region": "上海", "sku": "drink-001"}, confidence=0.8,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        ))

    service = MemoryService()
    previous, candidate = service.supersede("old", "暴雨黄色预警时提前两天补货", "reviewer-a")

    assert previous["status"] == "approved"
    assert candidate["status"] == "candidate"
    assert candidate["supersedes_id"] == "old"
    assert [item["memory_id"] for item in service.retrieve_approved_priors("demo", {"region": "上海", "sku": "drink-001"})["items"]] == ["old"]

    # Rejection/expiration of the candidate must not create a recall gap.
    service.expire(candidate["memory_id"], "reviewer-b")
    assert service.get("old")["status"] == "approved"
    assert [item["memory_id"] for item in service.retrieve_approved_priors("demo", {"region": "上海", "sku": "drink-001"})["items"]] == ["old"]


def test_approving_replacement_atomically_switches_memory_lineage(monkeypatch):
    _memory_session(monkeypatch)
    with memory_module.SessionLocal.begin() as session:
        session.add(MemoryItemRecord(
            memory_id="old", workspace_id="demo", status="approved", content="旧版配送规则",
            evidence_ids=["ev-old"], scope={"region": "上海"}, confidence=0.8,
            expires_at=datetime.now(timezone.utc) + timedelta(days=10),
        ))

    service = MemoryService()
    _, candidate = service.supersede("old", "新版配送规则", "reviewer-a")
    approved = service.approve(candidate["memory_id"], "reviewer-b")

    assert approved["status"] == "approved"
    assert approved["superseded_memory_id"] == "old"
    assert service.get("old")["status"] == "superseded"
    assert [item["memory_id"] for item in service.retrieve_approved_priors("demo", {"region": "上海"})["items"]] == [candidate["memory_id"]]
