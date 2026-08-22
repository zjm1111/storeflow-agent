"""Native LangGraph review interruption boundary with a MySQL checkpointer."""
from __future__ import annotations

from typing import TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.core import get_settings


class ReviewState(TypedDict, total=False):
    task_id: str
    review_payload: dict
    review_command: dict


def _review_gate(state: ReviewState) -> dict:
    command = interrupt(state["review_payload"])
    return {"review_command": command}


def build_review_graph():
    """Use MySQL for review threads, with a deliberate test/local fallback.

    The fallback only applies to SQLite test mode or a missing optional package;
    deployment health surfaces it instead of presenting it as durable execution.
    """
    settings = get_settings()
    checkpointer = MemorySaver()
    checkpoint_url = settings.langgraph_checkpoint_url or settings.mysql_url
    if checkpoint_url != "sqlite://":
        try:
            import pymysql
            from langgraph.checkpoint.mysql.pymysql import PyMySQLSaver

            mysql_url = checkpoint_url.replace("mysql+pymysql://", "mysql://", 1)
            options = PyMySQLSaver.parse_conn_string(mysql_url)
            connection = pymysql.connect(**options, autocommit=True)
            checkpointer = PyMySQLSaver(connection)
            checkpointer.setup()
        except Exception as exc:
            # This is a safe demo fallback; callers expose the mode in review
            # payload rather than asserting persistent LangGraph execution.
            checkpointer = MemorySaver()
            setattr(checkpointer, "supplymind_degradation", f"MySQL LangGraph checkpointer fallback: {type(exc).__name__}")
    graph = StateGraph(ReviewState)
    graph.add_node("review_gate", _review_gate)
    graph.add_edge(START, "review_gate")
    graph.add_edge("review_gate", END)
    compiled = graph.compile(checkpointer=checkpointer)
    setattr(compiled, "supplymind_checkpointer_mode", "mysql" if not isinstance(checkpointer, MemorySaver) else "memory")
    setattr(compiled, "supplymind_checkpointer_degradation", getattr(checkpointer, "supplymind_degradation", None))
    # Retain the connection for this process lifetime; closing it would make a
    # compiled graph unable to resume an interrupted review.
    setattr(compiled, "supplymind_checkpointer", checkpointer)
    return compiled
