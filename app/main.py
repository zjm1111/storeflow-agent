from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware

from app.api.tasks import router as tasks_router, service as task_service
from app.api.memories import router as memories_router
from app.core import get_settings
from app.core.middleware import ProductionControlsMiddleware
from app.core.auth import TokenRequest, issue_token
from app.core.metrics import render_metrics
from app.repositories.database import create_tables

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    # SQLite exists solely for isolated tests; MySQL schema creation is owned
    # by `alembic upgrade head` before Uvicorn starts.
    if settings.mysql_url == "sqlite://":
        create_tables()
    yield


app = FastAPI(title=settings.app_name, version="0.2.0", lifespan=lifespan)
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s %(message)s")
app.add_middleware(ProductionControlsMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(tasks_router)
app.include_router(memories_router)


@app.post("/auth/token", tags=["auth"])
def login(request: TokenRequest):
    """Issue a short-lived demo JWT from configured local accounts."""
    return issue_token(request)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "api",
        "infrastructure": {"mysql": "configured", "redis": "configured", "qdrant": "configured", "events": task_service.events.persistence_mode},
    }


@app.get("/ready")
def ready():
    """Readiness verifies that task persistence is reachable before traffic is accepted."""
    from sqlalchemy import text
    from app.repositories.database import engine

    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="task persistence is unavailable") from exc
    return {"status": "ready"}


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Prometheus scrape endpoint: API plus request/dependency health."""
    body, media_type = render_metrics()
    return Response(body, media_type=media_type)
