import os

os.environ["MYSQL_URL"] = "sqlite://"

from app.services.context import build_context_pack


def test_context_pack_keeps_citations_diversity_and_top_eight():
    evidence = [
        {
            "evidence_id": f"ev-{index}", "source_id": f"source-{index % 10}",
            "event_id": f"event-{index % 3}", "quote": f"Evidence {index} about delivery delay. " * 30,
            "overall_score": 1 - index / 100,
        }
        for index in range(12)
    ]
    pack = build_context_pack(evidence, budget_tokens=10_000)
    assert len(pack["items"]) == 8
    assert all(item["summary"].startswith(f"[证据: {item['evidence_id']}]") for item in pack["items"])
    assert pack["selection"]["source_count"] >= 8
    assert pack["used_tokens"] <= pack["budget_tokens"]


def test_context_pack_respects_hard_token_budget():
    evidence = [{"evidence_id": "ev-1", "source_id": "s-1", "quote": "x" * 500, "overall_score": 1}]
    pack = build_context_pack(evidence, budget_tokens=10)
    assert pack["items"] == []
    assert pack["used_tokens"] == 0
