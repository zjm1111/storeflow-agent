"""Recovery tests for durable action phases and duplicate worker delivery."""
from copy import deepcopy

import pytest

from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool, agent_mark_action_running
from app.agent.state import initial_state
from app.services.research import ResearchService
from app.worker import execute_task


def _source() -> dict:
    return {
        "source_id": "recovery-fixture",
        "title": "门店库存、促销与中央仓通知",
        "url": "https://example.com/recovery-fixture",
        "content": "门店库存不足，促销带动销量和需求上升；中央仓配送因暴雨延迟，采购成本、缺货成本需要复核。",
        "retrieved_at": "2026-08-22T00:00:00Z",
        "source_type": "fixture",
    }


@pytest.fixture
def deterministic_retrieval(monkeypatch):
    calls = []

    def retrieve(state):
        calls.append(state.get("active_action", {}).get("action_id"))
        source = _source()
        return {
            "sources": [source],
            "hybrid_results": [{"source_id": source["source_id"], "candidate_id": source["source_id"], "rrf_score": 0.03, "rerank_score": 0.8}],
            "search_count": state.get("search_count", 0) + 1,
            "recalled_memories": [],
            "dependency_execution": {"mode": "test"},
            "working_memory": {**state.get("working_memory", {}), "source_rerank_ids": [source["source_id"]], "parallel_retrieval": {"completed_lanes": ["test"]}},
        }

    monkeypatch.setattr("app.agent.nodes.workflow.retrieve_sources", retrieve)
    return calls


def _persist_phase(service: ResearchService, phase: str) -> tuple[dict, str]:
    state = initial_state("recovery-" + phase, "暴雨促销下门店饮料补货")
    state["status"] = "running"
    state.update(agent_decide_next_action(state))
    action_id = state["active_action"]["action_id"]
    if phase in {"running", "completed"}:
        state.update(agent_mark_action_running(state))
    if phase == "completed":
        state.update(agent_execute_tool(state))
    service.repository.save(state["task_id"], state)
    service.snapshot_history.record_snapshot(state)
    return state, action_id


@pytest.mark.parametrize("phase,expected_attempts", [("planned", 1), ("running", 2)])
def test_crash_before_or_during_tool_reuses_one_action_id(deterministic_retrieval, phase, expected_attempts):
    service = ResearchService()
    state, action_id = _persist_phase(service, phase)

    service.run(state["task_id"])
    recovered = service.get(state["task_id"])
    action = next(item for item in recovered["agent_actions"] if item["action_id"] == action_id)

    assert deterministic_retrieval == [action_id]
    assert action["status"] == "completed"
    assert action["attempts"] == expected_attempts
    assert len([item for item in recovered["agent_actions"] if item["action_id"] == action_id]) == 1
    assert recovered["graph_execution"]["thread_id"] == state["task_id"]
    assert recovered["graph_execution"]["checkpointer_mode"] == "memory"
    assert recovered["status"] == "awaiting_review"


def test_crash_after_completed_tool_does_not_reexecute_action(deterministic_retrieval):
    service = ResearchService()
    state, action_id = _persist_phase(service, "completed")
    assert deterministic_retrieval == [action_id]

    service.run(state["task_id"])
    recovered = service.get(state["task_id"])

    assert deterministic_retrieval == [action_id]
    action = next(item for item in recovered["agent_actions"] if item["action_id"] == action_id)
    assert action["status"] == "completed"
    assert action["attempts"] == 1
    assert recovered["status"] == "awaiting_review"
    assert service.snapshot_history.latest_snapshot(state["task_id"])["state_version"] == recovered["state_version"]


def test_review_replan_can_restart_in_a_new_service_process(deterministic_retrieval):
    first = ResearchService()
    task = first.start("暴雨促销下门店饮料补货")
    first.run(task["task_id"])
    awaiting_review = first.get(task["task_id"])
    assert awaiting_review["status"] == "awaiting_review"

    replanned = first.review(task["task_id"], "need_more_evidence", "请补充配送证据", ["delivery"])
    assert replanned["status"] == "queued"
    version_after_review = replanned["state_version"]

    restarted = ResearchService()
    restarted.run(task["task_id"])
    recovered = restarted.get(task["task_id"])

    assert recovered["status"] == "awaiting_review"
    assert recovered["state_version"] > version_after_review
    assert any(item["action"] == "need_more_evidence" for item in recovered["audit_trail"])
    assert any(item["tool"] == "retrieve_evidence" and item["status"] == "completed" for item in recovered["agent_actions"])


def test_duplicate_worker_delivery_is_ignored_after_first_run(deterministic_retrieval):
    service = ResearchService()
    task = service.start("暴雨促销下门店饮料补货")
    queued = deepcopy(service.get(task["task_id"]))

    assert execute_task(task["task_id"], queued["checkpoint"]["version"], "demo", queued["state_version"]) is True
    first_result = service.get(task["task_id"])
    first_version, first_trace_count = first_result["state_version"], len(first_result["trace"])

    assert execute_task(task["task_id"], queued["checkpoint"]["version"], "demo", queued["state_version"]) is False
    duplicate_result = service.get(task["task_id"])

    assert duplicate_result["state_version"] == first_version
    assert len(duplicate_result["trace"]) == first_trace_count
    assert duplicate_result["status"] == "awaiting_review"
