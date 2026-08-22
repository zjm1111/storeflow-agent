from app.services.retrieval import _local_rerank_score


def test_local_reranker_prefers_query_coverage_and_authoritative_fresh_source():
    query = "heavy rain delivery delay"
    relevant = {"content": "Heavy rain causes last-mile delivery delay for grocery orders.", "source_tier": "official", "retrieved_at": "2026-08-21T00:00:00+00:00"}
    unrelated = {"content": "Summer cooking recipes and family meals.", "source_tier": "web", "retrieved_at": "2025-01-01T00:00:00+00:00"}
    relevant_score, factors = _local_rerank_score(query, relevant, 0.5)
    unrelated_score, _ = _local_rerank_score(query, unrelated, 0.5)
    assert relevant_score > unrelated_score
    assert factors["coverage"] > 0
    assert factors["authority"] == 1.0
