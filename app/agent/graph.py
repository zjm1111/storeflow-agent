from langgraph.graph import END, START, StateGraph

from app.agent.nodes import initialize
from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool, agent_mark_action_running, agent_recover_action
from app.agent.state import ResearchState


def _resume_route(state: ResearchState) -> str:
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
    """A single bounded ReAct manager over deterministic composite actions."""
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
    return graph.compile()
