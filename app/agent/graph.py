from langgraph.graph import END, START, StateGraph

from app.agent.checkpointer import build_langgraph_checkpointer
from app.agent.nodes import initialize
from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool, agent_mark_action_running, agent_recover_action
from app.agent.state import ResearchState


def _resume_route(state: ResearchState) -> str:
    # Normal execution always starts at initialize. Native LangGraph recovery
    # never reaches START: ResearchService calls stream(None, thread_id) and
    # resumes the durable pending node directly. This branch is intentionally
    # retained only for task snapshots created before native checkpoints were
    # introduced, so their in-flight read-only action can be recovered once.
    if not state.get("graph_execution", {}).get("legacy_resume"):
        return "initialize"
    node = state.get("checkpoint", {}).get("node", "queued")
    active = state.get("active_action") or {}
    action_status = active.get("status")
    if node == "queued":
        return "initialize"
    if action_status in {"running", "unknown"} or node in {"agent_action_running", "agent_recover_action"}:
        return "agent_recover_action"
    if action_status == "planned":
        return "agent_mark_action_running"
    return "agent_decide_next_action"


def _after_action(state: ResearchState) -> str:
    return "done" if state.get("agent_finished") else "decide"


def build_research_graph():
    """A single bounded ReAct manager over deterministic composite actions.

    Native LangGraph checkpoints are keyed by the task id supplied at runtime.
    The custom action snapshot remains business audit/idempotency data during
    the staged migration; it is not the native graph execution store.
    """
    graph = StateGraph(ResearchState)
    graph.add_node("initialize", initialize)
    graph.add_node("agent_decide_next_action", agent_decide_next_action)
    graph.add_node("agent_mark_action_running", agent_mark_action_running)
    graph.add_node("agent_recover_action", agent_recover_action)
    graph.add_node("agent_execute_tool", agent_execute_tool)
    graph.add_conditional_edges(START, _resume_route, {
        "initialize": "initialize", "agent_decide_next_action": "agent_decide_next_action",
        "agent_mark_action_running": "agent_mark_action_running", "agent_recover_action": "agent_recover_action",
    })
    graph.add_edge("initialize", "agent_decide_next_action")
    graph.add_edge("agent_decide_next_action", "agent_mark_action_running")
    graph.add_edge("agent_recover_action", "agent_mark_action_running")
    graph.add_edge("agent_mark_action_running", "agent_execute_tool")
    graph.add_conditional_edges("agent_execute_tool", _after_action, {"decide": "agent_decide_next_action", "done": END})
    checkpointer, mode, degradation = build_langgraph_checkpointer()
    compiled = graph.compile(checkpointer=checkpointer)
    setattr(compiled, "supplymind_checkpointer_mode", mode)
    setattr(compiled, "supplymind_checkpointer_degradation", degradation)
    setattr(compiled, "supplymind_checkpointer", checkpointer)
    return compiled
