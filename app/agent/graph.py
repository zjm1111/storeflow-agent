from langgraph.graph import END, START, StateGraph

from app.agent.checkpointer import build_langgraph_checkpointer
from app.agent.nodes import initialize
from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool, agent_mark_action_running
from app.agent.state import ResearchState


def _after_action(state: ResearchState) -> str:
    return "done" if state.get("agent_finished") else "decide"


def build_research_graph():
    """A single bounded ReAct manager over deterministic composite actions.

    Native LangGraph checkpoints are keyed by the task id supplied at runtime.
    The business task projection tracks action audit/idempotency data; native
    LangGraph checkpoints remain the only graph recovery store.
    """
    graph = StateGraph(ResearchState)
    graph.add_node("initialize", initialize)
    graph.add_node("agent_decide_next_action", agent_decide_next_action)
    graph.add_node("agent_mark_action_running", agent_mark_action_running)
    graph.add_node("agent_execute_tool", agent_execute_tool)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "agent_decide_next_action")
    graph.add_edge("agent_decide_next_action", "agent_mark_action_running")
    graph.add_edge("agent_mark_action_running", "agent_execute_tool")
    graph.add_conditional_edges("agent_execute_tool", _after_action, {"decide": "agent_decide_next_action", "done": END})
    checkpointer, mode, degradation = build_langgraph_checkpointer()
    compiled = graph.compile(checkpointer=checkpointer)
    setattr(compiled, "supplymind_checkpointer_mode", mode)
    setattr(compiled, "supplymind_checkpointer_degradation", degradation)
    setattr(compiled, "supplymind_checkpointer", checkpointer)
    return compiled
