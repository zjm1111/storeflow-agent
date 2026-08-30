import asyncio
from collections.abc import AsyncIterator
from urllib.parse import unquote

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.services.research import ResearchService
from app.services.retrieval import HybridRetriever
from app.services.evaluation import run_evaluation
from app.repositories import StateConflictError
from app.agent.schemas import FulfillmentScope
from app.core.auth import Principal, get_current_principal, require_roles

router = APIRouter(prefix="/tasks", tags=["tasks"])
service = ResearchService()


class CreateTaskRequest(BaseModel):
    question: str = Field(min_length=5, max_length=2000)
    scope: FulfillmentScope = Field(default_factory=FulfillmentScope)
    constraints: dict[str, float | int] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, max_length=128)


class KnowledgeSearchRequest(BaseModel):
    query: str = Field(min_length=3, max_length=2000)
    limit: int = Field(default=5, ge=1, le=20)


class ReviewRequest(BaseModel):
    comment: str | None = Field(default=None, max_length=2000)
    constraints: dict[str, float | int] | None = None
    evidence_dimensions: list[str] | None = Field(default=None, max_length=4)


_ALLOWED_CONSTRAINTS = {"demand_mean", "demand_stddev", "current_inventory", "lead_time_days", "delay_probability", "extra_delay_days", "purchase_cost", "holding_cost", "stockout_cost", "expedite_cost", "budget", "max_replenishment", "target_service_level"}


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_task(request: CreateTaskRequest, background_tasks: BackgroundTasks, idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"), principal: Principal = Depends(require_roles("operator", "reviewer", "admin"))):
    key = idempotency_key_header or request.idempotency_key
    task = service.start(request.question, workspace_id=principal.workspace_id, scope=request.scope.model_dump(exclude_none=True), constraints=request.constraints, idempotency_key=key)
    # An idempotent retry must not schedule a second execution.
    if task["status"] == "queued":
        _schedule_research(task, principal.workspace_id, background_tasks)
    return {"task_id": task["task_id"], "status": task["status"], "trace": task["trace"], "workspace_id": task.get("workspace_id", "demo"), "idempotent": bool(key)}


@router.post("/knowledge/search")
def search_knowledge(request: KnowledgeSearchRequest, _: Principal = Depends(get_current_principal)):
    return {"query": request.query, "results": HybridRetriever().search_knowledge(request.query, request.limit)}


@router.post("/knowledge/upload")
async def upload_knowledge_pdf(request: Request, _: Principal = Depends(require_roles("operator", "admin"))):
    raw = await request.body()
    filename = unquote(request.headers.get("x-filename", "uploaded-document.pdf"))
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF must be 5 MB or smaller")
    try:
        source = HybridRetriever().ingest_pdf(filename, raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    message = "PDF already exists in the knowledge base" if source["duplicate"] else "PDF imported into the knowledge base"
    return {"message": message, "duplicate": source["duplicate"], "source": source}


@router.get("/evaluations/run")
def evaluation_baseline(_: Principal = Depends(require_roles("admin"))):
    """Run the checked-in deterministic Week-9 baseline without external calls."""
    return run_evaluation()


@router.post("/{task_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_task(task_id: str, background_tasks: BackgroundTasks, principal: Principal = Depends(require_roles("operator", "reviewer", "admin"))):
    task = service.resume(task_id, principal.workspace_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found or already completed")
    _schedule_research(task, principal.workspace_id, background_tasks)
    return {"task_id": task_id, "status": "queued", "checkpoint": task.get("checkpoint")}


@router.get("/{task_id}/review")
def get_review(task_id: str, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    task = service.get(task_id, principal.workspace_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {key: task.get(key) for key in ("task_id", "status", "human_review", "audit_trail", "decision", "evidence", "events", "final_report")}


@router.get("/{task_id}/evidence")
def get_task_evidence(task_id: str, principal: Principal = Depends(get_current_principal)):
    task = service.get(task_id, principal.workspace_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "evidence_context_pack": task.get("evidence_context_pack", {}), "evidence": task.get("evidence", []), "sources": task.get("sources", []), "conflicts": [item for item in task.get("evidence", []) if item.get("conflict_status") == "pending_review"]}


@router.get("/{task_id}/memory")
def get_task_memory(task_id: str, principal: Principal = Depends(get_current_principal)):
    task = service.get(task_id, principal.workspace_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task_id, "working_memory": task.get("working_memory", {}), "historical_prior": task.get("working_memory", {}).get("historical_prior", {}), "situational_memories": task.get("situational_memories", []), "recalled_memories": task.get("recalled_memories", []), "memory_conflicts": task.get("memory_conflicts", []), "memory_candidates": task.get("memory_candidates", []), "candidate_extraction": task.get("memory_candidate_extraction")}


def _schedule_research(task: dict, workspace_id: str, background_tasks: BackgroundTasks) -> None:
    """Use Celery for both initial research and review-triggered replanning."""
    try:
        from app.worker import celery_app, run_task
        if celery_app is None or run_task is None:
            raise RuntimeError("Celery is not installed")
        # ``apply_async`` reaches the same named worker task in production and
        # also honours Celery's task_always_eager setting in isolated tests.
        run_task.apply_async(
            args=[task["task_id"], task.get("checkpoint", {}).get("version"), workspace_id, task.get("state_version")],
        )
    except Exception:
        # No-broker test/dev mode remains deterministic and usable.
        background_tasks.add_task(service.run, task["task_id"], workspace_id)


def _review_action(task_id: str, action: str, request: ReviewRequest, principal: Principal, background_tasks: BackgroundTasks | None = None):
    try:
        task = service.review(task_id, action, request.comment, request.constraints, request.evidence_dimensions, workspace_id=principal.workspace_id, reviewer=principal.subject)
    except StateConflictError as exc:
        raise HTTPException(status_code=409, detail="Task state changed; refresh and retry the review action") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=409, detail="Review action requires a task awaiting_review")
    if action == "need_more_evidence" and background_tasks is not None:
        _schedule_research(task, principal.workspace_id, background_tasks)
    return {key: task.get(key) for key in ("task_id", "status", "human_review", "audit_trail", "decision", "final_report", "memory_candidates", "memory_candidate_extraction")}


@router.post("/{task_id}/review")
def review(task_id: str, request: ReviewRequest, background_tasks: BackgroundTasks, action: str = "approve", principal: Principal = Depends(require_roles("reviewer", "admin"))):
    if action not in {"approve", "modify_constraints", "need_more_evidence", "reject"}:
        raise HTTPException(status_code=422, detail="Unsupported review action")
    if action == "modify_constraints":
        if not request.constraints:
            raise HTTPException(status_code=422, detail="constraints are required when modifying constraints")
        invalid = set(request.constraints) - _ALLOWED_CONSTRAINTS
        if invalid:
            raise HTTPException(status_code=422, detail=f"Unsupported constraints: {', '.join(sorted(invalid))}")
    return _review_action(task_id, action, request, principal, background_tasks)


@router.get("/{task_id}")
def get_task(task_id: str, principal: Principal = Depends(get_current_principal)):
    task = service.get(task_id, principal.workspace_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"task_id": task["task_id"], "status": task["status"], "state_version": task.get("state_version"), "trace": task["trace"], "errors": task["errors"], "checkpoint": task.get("checkpoint"), "active_action": task.get("active_action"), "coverage": task.get("coverage"), "stop_reason": task.get("stop_reason")}


@router.get("/{task_id}/result")
def get_result(task_id: str, principal: Principal = Depends(get_current_principal)):
    task = service.get(task_id, principal.workspace_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return {key: task.get(key) for key in ("task_id", "workspace_id", "scope", "status", "state_version", "plan", "sources", "evidence", "events", "report", "hybrid_results", "evidence_context_pack", "working_memory", "situational_memories", "recalled_memories", "memory_conflicts", "memory_candidates", "memory_candidate_extraction", "agent_actions", "active_action", "errors", "trace", "coverage", "missing_dimensions", "hypotheses", "analysis_snapshot", "investigation_status", "unresolved_conflicts", "search_count", "max_search", "max_loop", "max_latency_seconds", "stop_reason", "checkpoint", "decision", "human_review", "audit_trail", "final_report", "model_execution", "dependency_execution", "context_telemetry", "token_usage", "estimated_cost_usd")}


@router.get("/{task_id}/events")
async def task_events(task_id: str, request: Request, principal: Principal = Depends(get_current_principal)):
    if not service.get(task_id, principal.workspace_id):
        raise HTTPException(status_code=404, detail="Task not found")

    async def stream() -> AsyncIterator[str]:
        last_id = request.headers.get("Last-Event-ID")
        for event in service.events.history(task_id, last_id):
            yield service.events.encode(event)
            last_id = event["id"]
        while True:
            events = service.events.history(task_id, last_id)
            if events:
                for event in events:
                    yield service.events.encode(event)
                    last_id = event["id"]
            else:
                yield ": keep-alive\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
