"""Deterministic extraction of reviewer-gated reusable memory candidates.

This module deliberately does not ask an LLM to turn a single task into a
business rule.  It converts only already-approved, evidence-backed risk
patterns into a conservative episodic proposal.  A separate memory review is
still required before anything can be recalled across tasks.
"""
from __future__ import annotations


class MemoryCandidateExtractor:
    """Build one bounded episodic proposal after a decision approval.

    Atomic multi-candidate extraction is intentionally a later concern.  This
    first boundary replaces raw RiskEvent concatenation with an explainable
    eligibility gate and a durable historical-prior formulation.
    """

    _SCOPE_KEYS = ("region", "warehouse", "store", "category", "sku", "channel")
    _EVENT_LABELS = {
        "supply_disruption": "供给中断",
        "logistics_delay": "中央仓/配送延迟",
        "demand_surge": "需求激增",
        "inventory_shortage": "库存不足",
        "price_volatility": "采购或履约成本上升",
    }
    _LOW_VALUE_MARKERS = ("traceback", "exception", "tool-call", "http", "redis", "celery", "解析失败", "调用失败", "日志")

    def extract(self, task: dict) -> dict:
        """Return ``candidate`` plus auditable acceptance/rejection details."""
        decision = task.get("decision") or {}
        recommended = decision.get("recommended_strategy")
        scope = self._valid_scope(task.get("scope") or {})
        evidence_ids = {str(item.get("evidence_id")) for item in task.get("evidence", []) if item.get("evidence_id")}
        accepted_events, rejected_events = [], []
        for event in task.get("events", []):
            event_id = event.get("event_id") or "unknown-event"
            event_type = event.get("event_type")
            event_evidence = [str(item) for item in event.get("evidence_ids", []) if str(item) in evidence_ids]
            summary = str(event.get("summary") or "").lower()
            if event_type not in self._EVENT_LABELS:
                rejected_events.append({"event_id": event_id, "reason": "unsupported_event_type"})
            elif not event_evidence or len(event_evidence) != len(event.get("evidence_ids", [])):
                rejected_events.append({"event_id": event_id, "reason": "missing_verified_evidence"})
            elif any(marker in summary for marker in self._LOW_VALUE_MARKERS):
                rejected_events.append({"event_id": event_id, "reason": "operational_log_or_tool_failure"})
            else:
                accepted_events.append({**event, "verified_evidence_ids": event_evidence})

        reasons = []
        if not recommended:
            reasons.append("no_approved_recommendation")
        if not scope:
            reasons.append("missing_valid_business_scope")
        if not accepted_events:
            reasons.append("no_reusable_evidence_backed_risk_pattern")
        validation = {
            "status": "accepted" if not reasons else "rejected",
            "reasons": reasons,
            "valid_scope": scope,
            "accepted_event_ids": [event.get("event_id") for event in accepted_events],
            "rejected_events": rejected_events,
            "fact_boundary": "Temporary quantities, timestamps, raw event summaries, and tool logs are excluded from long-term memory content.",
        }
        if reasons:
            return {"candidate": None, "validation": validation}

        event_types = list(dict.fromkeys(event["event_type"] for event in accepted_events))
        labels = "、".join(self._EVENT_LABELS[event_type] for event_type in event_types)
        cited_evidence = sorted({evidence_id for event in accepted_events for evidence_id in event["verified_evidence_ids"]})
        confidence = min(float(event.get("confidence", 0.5)) for event in accepted_events)
        scope_label = "、".join(f"{key}={value}" for key, value in scope.items())
        content = (
            f"已审核补货历史案例（适用范围：{scope_label}）：当出现{labels}风险且当期证据充分时，"
            f"应比较单门店、单 SKU、单周期的三种订货策略；本次由采购负责人批准“{recommended}”。"
            "该条仅作为历史先验，后续任务必须重新核验当前库存、需求、到货与成本证据。"
        )
        validation["reusable_pattern"] = {"event_types": event_types, "approved_strategy": recommended}
        return {
            "candidate": {
                "content": content,
                "evidence_ids": cited_evidence,
                "scope": scope,
                "confidence": confidence,
                "kind": "episodic",
            },
            "validation": validation,
        }

    @classmethod
    def _valid_scope(cls, scope: dict) -> dict:
        return {
            key: str(value).strip()
            for key, value in scope.items()
            if key in cls._SCOPE_KEYS and isinstance(value, (str, int, float)) and str(value).strip()
        }
