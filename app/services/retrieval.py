"""Keyless web/PDF retrieval with Redis cache, Qdrant vectors and BM25 fusion."""
import hashlib
import math
import re
import base64
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from urllib.parse import parse_qs, quote_plus, urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup
from pypdf import PdfReader
from redis import Redis

from app.core import get_settings
from app.core.external_fetch import ExternalUrlBlocked, read_public_url, validate_public_url
from app.core.metrics import EXTERNAL_CALLS, EXTERNAL_FAILURES
from app.services.llm import BailianClient, ModelCallError

COLLECTION = "supplymind_knowledge"


def _tokens(value: str) -> list[str]: return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", value.lower())
def _vector(value: str, size: int = 64) -> list[float]:
    values = [0.0] * size
    for token in _tokens(value): values[int(hashlib.sha256(token.encode()).hexdigest(), 16) % size] += 1
    norm = math.sqrt(sum(v * v for v in values)) or 1
    return [v / norm for v in values]


def _bm25_scores(query: str, corpus: list[list[str]]) -> list[float]:
    """Small dependency-free BM25 implementation for the first knowledge-base slice."""
    if not corpus:
        return []
    query_tokens = _tokens(query)
    document_frequency = Counter(token for document in corpus for token in set(document))
    average_length = sum(len(document) for document in corpus) / len(corpus) or 1
    scores = []
    for document in corpus:
        counts = Counter(document)
        score = 0.0
        for token in query_tokens:
            frequency = counts[token]
            if not frequency:
                continue
            inverse_frequency = math.log(1 + (len(corpus) - document_frequency[token] + 0.5) / (document_frequency[token] + 0.5))
            score += inverse_frequency * frequency * 2.5 / (frequency + 1.5 * (1 - 0.75 + 0.75 * len(document) / average_length))
        scores.append(score)
    return scores


def rrf_fuse_lanes(lanes: dict[str, list[dict]], *, k: int = 60) -> list[dict]:
    """Fuse independently ranked candidate lanes without hiding their origins.

    Candidate IDs may represent evidence sources or approved memory. A caller
    keeps non-evidence memory out of the model evidence pack, while still
    exposing its rank in the retrieval funnel.
    """
    fused: dict[str, dict] = {}
    for lane, candidates in lanes.items():
        for rank, candidate in enumerate(candidates, 1):
            candidate_id = str(candidate.get("candidate_id") or candidate.get("source_id") or candidate.get("memory_id"))
            if not candidate_id:
                continue
            current = fused.setdefault(candidate_id, {**candidate, "candidate_id": candidate_id, "rrf_score": 0.0, "rrf_lanes": []})
            current["rrf_score"] += 1 / (k + rank)
            current["rrf_lanes"].append(lane)
    return sorted(({**item, "rrf_score": round(float(item["rrf_score"]), 6)} for item in fused.values()), key=lambda item: item["rrf_score"], reverse=True)


def _is_relevant(query: str, content: str) -> bool:
    """Reject search-engine noise before it can become evidence."""
    query_terms = set(_tokens(query))
    content_terms = set(_tokens(content))
    # One token can be a common word or a single Chinese character; require two signals.
    return len(query_terms.intersection(content_terms)) >= 2


def _is_low_value_source(url: str) -> bool:
    """Reject pages that routinely create lexical, not operational, matches."""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    blocked_hosts = ("dictionary.", "github.com", "gist.github.com", "stackoverflow.com")
    blocked_paths = ("/flash-deals", "/deals/", "/search")
    return host.startswith(blocked_hosts) or any(segment in path for segment in blocked_paths)


def _is_safe_external_url(url: str) -> bool:
    """SSRF boundary for URLs produced by search engines or external APIs."""
    try:
        validate_public_url(url)
        return True
    except ExternalUrlBlocked:
        return False


def _freshness_score(value: str | None) -> float:
    if not value:
        return 0.55
    try:
        age_days = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(value.replace("Z", "+00:00"))).days)
        return max(0.15, round(1 / (1 + age_days / 14), 3))
    except (TypeError, ValueError):
        return 0.55


def _local_rerank_score(query: str, source: dict, fused_score: float) -> tuple[float, dict]:
    """Free, inspectable reranker—not presented as a cross-encoder model."""
    query_tokens = set(_tokens(query))
    content_tokens = _tokens(source.get("content", ""))
    content_set = set(content_tokens)
    coverage = len(query_tokens & content_set) / max(1, len(query_tokens))
    normalized_content = " ".join(content_tokens)
    phrase = 1.0 if len(query_tokens) > 1 and " ".join(_tokens(query)) in normalized_content else 0.0
    authority = {"official": 1.0, "internal": 0.9, "news": 0.75, "pdf": 0.85, "web": 0.55, "fixture": 0.5}.get(source.get("source_tier") or source.get("source_type"), 0.5)
    freshness = _freshness_score(source.get("published_at") or source.get("retrieved_at"))
    score = 0.45 * fused_score + 0.25 * coverage + 0.10 * phrase + 0.12 * authority + 0.08 * freshness
    return round(score, 3), {"fused": round(fused_score, 3), "coverage": round(coverage, 3), "phrase": phrase, "authority": authority, "freshness": freshness}


def _bing_target(url: str) -> str:
    encoded = parse_qs(urlparse(url).query).get("u", [""])[0]
    if not encoded.startswith("a1"):
        return url
    try:
        value = encoded[2:]
        return base64.b64decode(value + "=" * (-len(value) % 4)).decode("utf-8")
    except Exception:
        return url


class HybridRetriever:
    def __init__(self):
        settings = get_settings()
        self.redis = Redis.from_url(settings.redis_url, decode_responses=False, socket_connect_timeout=1)
        self.qdrant_url = settings.qdrant_url.rstrip("/")
        self.tavily_api_key = settings.tavily_api_key
        self.tavily_max_results = settings.tavily_max_results
        self.tavily_time_range = settings.tavily_time_range
        self.embedding_dimensions = settings.embedding_dimensions
        # Keep legacy 64-d vectors readable during migration; remote vectors get
        # a new collection so Qdrant never mixes incompatible dimensions.
        self.collection = f"{COLLECTION}_v2_{self.embedding_dimensions}" if settings.model_enabled else COLLECTION

    def _embed(self, texts: list[str]) -> tuple[list[list[float]], str | None]:
        client = BailianClient()
        if not client.settings.model_enabled:
            return [_vector(text) for text in texts], "embedding fallback: BAILIAN_API_KEY is not configured"
        try:
            vectors, _ = client.embed_texts(texts)
            return vectors, None
        except ModelCallError as exc:
            return [_vector(text) for text in texts], f"embedding fallback: {exc}"

    def _qdrant(self, path: str, body: dict | None = None, method: str = "POST") -> dict:
        data = None if body is None else __import__("json").dumps(body).encode()
        request = Request(f"{self.qdrant_url}{path}", data=data, headers={"Content-Type": "application/json"}, method=method)
        return __import__("json").loads(urlopen(request, timeout=3).read())

    def _search(self, query: str) -> list[str]:
        urls = []
        headers = {"User-Agent": "Mozilla/5.0 (compatible; StoreFlow/1.0)"}
        try:
            html = urlopen(Request(f"https://html.duckduckgo.com/html/?q={quote_plus(query)}", headers=headers), timeout=8).read().decode("utf-8", "ignore")
            for link in BeautifulSoup(html, "html.parser").select(".result__a"):
                href = link.get("href")
                redirected = parse_qs(urlparse(href or "").query).get("uddg", [None])[0]
                if (redirected or href or "").startswith(("http://", "https://")):
                    urls.append(redirected or href)
        except Exception:
            pass
        if not urls:
            html = urlopen(Request(f"https://www.bing.com/search?q={quote_plus(query)}", headers=headers), timeout=8).read().decode("utf-8", "ignore")
            urls.extend(_bing_target(link.get("href") or "") for link in BeautifulSoup(html, "html.parser").select("li.b_algo h2 a") if (link.get("href") or "").startswith(("http://", "https://")))
        return list(dict.fromkeys(urls))[:3]

    def _tavily_search(self, query: str) -> tuple[list[dict], list[str]]:
        """Fetch recent, already-extracted news evidence when Tavily is configured."""
        if not self.tavily_api_key:
            return [], []
        payload = {
            "query": f"{query} retail store replenishment central warehouse delivery risk weather traffic disruption",
            "topic": "news",
            "time_range": self.tavily_time_range,
            "search_depth": "basic",
            "max_results": self.tavily_max_results,
            "include_answer": False,
            "include_raw_content": "text",
        }
        request = Request(
            "https://api.tavily.com/search",
            data=__import__("json").dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.tavily_api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with EXTERNAL_CALLS.labels("tavily").time():
                response = __import__("json").loads(urlopen(request, timeout=12).read())
        except Exception as exc:
            EXTERNAL_FAILURES.labels("tavily", type(exc).__name__).inc()
            return [], [f"Tavily news search unavailable: {type(exc).__name__}"]

        sources, seen_hashes = [], set()
        for result in response.get("results", []):
            url = result.get("url")
            content = (result.get("raw_content") or result.get("content") or "").strip()[:12000]
            if not isinstance(url, str) or not _is_safe_external_url(url) or not content or _is_low_value_source(url) or not _is_relevant(query, content):
                continue
            content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)
            sources.append({
                "source_id": f"tavily-{hashlib.sha1(url.encode()).hexdigest()[:12]}",
                "title": str(result.get("title") or url)[:500],
                "url": url,
                "content": content,
                "content_hash": content_hash,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "source_type": "web",
            })
        return sources, []

    def _content(self, url: str) -> str:
        key = f"url:{hashlib.sha256(url.encode()).hexdigest()}"
        try:
            cached = self.redis.get(key)
            if cached: return cached.decode("utf-8")
        except Exception: pass
        try:
            with EXTERNAL_CALLS.labels("public_web").time():
                raw = read_public_url(url, timeout=10)
        except Exception as exc:
            EXTERNAL_FAILURES.labels("public_web", type(exc).__name__).inc()
            raise
        if url.lower().endswith(".pdf") or raw.startswith(b"%PDF"):
            text = " ".join(page.extract_text() or "" for page in PdfReader(BytesIO(raw)).pages)
        else:
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]): tag.decompose()
            text = soup.get_text(" ", strip=True)
        text = text[:12000]
        try: self.redis.setex(key, 3600, text.encode("utf-8"))
        except Exception: pass
        return text

    def _ensure_collection(self) -> None:
        try:
            self._qdrant(f"/collections/{getattr(self, 'collection', COLLECTION)}", method="GET")
        except Exception:
            size = self.embedding_dimensions if get_settings().model_enabled else 64
            self._qdrant(f"/collections/{getattr(self, 'collection', COLLECTION)}", {"vectors": {"size": size, "distance": "Cosine"}}, "PUT")

    def _rank(self, query: str, sources: list[dict], vectors: dict[str, float]) -> list[dict]:
        """Fuse lexical and vector rankings with reciprocal-rank fusion.

        Scores remain visible in the API so the UI can explain why a source was
        selected; the final cross-encoder/local reranker is still applied later.
        """
        bm25 = _bm25_scores(query, [_tokens(item["content"]) for item in sources])
        maximum = max(bm25) or 1
        results = [{"source_id": item["source_id"], "title": item["title"], "url": item["url"], "bm25_score": round(float(bm25[i] / maximum), 3), "vector_score": round(float(vectors.get(item["source_id"], 0)), 3)} for i, item in enumerate(sources)]
        bm25_ranks = {item["source_id"]: index + 1 for index, item in enumerate(sorted(results, key=lambda item: item["bm25_score"], reverse=True))}
        vector_ranks = {item["source_id"]: index + 1 for index, item in enumerate(sorted(results, key=lambda item: item["vector_score"], reverse=True))}
        rrf_k = 60
        for result in results:
            result["rrf_score"] = round(1 / (rrf_k + bm25_ranks[result["source_id"]]) + 1 / (rrf_k + vector_ranks[result["source_id"]]), 6)
            # ``rerank_score`` is a compatibility alias consumed by the UI.
            result["rerank_score"] = result["rrf_score"]
        return sorted(results, key=lambda item: item["rerank_score"], reverse=True)

    def _rerank(self, query: str, sources: list[dict], ranked: list[dict], errors: list[str]) -> list[dict]:
        """Final Top-8 defaults to an explainable free local reranker."""
        candidates = ranked[:get_settings().rag_candidate_limit]
        if not candidates:
            return []
        by_id = {item["source_id"]: item for item in sources}
        if get_settings().rerank_provider.lower() != "bailian":
            reranked, represented = [], set()
            for item in candidates:
                source = by_id[item["source_id"]]
                duplicate_penalty = 0.08 if source.get("content_hash") in represented else 0.0
                score, factors = _local_rerank_score(query, source, item["rerank_score"])
                represented.add(source.get("content_hash"))
                reranked.append({**item, "rerank_score": round(score - duplicate_penalty, 3), "rerank_provider": "local-explainable", "rerank_factors": {**factors, "duplicate_penalty": duplicate_penalty}})
            return sorted(reranked, key=lambda item: item["rerank_score"], reverse=True)[:get_settings().rag_final_limit]
        if not get_settings().model_enabled:
            errors.append("rerank fallback: BaiLian model is not configured")
            return candidates[:get_settings().rag_final_limit]
        try:
            response, _ = BailianClient().rerank(query, [by_id[item["source_id"]]["content"][:4000] for item in candidates], get_settings().rag_final_limit)
            result = []
            for item in response:
                index = item.get("index")
                if isinstance(index, int) and 0 <= index < len(candidates):
                    candidate = {**candidates[index], "rerank_score": round(float(item.get("relevance_score", candidates[index].get("rerank_score", 0))), 3), "rerank_provider": get_settings().rerank_model}
                    result.append(candidate)
            return result or candidates[:get_settings().rag_final_limit]
        except (ModelCallError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"rerank fallback: {exc}")
            return candidates[:get_settings().rag_final_limit]

    def retrieve(self, query: str) -> tuple[list[dict], list[dict], list[str]]:
        errors: list[str] = []
        tavily_sources, tavily_errors = self._tavily_search(query)
        errors.extend(tavily_errors)
        # Tavily is the approved live search path. Keyless mode must stay fast,
        # deterministic and offline-friendly for demos/tests.
        if self.tavily_api_key:
            try:
                urls = list(dict.fromkeys(self._search(query)))
            except Exception as exc:
                urls = []
                errors.append(f"public search failed: {type(exc).__name__}: {exc}")
        else:
            urls = []
        sources = [*tavily_sources]
        content_hashes = {item["content_hash"] for item in sources}
        for index, url in enumerate(urls):
            if not _is_safe_external_url(url):
                errors.append(f"unsafe URL skipped: {url}")
                continue
            if urlparse(url).netloc.endswith("bing.com"):
                errors.append(f"search redirect skipped: {url}")
                continue
            if _is_low_value_source(url):
                errors.append(f"low-value source skipped: {url}")
                continue
            try:
                text = self._content(url)
            except Exception as exc:
                errors.append(f"parse failed for {url}: {type(exc).__name__}: {exc}")
                continue
            content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if not text or content_hash in content_hashes:
                errors.append(f"duplicate or empty content skipped: {url}")
                continue
            if not _is_relevant(query, text):
                errors.append(f"low relevance source skipped: {url}")
                continue
            content_hashes.add(content_hash)
            is_pdf = url.lower().endswith(".pdf")
            sources.append({"source_id": f"web-{hashlib.sha1(url.encode()).hexdigest()[:12]}", "title": url, "url": url, "content": text, "content_hash": content_hash, "retrieved_at": datetime.now(timezone.utc).isoformat(), "source_type": "pdf" if is_pdf else "web"})
        if not sources: return [], [], errors or ["search returned no parsable sources"]
        try:
            self._ensure_collection()
            vectors_to_upsert, embedding_error = self._embed([item["content"] for item in sources])
            if embedding_error: errors.append(embedding_error)
            query_vector, query_error = self._embed([query])
            if query_error and query_error not in errors: errors.append(query_error)
            points = [{"id": int(hashlib.sha1(item["source_id"].encode()).hexdigest()[:12], 16), "vector": vectors_to_upsert[index], "payload": {key: item[key] for key in ("source_id", "title", "url", "content")}} for index, item in enumerate(sources)]
            self._qdrant(f"/collections/{getattr(self, 'collection', COLLECTION)}/points?wait=true", {"points": points}, "PUT")
            response = self._qdrant(f"/collections/{getattr(self, 'collection', COLLECTION)}/points/search", {"vector": query_vector[0], "limit": len(sources), "with_payload": True})
            vectors = {point["payload"]["source_id"]: point["score"] for point in response["result"]}
        except Exception: vectors = {}
        return sources, self._rerank(query, sources, self._rank(query, sources, vectors), errors), errors

    def _knowledge_sources(self) -> list[dict]:
        """Return the stored internal documents, including legacy payloads."""
        self._ensure_collection()
        scroll = self._qdrant(f"/collections/{getattr(self, 'collection', COLLECTION)}/points/scroll", {"limit": 100, "with_payload": True})
        sources = []
        for point in scroll["result"]["points"]:
            payload = point["payload"]
            if not all(key in payload for key in ("source_id", "title", "url", "content")):
                continue
            # Payloads created before explicit upload metadata remain queryable.
            sources.append({
                **payload,
                "content_hash": payload.get("content_hash"),
                "retrieved_at": payload.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
                "source_type": payload.get("source_type", "internal"),
            })
        return sources

    def _knowledge_results(self, query: str, limit: int = 5) -> tuple[list[dict], list[dict]]:
        try:
            sources = self._knowledge_sources()
            if not sources:
                return [], []
            query_vector, _ = self._embed([query])
            response = self._qdrant(f"/collections/{getattr(self, 'collection', COLLECTION)}/points/search", {"vector": query_vector[0], "limit": len(sources), "with_payload": True})
            vectors = {point["payload"]["source_id"]: point["score"] for point in response["result"]}
            ranked = self._rerank(query, sources, self._rank(query, sources, vectors), [])[:limit]
            by_id = {source["source_id"]: source for source in sources}
            return [by_id[item["source_id"]] for item in ranked], ranked
        except Exception:
            return [], []

    def search_knowledge(self, query: str, limit: int = 5) -> list[dict]:
        _, results = self._knowledge_results(query, limit)
        return results

    def retrieve_knowledge(self, query: str, limit: int = 5) -> tuple[list[dict], list[dict]]:
        """Expose internal PDF sources for the Agent retrieval node."""
        return self._knowledge_results(query, limit)

    def ingest_pdf(self, filename: str, raw: bytes) -> dict:
        if not raw.startswith(b"%PDF"):
            raise ValueError("Uploaded file is not a PDF")
        text = " ".join(page.extract_text() or "" for page in PdfReader(BytesIO(raw)).pages).strip()[:12000]
        if not text:
            raise ValueError("No extractable text found in PDF")
        content_hash = hashlib.sha256(raw).hexdigest()
        source_id = f"upload-{content_hash[:12]}"
        source = {
            "source_id": source_id,
            "title": filename,
            "url": f"https://local.storeflow/uploads/{source_id}",
            "content": text,
            "content_hash": content_hash,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_type": "internal",
        }
        self._ensure_collection()
        try:
            existing = self._qdrant(f"/collections/{COLLECTION}/points/{int(hashlib.sha1(source_id.encode()).hexdigest()[:12], 16)}", method="GET")
            if existing.get("result"):
                return {**source, "duplicate": True}
        except Exception:
            # A missing point is reported by Qdrant as 404; it is safe to insert it.
            pass
        vectors, _ = self._embed([text])
        point = {"id": int(hashlib.sha1(source_id.encode()).hexdigest()[:12], 16), "vector": vectors[0], "payload": {key: source[key] for key in ("source_id", "title", "url", "content", "content_hash", "retrieved_at", "source_type")}}
        self._qdrant(f"/collections/{getattr(self, 'collection', COLLECTION)}/points?wait=true", {"points": [point]}, "PUT")
        return {**source, "duplicate": False}
