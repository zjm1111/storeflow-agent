"""Native LangGraph review interruption boundary with a MySQL checkpointer."""
from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.agent.checkpointer import build_langgraph_checkpointer


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
    checkpointer, mode, degradation = build_langgraph_checkpointer()
    graph = StateGraph(ReviewState)
    graph.add_node("review_gate", _review_gate)
    graph.add_edge(START, "review_gate")
    graph.add_edge("review_gate", END)
    compiled = graph.compile(checkpointer=checkpointer)
    setattr(compiled, "supplymind_checkpointer_mode", mode)
    setattr(compiled, "supplymind_checkpointer_degradation", degradation)
    # Retain the connection for this process lifetime; closing it would make a
    # compiled graph unable to resume an interrupted review.
    setattr(compiled, "supplymind_checkpointer", checkpointer)
    return compiled
