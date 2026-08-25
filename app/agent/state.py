from typing import TypedDict


class ResearchState(TypedDict, total=False):
    task_id: str
    state_version: int
    question: str
    plan: dict | None
    sources: list[dict]
    evidence: list[dict]
    events: list[dict]
    errors: list[str]
    trace: list[dict]
    loop_count: int
    token_usage: int
    estimated_cost_usd: float
    status: str
    report: dict | None
    hybrid_results: list[dict]
    coverage: dict[str, float]
    missing_dimensions: list[str]
    search_count: int
    max_search: int
    max_loop: int
    max_latency_seconds: int
    started_at: str
    search_query: str
    stop_reason: str | None
    checkpoint: dict
    decision: dict | None
    human_review: dict
    audit_trail: list[dict]
    final_report: dict | None
    model_execution: list[dict]
    dependency_execution: dict
    workspace_id: str
    scope: dict
    constraints: dict
    working_memory: dict
    recalled_memories: list[dict]
    situational_memories: list[dict]
    memory_conflicts: list[dict]
    memory_candidate_extraction: dict
    memory_candidates: list[dict]
    context_pack: dict
    agent_actions: list[dict]
    next_action: dict | None
    active_action: dict | None
    external_searches: int
    agent_finished: bool
    model_decision_count: int
    max_model_decisions: int
    review_requested: bool


def initial_state(task_id: str, question: str, workspace_id: str = "demo", scope: dict | None = None, constraints: dict | None = None, idempotency_key: str | None = None) -> ResearchState:
    return {
        "task_id": task_id,
        "state_version": 0,
        "workspace_id": workspace_id,
        "scope": scope or {},
        "constraints": constraints or {},
        "idempotency_key": idempotency_key,
        "working_memory": {"queries": [], "coverage_gaps": [], "selected_evidence_ids": [], "context_summary": "", "parallel_retrieval": {}},
        "recalled_memories": [],
        "situational_memories": [],
        "memory_conflicts": [],
        "context_pack": {"budget_tokens": 12000, "used_tokens": 0, "items": []},
        "agent_actions": [],
        "next_action": None,
        "active_action": None,
        "external_searches": 0,
        "agent_finished": False,
        "model_decision_count": 0,
        "max_model_decisions": 2,
        "review_requested": False,
        "question": question,
        "plan": None,
        "sources": [],
        "evidence": [],
        "events": [],
        "errors": [],
        "trace": [],
        "loop_count": 0,
        "token_usage": 0,
        "estimated_cost_usd": 0.0,
        "status": "running",
        "report": None,
        "hybrid_results": [],
        "coverage": {"inventory": 0.0, "delivery": 0.0, "demand": 0.0, "cost": 0.0},
        "missing_dimensions": ["inventory", "delivery", "demand", "cost"],
        "search_count": 0,
        "max_search": 2,
        "max_loop": 6,
        "max_latency_seconds": 75,
        "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "search_query": question,
        "stop_reason": None,
        "checkpoint": {"node": "queued", "version": 1},
        "decision": None,
        "human_review": {"status": "not_requested", "comment": None, "constraints": None},
        "audit_trail": [],
        "final_report": None,
        "model_execution": [],
        "dependency_execution": {},
    }
