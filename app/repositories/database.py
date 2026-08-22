from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core import get_settings


class Base(DeclarativeBase):
    pass


def _engine_options(url: str) -> dict:
    if url == "sqlite://":
        return {"connect_args": {"check_same_thread": False}, "poolclass": StaticPool}
    return {"pool_pre_ping": True}


engine = create_engine(get_settings().mysql_url, **_engine_options(get_settings().mysql_url))
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def create_tables() -> None:
    # Development fallback only. Production starts with `alembic upgrade head`.
    # Keeping this is useful for the deterministic SQLite test mode.
    from app.repositories import models, tasks  # noqa: F401
    Base.metadata.create_all(bind=engine)
