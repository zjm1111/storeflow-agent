from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool, agent_mark_action_running, agent_recover_action, retrieve_sources
from app.agent.graph import _resume_route
from app.agent.state import initial_state
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


def test_rrf_tracks_lanes_without_turning_memory_into_evidence():
    fused = rrf_fuse_lanes({"internal": [{"source_id": "s1"}], "memory": [{"memory_id": "m1"}]})
    assert {item["candidate_id"] for item in fused} == {"s1", "m1"}
    assert all(item["rrf_lanes"] for item in fused)


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
    result = retrieve_sources(initial_state("parallel", "暴雨促销期间门店订货", scope={"store": "浦东门店"}))
    parallel = result["working_memory"]["parallel_retrieval"]
    assert parallel["mode"] == "fan_out_fan_in"
    assert set(parallel["completed_lanes"]) == {"internal_knowledge", "public_risk", "approved_memory"}
    assert result["dependency_execution"]["tavily"]["status"] in {"used", "degraded", "not_configured"}
    assert "estimated_cost_usd" in result["dependency_execution"]["tavily"]
    assert {item["candidate_id"] for item in result["hybrid_results"]} == {"internal-1", "public-1", "memory:m1"}
