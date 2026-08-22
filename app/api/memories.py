from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.services.memory import MemoryService
from app.core.auth import Principal, require_roles

router = APIRouter(prefix="/memories", tags=["memory"])
service = MemoryService()

class MemoryAction(BaseModel):
    # Retained for old clients, but never trusted: the JWT subject is recorded.
    reviewer: str | None = Field(default=None, min_length=2, max_length=120)
    replacement_content: str | None = Field(default=None, max_length=4000)

@router.post("/{memory_id}/approve")
def approve(memory_id: str, request: MemoryAction, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    item = service.approve(memory_id, principal.subject, principal.workspace_id)
    if not item: raise HTTPException(404, "Candidate memory not found")
    return item

@router.post("/{memory_id}/expire")
def expire(memory_id: str, request: MemoryAction, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    item = service.expire(memory_id, principal.subject, principal.workspace_id)
    if not item: raise HTTPException(404, "Memory not found")
    return item

@router.post("/{memory_id}/supersede")
def supersede(memory_id: str, request: MemoryAction, principal: Principal = Depends(require_roles("reviewer", "admin"))):
    if not request.replacement_content: raise HTTPException(422, "replacement_content is required")
    result = service.supersede(memory_id, request.replacement_content, principal.subject, principal.workspace_id)
    if not result: raise HTTPException(404, "Memory not found")
    return {"superseded": result[0], "candidate": result[1]}
