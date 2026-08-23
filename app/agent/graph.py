from langgraph.graph import END, START, StateGraph

from app.agent.nodes import initialize
from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool
from app.agent.state import ResearchState


def _resume_route(state: ResearchState) -> str:
    node = state.get("checkpoint", {}).get("node", "queued")
    return "initialize" if node == "queued" else "agent_decide_next_action" if node in {"initialize", "agent_decide_next_action", "agent_execute_tool"} else "initialize"


def _after_action(state: ResearchState) -> str:
    return "done" if state.get("agent_finished") else "decide"


def build_research_graph():
    """A single bounded ReAct manager over deterministic composite actions."""
    graph = StateGraph(ResearchState)
    graph.add_node("initialize", initialize)
    graph.add_node("agent_decide_next_action", agent_decide_next_action)
    graph.add_node("agent_execute_tool", agent_execute_tool)
    graph.add_conditional_edges(START, _resume_route, {"initialize": "initialize", "agent_decide_next_action": "agent_decide_next_action"})
    graph.add_edge("initialize", "agent_decide_next_action")
    graph.add_edge("agent_decide_next_action", "agent_execute_tool")
    graph.add_conditional_edges("agent_execute_tool", _after_action, {"decide": "agent_decide_next_action", "done": END})
    return graph.compile()
