from fastapi import APIRouter, Depends, HTTPException
from typing import Literal

from pydantic import BaseModel, Field

from app.services.memory import MemoryService
from app.core.auth import Principal, require_roles

router = APIRouter(prefix="/memories", tags=["memory"])
service = MemoryService()

class MemoryAction(BaseModel):
    # Retained for old clients, but never trusted: the JWT subject is recorded.
    reviewer: str | None = Field(default=None, min_length=2, max_length=120)
    comment: str | None = Field(default=None, max_length=1000)
    replacement_content: str | None = Field(default=None, max_length=4000)


class ManualMemoryCandidateRequest(BaseModel):
    """Human-maintained facts and playbooks must start at the review boundary."""
    kind: Literal["episodic", "semantic", "procedural"]
    content: str = Field(min_length=5, max_length=4000)
    evidence_ids: list[str] = Field(min_length=1, max_length=32)
    scope: dict[str, str] = Field(default_factory=dict)
    confidence: float = Field(default=0.8, ge=0, le=1)


@router.post("/candidates", status_code=201)
def create_manual_candidate(request: ManualMemoryCandidateRequest, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    """Create a reviewer-owned candidate; procedural memory is never agent-made."""
    try:
        return service.create_candidate(
            workspace_id=principal.workspace_id, content=request.content,
            evidence_ids=request.evidence_ids, scope=request.scope,
            confidence=request.confidence, kind=request.kind, human_initiated=True,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

@router.post("/{memory_id}/approve")
def approve(memory_id: str, request: MemoryAction, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    item = service.approve(memory_id, principal.subject, principal.workspace_id, request.comment)
    if not item: raise HTTPException(404, "Candidate memory not found")
    return item

@router.post("/{memory_id}/expire")
def expire(memory_id: str, request: MemoryAction, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    item = service.expire(memory_id, principal.subject, principal.workspace_id, request.comment)
    if not item: raise HTTPException(404, "Memory not found")
    return item


@router.post("/{memory_id}/reject")
def reject(memory_id: str, request: MemoryAction, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    if not request.comment or len(request.comment.strip()) < 3:
        raise HTTPException(422, "A rejection comment of at least 3 characters is required")
    item = service.reject(memory_id, principal.subject, request.comment, principal.workspace_id)
    if not item: raise HTTPException(404, "Candidate memory not found")
    return item


@router.get("/{memory_id}")
def get_memory(memory_id: str, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    item = service.get(memory_id, principal.workspace_id)
    if not item: raise HTTPException(404, "Memory not found")
    return item

@router.post("/{memory_id}/supersede")
def supersede(memory_id: str, request: MemoryAction, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    if not request.replacement_content: raise HTTPException(422, "replacement_content is required")
    result = service.supersede(memory_id, request.replacement_content, principal.subject, principal.workspace_id)
    if not result: raise HTTPException(404, "Memory not found")
    return {"previous": result[0], "candidate": result[1], "replacement_switch": "pending_approval"}
