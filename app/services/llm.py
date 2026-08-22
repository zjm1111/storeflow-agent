"""Small, dependency-free client for BaiLian's OpenAI-compatible endpoint."""
import json
import re
from time import perf_counter
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core import get_settings
from app.core.metrics import EXTERNAL_CALLS, EXTERNAL_FAILURES


class ModelCallError(RuntimeError):
    """A safe, user-visible description of an upstream model failure."""


def _json_object(content: str) -> dict[str, Any]:
    """Accept plain JSON or a JSON markdown fence, but nothing ambiguous."""
    stripped = content.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", stripped, flags=re.DOTALL | re.IGNORECASE)
    value = json.loads(fenced.group(1) if fenced else stripped)
    if not isinstance(value, dict):
        raise ModelCallError("model returned a JSON value other than an object")
    return value


class BailianClient:
    """Calls Chat Completions without adding an SDK dependency to the demo."""

    def __init__(self):
        self.settings = get_settings()

    def status(self) -> dict[str, Any]:
        if self.settings.model_enabled:
            return {"provider": "bailian", "model": self.settings.bailian_model, "mode": "remote", "enabled": True}
        return {
            "provider": "bailian",
            "model": self.settings.bailian_model,
            "mode": "deterministic-fallback",
            "enabled": False,
            "reason": self.settings.model_configuration_error or "BAILIAN_API_KEY is not configured",
        }

    def complete_json(self, *, system: str, user: str, max_tokens: int = 900) -> tuple[dict[str, Any], dict[str, Any]]:
        if not self.settings.model_enabled:
            raise ModelCallError(self.settings.model_configuration_error or "BaiLian model is not configured")
        endpoint = f"{self.settings.bailian_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.bailian_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": 0.1,
            "max_tokens": max_tokens,
        }
        request = Request(
            endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.settings.bailian_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        started = perf_counter()
        try:
            with EXTERNAL_CALLS.labels("bailian").time():
                with urlopen(request, timeout=self.settings.bailian_timeout_seconds) as response:
                    body = json.loads(response.read())
        except HTTPError as exc:
            EXTERNAL_FAILURES.labels("bailian", f"http_{exc.code}").inc()
            raise ModelCallError(f"BaiLian request failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            EXTERNAL_FAILURES.labels("bailian", type(exc).__name__).inc()
            raise ModelCallError(f"BaiLian request unavailable: {type(exc).__name__}") from exc
        except json.JSONDecodeError as exc:
            raise ModelCallError("BaiLian returned invalid JSON") from exc

        try:
            content = body["choices"][0]["message"]["content"]
            result = _json_object(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ModelCallError) as exc:
            raise ModelCallError(f"BaiLian response could not be validated: {exc}") from exc
        usage = body.get("usage") or {}
        total_tokens = int(usage.get("total_tokens") or 0)
        configured_rate = self.settings.bailian_cost_per_1k_tokens_usd
        metadata = {
            "provider": "bailian",
            "model": self.settings.bailian_model,
            "mode": "remote",
            "enabled": True,
            "total_tokens": total_tokens,
            "latency_ms": round((perf_counter() - started) * 1000, 1),
            "estimated_cost_usd": round(total_tokens / 1000 * configured_rate, 8),
            "cost_estimate_status": "configured_rate" if configured_rate else "rate_not_configured",
        }
        return result, metadata

    def embed_texts(self, texts: list[str]) -> tuple[list[list[float]], dict[str, Any]]:
        """Call BaiLian's OpenAI-compatible embeddings endpoint."""
        if not self.settings.model_enabled:
            raise ModelCallError(self.settings.model_configuration_error or "BaiLian embedding is not configured")
        endpoint = f"{self.settings.bailian_base_url.rstrip('/')}/embeddings"
        request = Request(endpoint, data=json.dumps({"model": self.settings.embedding_model, "input": texts, "dimensions": self.settings.embedding_dimensions, "encoding_format": "float"}).encode("utf-8"), headers={"Authorization": f"Bearer {self.settings.bailian_api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with EXTERNAL_CALLS.labels("bailian_embedding").time():
                with urlopen(request, timeout=self.settings.bailian_timeout_seconds) as response:
                    body = json.loads(response.read())
            vectors = [item["embedding"] for item in sorted(body["data"], key=lambda item: item.get("index", 0))]
            if len(vectors) != len(texts) or any(len(vector) != self.settings.embedding_dimensions for vector in vectors):
                raise ValueError("unexpected embedding count or dimensions")
        except (HTTPError, URLError, TimeoutError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            EXTERNAL_FAILURES.labels("bailian_embedding", type(exc).__name__).inc()
            raise ModelCallError(f"BaiLian embedding unavailable: {type(exc).__name__}") from exc
        return vectors, {"provider": "bailian", "model": self.settings.embedding_model, "mode": "remote", "dimensions": self.settings.embedding_dimensions, "total_tokens": int((body.get("usage") or {}).get("total_tokens") or 0)}

    def rerank(self, query: str, documents: list[str], top_n: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Use the OpenAI-compatible rerank API for qwen3-rerank."""
        if not self.settings.model_enabled:
            raise ModelCallError(self.settings.model_configuration_error or "BaiLian rerank is not configured")
        if not self.settings.bailian_rerank_base_url:
            raise ModelCallError("BAILIAN_RERANK_BASE_URL is required for qwen3-rerank (use the workspace compatible-api/v1 endpoint)")
        endpoint = f"{self.settings.bailian_rerank_base_url.rstrip('/')}/reranks"
        request = Request(endpoint, data=json.dumps({"model": self.settings.rerank_model, "query": query, "documents": documents, "top_n": min(top_n, len(documents))}).encode("utf-8"), headers={"Authorization": f"Bearer {self.settings.bailian_api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with EXTERNAL_CALLS.labels("bailian_rerank").time():
                with urlopen(request, timeout=self.settings.bailian_timeout_seconds) as response:
                    body = json.loads(response.read())
            results = body["results"]
        except HTTPError as exc:
            EXTERNAL_FAILURES.labels("bailian_rerank", f"http_{exc.code}").inc()
            try:
                raw_detail = exc.read().decode("utf-8", "replace")
                payload = json.loads(raw_detail)
                detail = f"{payload.get('code', 'upstream error')}: {payload.get('message', '')}"[:500]
            except Exception:
                detail = ""
            raise ModelCallError(f"BaiLian rerank failed with HTTP {exc.code}: {detail}") from exc
        except (URLError, TimeoutError, KeyError, TypeError, json.JSONDecodeError) as exc:
            EXTERNAL_FAILURES.labels("bailian_rerank", type(exc).__name__).inc()
            raise ModelCallError(f"BaiLian rerank unavailable: {type(exc).__name__}") from exc
        return results, {"provider": "bailian", "model": self.settings.rerank_model, "mode": "remote", "total_tokens": 0}
