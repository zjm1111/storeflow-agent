import json

import pytest

from app.agent.nodes.workflow import plan_research
from app.agent.state import initial_state
from app.core.config import get_settings
from app.services.llm import BailianClient, ModelCallError


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_bailian_is_disabled_without_server_credentials(monkeypatch):
    monkeypatch.delenv("BAILIAN_API_KEY", raising=False)
    monkeypatch.delenv("BAILIAN_BASE_URL", raising=False)
    get_settings.cache_clear()
    status = BailianClient().status()
    assert status["enabled"] is False
    assert status["mode"] == "deterministic-fallback"


def test_bailian_posts_openai_compatible_chat_completion(monkeypatch):
    monkeypatch.setenv("BAILIAN_API_KEY", "test-key")
    monkeypatch.setenv("BAILIAN_BASE_URL", "https://example.test/compatible-mode/v1")
    get_settings.cache_clear()
    captured = {}

    class Response:
        def __enter__(self): return self
        def __exit__(self, *_): return None
        def read(self):
            return json.dumps({"choices": [{"message": {"content": '{"objective":"Assess delivery delays","sub_questions":["What causes last-mile delays?"]}'}}], "usage": {"total_tokens": 19}}).encode()

    def fake_open(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        return Response()

    monkeypatch.setattr("app.services.llm.urlopen", fake_open)
    body, metadata = BailianClient().complete_json(system="system", user="question")
    assert captured["url"] == "https://example.test/compatible-mode/v1/chat/completions"
    assert captured["payload"]["model"] == "qwen3.7-plus"
    assert body["sub_questions"] == ["What causes last-mile delays?"]
    assert metadata["total_tokens"] == 19


def test_model_plan_is_validated_and_failure_keeps_deterministic_plan(monkeypatch):
    class FakeClient:
        class Settings:
            model_enabled = True
            model_enrichment_enabled = True
        settings = Settings()
        def complete_json(self, **_):
            raise ModelCallError("HTTP 503")
        def status(self):
            return {"provider": "bailian", "model": "qwen-plus", "mode": "remote", "enabled": True}

    monkeypatch.setattr("app.agent.nodes.workflow.BailianClient", FakeClient)
    result = plan_research(initial_state("test", "How could heavy rain delay online grocery delivery?"))
    assert result["plan"]["objective"].startswith("How could")
    assert "model plan fallback" in result["errors"][-1]
    assert result["model_execution"][-1]["success"] is False
