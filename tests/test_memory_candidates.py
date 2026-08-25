from app.services.memory_candidates import MemoryCandidateExtractor


def _approved_task() -> dict:
    return {
        "scope": {"region": "上海", "store": "浦东门店", "sku": "drink-001", "unknown": "ignored"},
        "decision": {"recommended_strategy": "适度加订"},
        "evidence": [{"evidence_id": "ev-delay"}, {"evidence_id": "ev-inventory"}],
        "events": [
            {
                "event_id": "risk-delay", "event_type": "logistics_delay", "summary": "中央仓预计晚到 12 小时",
                "confidence": 0.82, "evidence_ids": ["ev-delay"],
            },
            {
                "event_id": "risk-stock", "event_type": "inventory_shortage", "summary": "门店当前库存仅够 1.5 天",
                "confidence": 0.74, "evidence_ids": ["ev-inventory"],
            },
        ],
    }


def test_extractor_creates_atomic_reusable_priors_without_raw_temporary_facts():
    result = MemoryCandidateExtractor().extract(_approved_task())

    candidate = result["candidate"]
    assert result["validation"]["status"] == "accepted"
    assert len(result["candidates"]) == 2
    assert [item["risk_dimension"] for item in result["candidates"]] == ["logistics_delay", "inventory_shortage"]
    assert candidate["kind"] == "episodic"
    assert candidate["scope"] == {"region": "上海", "store": "浦东门店", "sku": "drink-001"}
    assert candidate["evidence_ids"] == ["ev-delay"]
    assert candidate["confidence"] == 0.82
    assert "中央仓/配送延迟" in candidate["content"]
    assert "适度加订" in candidate["content"]
    # Current incident quantities and the raw event summaries remain Evidence,
    # never copied into reusable long-term-memory content.
    assert "12 小时" not in candidate["content"]
    assert "1.5 天" not in candidate["content"]


def test_extractor_caps_atomic_candidates_at_three_deterministically():
    task = _approved_task()
    task["evidence"].extend([
        {"evidence_id": "ev-demand"}, {"evidence_id": "ev-cost"}, {"evidence_id": "ev-supply"},
    ])
    task["events"].extend([
        {"event_id": "risk-demand", "event_type": "demand_surge", "summary": "促销需求上升", "confidence": 0.8, "evidence_ids": ["ev-demand"]},
        {"event_id": "risk-cost", "event_type": "price_volatility", "summary": "配送价格上升", "confidence": 0.8, "evidence_ids": ["ev-cost"]},
        {"event_id": "risk-supply", "event_type": "supply_disruption", "summary": "供应到仓受阻", "confidence": 0.8, "evidence_ids": ["ev-supply"]},
    ])

    result = MemoryCandidateExtractor().extract(task)

    assert [item["risk_dimension"] for item in result["candidates"]] == ["logistics_delay", "inventory_shortage", "demand_surge"]
    assert result["validation"]["candidate_count"] == 3
    assert result["validation"]["omitted_event_types_due_to_limit"] == ["price_volatility", "supply_disruption"]


def test_extractor_rejects_missing_scope_or_unverified_evidence():
    task = _approved_task()
    task["scope"] = {}
    task["events"][0]["evidence_ids"] = ["not-in-task-evidence"]

    result = MemoryCandidateExtractor().extract(task)

    assert result["candidate"] is None
    assert result["candidates"] == []
    assert result["validation"]["status"] == "rejected"
    assert "missing_valid_business_scope" in result["validation"]["reasons"]
    assert "risk-delay" in {item["event_id"] for item in result["validation"]["rejected_events"]}


def test_extractor_rejects_operational_logs_even_when_they_claim_evidence():
    task = _approved_task()
    task["events"] = [{
        "event_id": "risk-log", "event_type": "logistics_delay", "summary": "Celery 调用失败日志",
        "confidence": 0.8, "evidence_ids": ["ev-delay"],
    }]

    result = MemoryCandidateExtractor().extract(task)

    assert result["candidate"] is None
    assert result["candidates"] == []
    assert result["validation"]["reasons"] == ["no_reusable_evidence_backed_risk_pattern"]
    assert result["validation"]["rejected_events"] == [{"event_id": "risk-log", "reason": "operational_log_or_tool_failure"}]
