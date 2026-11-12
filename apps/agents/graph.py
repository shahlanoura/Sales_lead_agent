from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from . import nodes
from .checkpoint import get_checkpointer
from .state import SalesState

_graph = None
RETRY = RetryPolicy(max_attempts=3, initial_interval=2.0)


def build_graph(checkpointer=None):
    g = StateGraph(SalesState)
    g.add_node("research", nodes.research_node, retry_policy=RETRY)
    g.add_node("manual", nodes.manual_review_node)
    g.add_node("qualify", nodes.qualify_node, retry_policy=RETRY)
    g.add_node("draft", nodes.draft_node, retry_policy=RETRY)
    g.add_node("approval", nodes.approval_node)
    g.add_node("send", nodes.send_node, retry_policy=RETRY)

    g.add_edge(START, "research")
    g.add_conditional_edges("research", nodes.route_after_research, {"qualify": "qualify", "manual": "manual"})
    g.add_edge("manual", END)
    g.add_conditional_edges("qualify", nodes.route_after_qualify, {"draft": "draft", "end": END})
    g.add_edge("draft", "approval")
    g.add_conditional_edges("approval", nodes.route_after_approval, {"send": "send", "end": END})
    g.add_edge("send", END)
    return g.compile(checkpointer=checkpointer or get_checkpointer())


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph
