from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool, retrieve_sources
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
    assert decision["next_action"]["tool"] == "retrieve_approved_memory"
    state.update(decision)
    executed = agent_execute_tool(state)
    assert executed["agent_actions"][0]["tool"] == "retrieve_approved_memory"
    assert executed["external_searches"] == 0


def test_storeflow_decision_has_three_named_purchase_options():
    decision = make_decision([{"event_id": "promo", "event_type": "demand_surge", "confidence": 0.8}], seed=7)
    assert [item["strategy"] for item in decision["strategies"]][:2] == ["正常订货", "适度加订"]


def test_agent_accepts_a_structured_model_tool_choice(monkeypatch):
    class Client:
        class settings: model_enabled = True
        def status(self): return {"provider": "test", "enabled": True}
        def complete_json(self, **_kwargs): return {"tool": "search_recent_risk", "reason": "配送维度缺少近期天气和交通证据。"}, {"total_tokens": 12}
    monkeypatch.setattr("app.agent.nodes.workflow.BailianClient", Client)
    state = initial_state("structured-agent", "门店暴雨促销补货")
    state["agent_actions"] = [{"tool": "retrieve_approved_memory", "status": "completed"}, {"tool": "search_internal_knowledge", "status": "completed"}]
    state["sources"] = [{"source_id": "s", "title": "库存日报", "url": "https://example.com/s", "content": "门店库存和促销销量", "retrieved_at": "2026-08-22T00:00:00Z"}]
    result = agent_decide_next_action(state)
    assert result["next_action"]["tool"] == "search_recent_risk"
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
