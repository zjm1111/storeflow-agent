from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator


class ResearchPlan(BaseModel):
    objective: str = Field(min_length=5)
    sub_questions: list[str] = Field(min_length=1, max_length=5)

    @field_validator("sub_questions")
    @classmethod
    def non_empty_questions(cls, values: list[str]) -> list[str]:
        if any(not item.strip() for item in values):
            raise ValueError("sub_questions cannot contain blanks")
        return values


class AgentAction(BaseModel):
    """A bounded tool decision, deliberately excluding chain-of-thought."""
    tool: Literal[
        "retrieve_evidence", "assess_evidence_gap", "run_decision_analysis",
        "request_human_review", "finish",
    ]
    reason: str = Field(min_length=3, max_length=240)


class FulfillmentScope(BaseModel):
    """Bounded StoreFlow context; ``warehouse`` means the central warehouse."""
    region: str | None = Field(default=None, max_length=80)
    warehouse: str | None = Field(default=None, max_length=80)
    store: str | None = Field(default=None, max_length=80)
    category: str | None = Field(default=None, max_length=80)
    sku: str | None = Field(default=None, max_length=80)
    channel: str | None = Field(default=None, max_length=80)
    time_window: Literal["day", "week", "month"] = "week"
    risk_topics: list[str] = Field(default_factory=list, max_length=8)


class Source(BaseModel):
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: HttpUrl
    content: str = Field(min_length=1)
    retrieved_at: datetime
    source_type: Literal["fixture", "web", "pdf", "internal"] = "fixture"
    content_hash: str | None = None
    document_id: str | None = None
    published_at: datetime | None = None
    region: str | None = None
    warehouse: str | None = None
    channel: str | None = None
    source_tier: Literal["official", "internal", "news", "web", "fixture"] = "web"
    expires_at: datetime | None = None
    # Internal PDF retrieval stores individual child chunks.  These fields are
    # carried through task state so Evidence can still point to the exact page
    # and character range rather than being re-split later in the workflow.
    chunk_index: int | None = Field(default=None, ge=0)
    page_number: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    retrieval_unit: str | None = None
    parent_id: str | None = None
    parent_content: str | None = None
    parent_char_start: int | None = Field(default=None, ge=0)
    parent_char_end: int | None = Field(default=None, ge=0)


class EvidenceSnippet(BaseModel):
    evidence_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    source_type: Literal["fixture", "web", "pdf", "internal"] = "fixture"
    source_uri: str | None = None
    quote: str = Field(min_length=1)
    relevance_score: float = Field(ge=0, le=1)
    authority_score: float = Field(ge=0, le=1)
    freshness_score: float = Field(ge=0, le=1)
    overall_score: float = Field(ge=0, le=1)
    chunk_index: int = Field(ge=0)
    document_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    freshness_note: str | None = None
    consistency_score: float = Field(default=1.0, ge=0, le=1)
    conflict_group: str | None = None
    conflict_status: Literal["none", "pending_review"] = "none"
    # The exact child quote remains the citation.  This optional, bounded
    # parent window supplies surrounding procedural context to the model.
    parent_id: str | None = None
    context_quote: str | None = None


class RiskEvent(BaseModel):
    event_id: str = Field(min_length=1)
    event_type: Literal["supply_disruption", "logistics_delay", "demand_surge", "inventory_shortage", "price_volatility"]
    summary: str = Field(min_length=1)
    affected_entity: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    evidence_ids: list[str] = Field(min_length=1)
    source_ids: list[str] = Field(min_length=1)
    severity: Literal["low", "medium", "high"]


class CitedReport(BaseModel):
    markdown: str = Field(min_length=1)
    citation_evidence_ids: list[str]


class NodeEvent(BaseModel):
    node: str
    status: Literal["started", "completed", "error"]
    timestamp: datetime
    message: str | None = None
