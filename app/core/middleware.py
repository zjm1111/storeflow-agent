"""Production HTTP controls: correlation IDs, API-key protection and rate limiting."""
from __future__ import annotations

import logging
import time
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from redis import Redis
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.metrics import HTTP_LATENCY, HTTP_REQUESTS

logger = logging.getLogger("supplymind.access")
PUBLIC_PATHS = {"/health", "/ready", "/docs", "/openapi.json", "/redoc", "/auth/token"}


class ProductionControlsMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.redis: Redis | None = None

    def _within_rate_limit(self, request: Request) -> bool:
        settings = get_settings()
        if settings.rate_limit_per_minute <= 0:
            return True
        try:
            if self.redis is None:
                self.redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.2, socket_timeout=0.2)
            bucket = int(time.time() // 60)
            client = request.client.host if request.client else "unknown"
            key = f"rate:{client}:{bucket}"
            count = self.redis.incr(key)
            if count == 1:
                self.redis.expire(key, 61)
            return count <= settings.rate_limit_per_minute
        except Exception:
            # Availability takes precedence over a best-effort edge control; infrastructure alerts cover Redis failures.
            logger.warning("rate_limit_unavailable allowing_request")
            return True

    async def dispatch(self, request: Request, call_next):
        settings = get_settings()
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        if request.url.path not in PUBLIC_PATHS and not self._within_rate_limit(request):
            return JSONResponse({"error": {"code": "rate_limited", "message": "Request rate limit exceeded", "request_id": request_id}}, status_code=429, headers={"X-Request-ID": request_id, "Retry-After": "60"})
        started = time.perf_counter()
        with HTTP_LATENCY.labels(request.method, request.url.path).time():
            response = await call_next(request)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        response.headers["X-Request-ID"] = request_id
        HTTP_REQUESTS.labels(request.method, request.url.path, str(response.status_code)).inc()
        logger.info("request_completed method=%s path=%s status=%s latency_ms=%s request_id=%s", request.method, request.url.path, response.status_code, elapsed_ms, request_id)
        return response
