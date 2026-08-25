from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool, agent_mark_action_running, agent_recover_action, extract_events, generate_report, retrieve_sources
from app.agent.graph import _resume_route
from app.agent.state import initial_state
from app.services.context import build_controller_context, build_report_context, build_risk_context
from app.services.decision import make_decision
from app.services.retrieval import HybridRetriever, rrf_fuse_lanes


def test_rrf_keeps_both_retrieval_channels_visible():
    ranked = HybridRetriever.__new__(HybridRetriever)._rank(
        "store promotion rain", [{"source_id": "a", "title": "A", "url": "https://example.com/a", "content": "store promotion rain"}, {"source_id": "b", "title": "B", "url": "https://example.com/b", "content": "unrelated"}], {"a": 0.2, "b": 0.9}
    )
    assert all("rrf_score" in item for item in ranked)
    assert ranked[0]["rrf_score"] > 0


def test_agent_only_selects_read_only_whitelisted_tools(monkeypatch):
    class Retriever:
        def retrieve_knowledge(self, _query): return [], []
        def retrieve(self, _query): return [], [], []
    monkeypatch.setattr("app.agent.nodes.workflow.HybridRetriever", Retriever)
    state = initial_state("storeflow-agent", "暴雨期间门店饮料促销如何订货？")
    decision = agent_decide_next_action(state)
    assert decision["next_action"]["tool"] == "retrieve_evidence"
    assert decision["active_action"]["status"] == "planned"
    assert decision["active_action"]["action_id"] == decision["next_action"]["action_id"]
    state.update(decision)
    running = agent_mark_action_running(state)
    assert running["active_action"]["status"] == "running"
    state.update(running)
    executed = agent_execute_tool(state)
    assert executed["agent_actions"][0]["tool"] == "retrieve_evidence"
    assert executed["agent_actions"][0]["status"] == "completed"
    assert executed["active_action"] is None
    assert executed["search_count"] == 1


def test_interrupted_action_becomes_unknown_then_retries_with_same_action_id():
    state = initial_state("recover-agent", "暴雨期间门店饮料促销如何订货？")
    planned = agent_decide_next_action(state)
    state.update(planned)
    running = agent_mark_action_running(state)
    action_id = running["active_action"]["action_id"]
    state.update(running)
    recovered = agent_recover_action(state)
    assert recovered["active_action"]["status"] == "unknown"
    assert recovered["active_action"]["action_id"] == action_id
    state.update(recovered)
    retried = agent_mark_action_running(state)
    assert retried["active_action"]["status"] == "running"
    assert retried["active_action"]["action_id"] == action_id
    assert retried["active_action"]["attempts"] == 2


def test_resume_route_uses_action_phase_not_only_checkpoint_node():
    state = initial_state("resume-agent", "暴雨期间门店饮料促销如何订货？")
    # This is a one-release compatibility branch. New tasks resume through
    # LangGraph's native pending checkpoint and bypass START entirely.
    state["graph_execution"] = {"legacy_resume": True}
    state.update(agent_decide_next_action(state))
    assert _resume_route(state) == "agent_mark_action_running"
    state.update(agent_mark_action_running(state))
    assert _resume_route(state) == "agent_recover_action"
    state.update(agent_recover_action(state))
    assert _resume_route(state) == "agent_recover_action"
    state["active_action"] = None
    state["next_action"] = None
    state["checkpoint"] = {"node": "agent_execute_tool", "version": 9}
    assert _resume_route(state) == "agent_decide_next_action"


def test_failed_tool_action_is_durable_and_does_not_remain_active(monkeypatch):
    def broken(_state):
        raise RuntimeError("controlled retrieval failure")
    monkeypatch.setattr("app.agent.nodes.workflow.retrieve_sources", broken)
    state = initial_state("failed-agent", "暴雨期间门店饮料促销如何订货？")
    state.update(agent_decide_next_action(state))
    state.update(agent_mark_action_running(state))
    failed = agent_execute_tool(state)
    assert failed["active_action"] is None
    assert failed["next_action"] is None
    assert failed["agent_actions"][0]["status"] == "failed"
    assert "controlled retrieval failure" in failed["agent_actions"][0]["failure_reason"]


def test_storeflow_decision_has_three_named_purchase_options():
    decision = make_decision([{"event_id": "promo", "event_type": "demand_surge", "confidence": 0.8}], seed=7)
    assert [item["strategy"] for item in decision["strategies"]] == ["正常订货", "适度加订", "高保障加订"]
    quantities = [item["replenishment_quantity"] for item in decision["strategies"]]
    assert quantities == sorted(quantities)


def test_infeasible_service_target_reports_simulated_ceiling_and_remedies():
    decision = make_decision([], seed=7, samples=1000, constraints={
        "current_inventory": 144,
        "demand_mean": 180,
        "demand_stddev": 0,
        "lead_time_days": 2,
        "delay_probability": 0.166,
        "extra_delay_days": 2,
        "purchase_cost": 10,
        "budget": 1500,
        "max_replenishment": 200,
        "target_service_level": 0.92,
    })
    summary = decision["feasibility_summary"]
    assert decision["recommended_strategy"] is None
    assert summary["max_achievable_service_level"] < summary["target_service_level"]
    assert decision["strategies"][-1]["replenishment_quantity"] == 150
    assert "配送延迟覆盖整个补货周期" in decision["infeasibility_reason"]
    assert summary["remediation_options"]


def test_agent_accepts_a_structured_model_tool_choice(monkeypatch):
    class Client:
        class settings: model_enabled = True
        def status(self): return {"provider": "test", "enabled": True}
        def complete_json(self, **_kwargs): return {"tool": "retrieve_evidence", "reason": "配送维度缺少近期天气和交通证据。"}, {"total_tokens": 12}
    monkeypatch.setattr("app.agent.nodes.workflow.BailianClient", Client)
    state = initial_state("structured-agent", "门店暴雨促销补货")
    state["agent_actions"] = [{"tool": "retrieve_evidence", "status": "completed"}]
    state["sources"] = [{"source_id": "s", "title": "库存日报", "url": "https://example.com/s", "content": "门店库存和促销销量", "retrieved_at": "2026-08-22T00:00:00Z"}]
    result = agent_decide_next_action(state)
    assert result["next_action"]["tool"] == "retrieve_evidence"
    assert result["token_usage"] == 12
    assert result["context_telemetry"][-1]["call"] == "controller"
    assert result["context_telemetry"][-1]["mode"] == "remote"


def test_risk_and_report_models_only_receive_compressed_evidence_context(monkeypatch):
    raw_marker = "RAW_ORIGINAL_QUOTE_MUST_NOT_ENTER_MODEL_CONTEXT"
    prompts = []

    class Client:
        class settings:
            model_enabled = True
            model_enrichment_enabled = True

        def status(self): return {"provider": "test", "enabled": True}

        def complete_json(self, **kwargs):
            prompts.append(kwargs["user"])
            if "REPORT_CONTEXT" in kwargs["user"]:
                return {"markdown": "## 报告\n[证据: ev-context]", "citation_evidence_ids": ["ev-context"]}, {"total_tokens": 4}
            return {"events": [{"event_type": "logistics_delay", "summary": "配送风险", "affected_entity": "浦东门店", "confidence": 0.6, "evidence_ids": ["ev-context"], "source_ids": ["source-context"], "severity": "high"}]}, {"total_tokens": 4}

    monkeypatch.setattr("app.agent.nodes.workflow.BailianClient", Client)
    state = initial_state("context-model", "暴雨期间门店如何订货？", scope={"store": "浦东门店"})
    state["evidence"] = [{"evidence_id": "ev-context", "source_id": "source-context", "source_type": "fixture", "quote": raw_marker, "relevance_score": 0.9, "authority_score": 0.9, "freshness_score": 0.9, "overall_score": 0.9, "chunk_index": 0, "conflict_status": "none"}]
    state["sources"] = [{"source_id": "source-context", "title": "配送通知", "url": "https://example.com/context"}]
    state["evidence_context_pack"] = {"kind": "current_evidence", "items": [{"evidence_id": "ev-context", "source_id": "source-context", "summary": "[证据: ev-context] 暴雨导致配送延迟", "page_number": 1, "char_start": 0, "char_end": 20}]}
    events = extract_events(state)
    state.update(events)
    generate_report(state)
    assert len(prompts) == 2
    assert "RISK_CONTEXT" in prompts[0]
    assert "REPORT_CONTEXT" in prompts[1]
    assert all(raw_marker not in prompt for prompt in prompts)


def test_context_projections_isolate_historical_prior_from_current_evidence():
    state = initial_state("projection", "门店暴雨补货", scope={"store": "浦东门店"})
    state["evidence_context_pack"] = {"items": [{"evidence_id": "ev-1", "source_id": "s-1", "summary": "[证据: ev-1] 暴雨导致配送延迟"}]}
    state["recalled_memories"] = [{"memory_id": "mem-1", "kind": "episodic", "summary": "历史暴雨案例", "content": "历史经验只用于提示复核", "scope": {"store": "浦东门店"}, "confidence": 0.8, "evidence_ids": ["old-ev"]}]
    controller = build_controller_context(state)
    risk = build_risk_context(state)
    report = build_report_context(state, risk_events=[])
    assert controller["historical_prior"]["items"][0]["label"] == "HISTORICAL_PRIOR_NOT_CURRENT_EVIDENCE"
    assert controller["current_evidence_status"]["label"] == "CURRENT_EVIDENCE"
    assert risk["historical_prior"]["included"] is False
    assert report["historical_prior"]["included"] is False
    assert all(item["label"] == "CURRENT_EVIDENCE" for item in risk["current_evidence"])


def test_rrf_tracks_source_lanes_only():
    fused = rrf_fuse_lanes({"internal": [{"source_id": "s1"}], "public": [{"source_id": "s2"}]})
    assert {item["candidate_id"] for item in fused} == {"s1", "s2"}
    assert all(item["rrf_lanes"] for item in fused)


def test_rrf_rejects_memory_candidates_to_preserve_fact_boundary():
    import pytest

    with pytest.raises(ValueError, match="historical-prior chain"):
        rrf_fuse_lanes({"approved_memory": [{"memory_id": "m1"}]})


def test_seeded_current_source_enters_source_rrf_and_rerank(monkeypatch):
    class Retriever:
        def retrieve_knowledge(self, _query): return [], []

        def _rerank(self, query, sources, ranked, errors):
            return HybridRetriever._rerank(self, query, sources, ranked, errors)

    class Memory:
        def list_for_task(self, _workspace, _scope): return [{"memory_id": "m1", "content": "仅作历史先验"}]

    monkeypatch.setattr("app.agent.nodes.workflow.HybridRetriever", Retriever)
    monkeypatch.setattr("app.agent.nodes.workflow.MemoryService", Memory)
    state = initial_state("seeded-source", "暴雨促销下门店饮料补货")
    state["sources"] = [{"source_id": "fixture-1", "title": "门店库存日报", "url": "https://example.com/fixture", "content": "暴雨期间门店饮料促销，库存仅够 1.5 天。", "retrieved_at": "2026-08-22T00:00:00Z"}]
    result = retrieve_sources(state)
    assert [item["source_id"] for item in result["hybrid_results"]] == ["fixture-1"]
    assert result["working_memory"]["source_rerank_ids"] == ["fixture-1"]
    assert result["recalled_memories"][0]["memory_id"] == "m1"


def test_retrieval_fans_out_independent_lanes_then_records_fan_in(monkeypatch):
    class Retriever:
        def retrieve_knowledge(self, _query):
            return ([{"source_id": "internal-1", "title": "库存日报", "url": "https://example.com/in", "content": "门店库存 促销 成本", "retrieved_at": "2026-08-22T00:00:00Z"}], [{"source_id": "internal-1", "rrf_score": 0.03, "rerank_score": 0.8}])

        def retrieve(self, _query):
            return ([{"source_id": "public-1", "title": "天气配送", "url": "https://example.com/out", "content": "暴雨 导致 中央仓 配送 延迟", "retrieved_at": "2026-08-22T00:00:00Z"}], [{"source_id": "public-1", "rrf_score": 0.03, "rerank_score": 0.7}], [])

    class Memory:
        def list_for_task(self, _workspace, _scope):
            return [{"memory_id": "m1", "content": "同区域暴雨时应复核配送时效"}]

    monkeypatch.setattr("app.agent.nodes.workflow.HybridRetriever", Retriever)
    monkeypatch.setattr("app.agent.nodes.workflow.MemoryService", Memory)
    # This unit test verifies the public lane's fan-in behavior without
    # requiring a real Tavily key or network request.
    monkeypatch.setattr(
        "app.agent.nodes.workflow.get_settings",
        lambda: type("Settings", (), {"tavily_api_key": "test-key", "tavily_cost_per_request_usd": 0.0})(),
    )
    result = retrieve_sources(initial_state("parallel", "暴雨促销期间门店订货", scope={"store": "浦东门店"}))
    parallel = result["working_memory"]["parallel_retrieval"]
    assert parallel["mode"] == "source_fan_out_with_memory_prior"
    assert set(parallel["completed_lanes"]) == {"internal_knowledge", "public_risk", "approved_memory"}
    assert result["dependency_execution"]["tavily"]["status"] in {"used", "degraded", "not_configured"}
    assert "estimated_cost_usd" in result["dependency_execution"]["tavily"]
    assert {item["candidate_id"] for item in result["hybrid_results"]} == {"internal-1", "public-1"}
    assert result["recalled_memories"][0]["memory_id"] == "m1"
    assert result["working_memory"]["historical_prior"]["kind"] == "approved_memory_prior"
    assert "not current RiskEvent evidence" in result["working_memory"]["historical_prior"]["fact_boundary"]
