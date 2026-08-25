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


def test_extractor_creates_reusable_evidence_backed_prior_without_raw_temporary_facts():
    result = MemoryCandidateExtractor().extract(_approved_task())

    candidate = result["candidate"]
    assert result["validation"]["status"] == "accepted"
    assert candidate["kind"] == "episodic"
    assert candidate["scope"] == {"region": "上海", "store": "浦东门店", "sku": "drink-001"}
    assert candidate["evidence_ids"] == ["ev-delay", "ev-inventory"]
    assert candidate["confidence"] == 0.74
    assert "中央仓/配送延迟、库存不足" in candidate["content"]
    assert "适度加订" in candidate["content"]
    # Current incident quantities and the raw event summaries remain Evidence,
    # never copied into reusable long-term-memory content.
    assert "12 小时" not in candidate["content"]
    assert "1.5 天" not in candidate["content"]


def test_extractor_rejects_missing_scope_or_unverified_evidence():
    task = _approved_task()
    task["scope"] = {}
    task["events"][0]["evidence_ids"] = ["not-in-task-evidence"]

    result = MemoryCandidateExtractor().extract(task)

    assert result["candidate"] is None
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
    assert result["validation"]["reasons"] == ["no_reusable_evidence_backed_risk_pattern"]
    assert result["validation"]["rejected_events"] == [{"event_id": "risk-log", "reason": "operational_log_or_tool_failure"}]
