import json

import pytest

from app.core.config import get_settings
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
