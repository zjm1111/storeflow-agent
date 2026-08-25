from datetime import datetime, timedelta, timezone

import pytest
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
    retrieval = result["retrieval"]
    assert retrieval["strategy"] == "approved_scope_ttl_catalog_then_load"
    assert retrieval["catalog_limit"] == 5
    assert retrieval["approved_candidates"] == 4
    assert retrieval["scope_ttl_matches"] == 2
    assert retrieval["catalog_selected"] == 2
    assert retrieval["content_loaded"] == 2
    assert retrieval["content_budget_excluded"] == 0
    assert retrieval["expired_excluded"] == 1
    assert retrieval["scope_mismatch_excluded"] == 1
    assert retrieval["fact_boundary"] == "Historical priors cannot become current RiskEvent evidence or citations."


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


def test_memory_catalog_selects_before_loading_content_under_a_separate_budget(monkeypatch):
    _memory_session(monkeypatch)
    now = datetime.now(timezone.utc)
    with memory_module.SessionLocal.begin() as session:
        session.add_all([
            MemoryItemRecord(memory_id="exact", workspace_id="demo", status="approved", kind="risk_pattern", summary="浦东饮料暴雨补货案例", content="A" * 160, scope={"region": "上海", "store": "浦东"}, confidence=0.8, expires_at=now + timedelta(days=5)),
            MemoryItemRecord(memory_id="broad", workspace_id="demo", status="approved", kind="risk_pattern", summary="上海区域配送案例", content="B" * 160, scope={"region": "上海"}, confidence=0.7, expires_at=now + timedelta(days=5)),
            MemoryItemRecord(memory_id="third", workspace_id="demo", status="approved", kind="risk_pattern", summary="不应进入目录的第三条", content="C" * 20, scope={"region": "上海"}, confidence=0.6, expires_at=now + timedelta(days=5)),
        ])
    monkeypatch.setattr(memory_module, "get_settings", lambda: type("Settings", (), {
        "memory_catalog_limit": 2, "memory_context_token_budget": 20,
    })())

    result = MemoryService().retrieve_approved_priors("demo", {"region": "上海", "store": "浦东"})

    assert result["retrieval"]["catalog_selected"] == 2
    assert result["retrieval"]["content_loaded"] == 1
    assert result["retrieval"]["content_budget_excluded"] == 1
    assert result["retrieval"]["content_used_tokens"] <= 20
    assert result["items"][0]["memory_id"] == "exact"
    assert result["items"][0]["summary"] == "浦东饮料暴雨补货案例"
    assert result["items"][0]["content_truncated"] is True
    assert result["items"][0]["content"].endswith("…")


def test_memory_kind_lifecycle_limits_agent_candidates_and_applies_kind_ttl(monkeypatch):
    _memory_session(monkeypatch)
    monkeypatch.setattr(memory_module, "get_settings", lambda: type("Settings", (), {
        "memory_default_ttl_days": 90,
        "memory_episodic_ttl_days": 30,
        "memory_semantic_ttl_days": 180,
        "memory_procedural_ttl_days": 365,
    })())
    service = MemoryService()

    episodic = service.create_candidate(
        workspace_id="demo", content="暴雨配送延迟后的已审核历史案例", evidence_ids=["ev-1"],
        scope={"region": "上海"}, confidence=0.8,
    )
    assert episodic["kind"] == "episodic"
    with pytest.raises(ValueError, match="kind=episodic"):
        service.create_candidate(
            workspace_id="demo", content="中央仓默认服务区域", evidence_ids=["ev-2"],
            scope={"region": "上海"}, confidence=0.8, kind="semantic",
        )
    with pytest.raises(ValueError, match="kind=episodic"):
        service.create_candidate(
            workspace_id="demo", content="发生中断必须人工确认", evidence_ids=["ev-3"],
            scope={"region": "上海"}, confidence=0.8, kind="procedural",
        )

    procedural = service.create_candidate(
        workspace_id="demo", content="中央仓确认中断时必须请求采购负责人审核", evidence_ids=["ev-policy"],
        scope={"region": "上海"}, confidence=0.9, kind="procedural", human_initiated=True,
    )
    approved = service.approve(procedural["memory_id"], "reviewer-a")
    assert approved["kind"] == "procedural"
    expires_at = datetime.fromisoformat(approved["expires_at"])
    assert 364 <= (expires_at - datetime.now(timezone.utc)).days <= 365
