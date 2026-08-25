"""Shared native LangGraph checkpointer construction.

The graph checkpointer owns durable graph execution (thread/node/interrupt).
It deliberately does not replace the business TaskRepository, which owns task
projection, workspace isolation, idempotency and optimistic locking.
"""
from __future__ import annotations

from langgraph.checkpoint.memory import MemorySaver

from app.core import get_settings


def build_langgraph_checkpointer():
    """Return a MySQL saver when configured, otherwise an explicit local fallback.

    SQLite test mode and a missing optional MySQL dependency use ``MemorySaver``.
    Callers expose the returned mode/degradation so a demo fallback is never
    described as durable execution.
    """
    settings = get_settings()
    checkpoint_url = settings.langgraph_checkpoint_url or settings.mysql_url
    if checkpoint_url == "sqlite://":
        return MemorySaver(), "memory", "SQLite test mode uses an in-memory LangGraph checkpointer"

    try:
        import pymysql
        from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

        mysql_url = checkpoint_url.replace("mysql+pymysql://", "mysql://", 1)
        options = PyMySQLSaver.parse_conn_string(mysql_url)
        connection = pymysql.connect(**options, autocommit=True)
        saver = PyMySQLSaver(connection)
        saver.setup()
        # PyMySQLSaver retains the connection for the compiled graph lifetime.
        return saver, "mysql", None
    except Exception as exc:
        saver = MemorySaver()
        degradation = f"MySQL LangGraph checkpointer fallback: {type(exc).__name__}"
        setattr(saver, "supplymind_degradation", degradation)
        return saver, "memory", degradation
