import json

import pytest

from app.core.config import get_settings
from app.services import retrieval
from app.services.retrieval import HybridRetriever


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_tavily_is_skipped_cleanly_without_a_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    sources, errors = HybridRetriever()._tavily_search("heavy rain delivery delay")
    assert sources == []
    assert errors == []


def test_tavily_news_results_become_traceable_sources(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-test")
    captured = {}

    class Response:
        def read(self):
            return json.dumps({"results": [{
                "url": "https://93.184.216.34/delivery-rain",
                "title": "Heavy rain delays last-mile delivery",
                "content": "Heavy rain caused delivery delays and logistics disruption for e-commerce orders.",
            }]}).encode()

    def fake_open(request, timeout):
        captured["payload"] = json.loads(request.data)
        captured["authorization"] = request.get_header("Authorization")
        return Response()

    monkeypatch.setattr("app.services.retrieval.urlopen", fake_open)
    sources, errors = HybridRetriever()._tavily_search("heavy rain delivery delay")
    assert errors == []
    assert sources[0]["source_id"].startswith("tavily-")
    assert sources[0]["url"] == "https://93.184.216.34/delivery-rain"
    assert captured["payload"]["topic"] == "news"
    assert captured["payload"]["time_range"] == "week"
    assert captured["authorization"] == "Bearer tvly-test"


def test_public_documents_are_ranked_as_ephemeral_chunks_without_qdrant(monkeypatch):
    client = HybridRetriever.__new__(HybridRetriever)
    client.tavily_api_key = "tvly-test"
    long_news = "\n\n".join(
        f"第 {index} 段：暴雨造成道路积水和中央仓配送延迟，门店需要复核饮料安全库存与周末促销需求。" * 10
        for index in range(8)
    )
    monkeypatch.setattr(client, "_tavily_search", lambda _query: ([{
        "source_id": "tavily-news-1", "title": "暴雨配送风险", "url": "https://example.com/rain",
        "content": long_news, "content_hash": "news-hash", "retrieved_at": "2026-08-24T00:00:00+00:00", "source_type": "web",
    }], []))
    monkeypatch.setattr(client, "_search", lambda _query: [])
    monkeypatch.setattr(client, "_embed", lambda texts: ([retrieval._vector(text) for text in texts], None))
    monkeypatch.setattr(client, "_qdrant", lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("public chunks must not enter Qdrant")))
    monkeypatch.setattr(client, "_rerank", lambda _query, _sources, ranked, _errors: ranked[:8])

    sources, ranked, errors = client.retrieve("暴雨导致中央仓配送延迟")

    assert errors == []
    assert len(sources) > 1
    assert all(item["retrieval_unit"] == "public_chunk" for item in sources)
    assert all(item["document_id"] == "tavily-news-1" for item in sources)
    assert all(item["source_id"].startswith("tavily-news-1-chunk-") for item in sources)
    assert {item["source_id"] for item in ranked}.issubset({item["source_id"] for item in sources})
