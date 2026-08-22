from langgraph.graph import END, START, StateGraph

from app.agent.nodes import assess_coverage, complete, extract_events, generate_report, initialize, parse_sources, plan_research, replan, retrieve_sources, score_evidence
from app.agent.nodes.workflow import agent_decide_next_action, agent_execute_tool
from app.agent.state import ResearchState


def _finish_route(_: ResearchState) -> str:
    return "done"


def _coverage_route(state: ResearchState) -> str:
    if state.get("stop_reason") or not state.get("missing_dimensions"):
        return "enough"
    if state.get("loop_count", 0) >= state.get("max_loop", 2) or state.get("search_count", 0) >= state.get("max_search", 2):
        return "enough"
    return "replan"


def _resume_route(state: ResearchState) -> str:
    last_node = state.get("checkpoint", {}).get("node", "queued")
    return {
        "queued": "initialize",
        "initialize": "agent_decide_next_action",
        "agent_decide_next_action": "plan_research" if (state.get("next_action") or {}).get("tool") == "finish" else "agent_execute_tool",
        "agent_execute_tool": "agent_decide_next_action" if not state.get("agent_finished") and len(state.get("agent_actions", [])) < state.get("max_loop", 6) else "plan_research",
        "plan_research": "retrieve_sources",
        "retrieve_sources": "parse_sources",
        "parse_sources": "score_evidence",
        "score_evidence": "assess_coverage",
        "assess_coverage": "replan" if _coverage_route(state) == "replan" else "extract_events",
        "replan": "retrieve_sources",
        "extract_events": "generate_report",
        "generate_report": "complete",
        "complete": "done",
    }.get(last_node, "initialize")


def _agent_route(state: ResearchState) -> str:
    return "continue" if not state.get("agent_finished") and len(state.get("agent_actions", [])) < state.get("max_loop", 6) else "research"


def _agent_decision_route(state: ResearchState) -> str:
    return "research" if (state.get("next_action") or {}).get("tool") == "finish" else "execute"


def build_research_graph():
    graph = StateGraph(ResearchState)
    graph.add_node("initialize", initialize)
    graph.add_node("agent_decide_next_action", agent_decide_next_action)
    graph.add_node("agent_execute_tool", agent_execute_tool)
    graph.add_node("plan_research", plan_research)
    graph.add_node("complete", complete)
    graph.add_node("parse_sources", parse_sources)
    graph.add_node("score_evidence", score_evidence)
    graph.add_node("extract_events", extract_events)
    graph.add_node("generate_report", generate_report)
    graph.add_node("retrieve_sources", retrieve_sources)
    graph.add_node("assess_coverage", assess_coverage)
    graph.add_node("replan", replan)
    graph.add_conditional_edges(START, _resume_route, {"initialize": "initialize", "agent_decide_next_action": "agent_decide_next_action", "agent_execute_tool": "agent_execute_tool", "plan_research": "plan_research", "retrieve_sources": "retrieve_sources", "parse_sources": "parse_sources", "score_evidence": "score_evidence", "assess_coverage": "assess_coverage", "replan": "replan", "extract_events": "extract_events", "generate_report": "generate_report", "complete": "complete", "done": END})
    graph.add_edge("initialize", "agent_decide_next_action")
    graph.add_conditional_edges("agent_decide_next_action", _agent_decision_route, {"execute": "agent_execute_tool", "research": "plan_research"})
    graph.add_conditional_edges("agent_execute_tool", _agent_route, {"continue": "agent_decide_next_action", "research": "plan_research"})
    graph.add_edge("plan_research", "retrieve_sources")
    graph.add_edge("retrieve_sources", "parse_sources")
    graph.add_edge("parse_sources", "score_evidence")
    graph.add_edge("score_evidence", "assess_coverage")
    graph.add_conditional_edges("assess_coverage", _coverage_route, {"replan": "replan", "enough": "extract_events"})
    graph.add_edge("replan", "retrieve_sources")
    graph.add_edge("extract_events", "generate_report")
    graph.add_edge("generate_report", "complete")
    graph.add_conditional_edges("complete", _finish_route, {"done": END})
    return graph.compile()
