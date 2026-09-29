"""组装 LangGraph。"""
from functools import partial
from langgraph.graph import END, StateGraph

from scoring import DEFAULT_MODEL
from .nodes import (
    MAX_REVISIONS,
    critique_node,
    plan_node,
    report_node,
    route_after_critique,
    score_node,
)
from .state import AgentState


def build_graph(api_key: str, model: str = DEFAULT_MODEL, scorer_agent=None,
                max_revisions: int = MAX_REVISIONS):
    """编译多智能体打分图。

    api_key/model：透传给各 node。scorer_agent：可注入 fake agent（测试用），
    不注入则 score_node 在运行时按 state 组装真实的 ReAct agent。
    max_revisions：scorer 最多跑几次（含初次）。=1 时 critic 只做审计、
    不触发修订轮——eval 证明修订轮不提升分数，这是便宜模式（约省一半调用）。
    默认 2（保持原有行为，测试依赖此默认值）。
    """
    g = StateGraph(AgentState)
    g.add_node("plan", partial(plan_node, api_key=api_key, model=model))
    g.add_node("score", partial(score_node, api_key=api_key, model=model,
                               scorer_agent=scorer_agent))
    g.add_node("critique", partial(critique_node, api_key=api_key, model=model))
    g.add_node("report", report_node)

    g.set_entry_point("plan")
    g.add_edge("plan", "score")
    g.add_edge("score", "critique")
    g.add_conditional_edges("critique",
                            partial(route_after_critique,
                                    max_revisions=max_revisions),
                            {"revise": "score", "report": "report"})
    g.add_edge("report", END)
    return g.compile()


__all__ = ["build_graph", "MAX_REVISIONS"]
