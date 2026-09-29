"""LangGraph 共享状态：planner → scorer → critic → reporter 全程读写同一份 state。"""
from typing import Optional
from typing_extensions import TypedDict

try:  # langgraph 版本差异：新版直接从 langgraph.graph 导出
    from langgraph.graph import add_messages  # noqa: F401  (预留给将来扩展用)
except ImportError:  # pragma: no cover
    from langgraph.graph.message import add_messages  # noqa: F401


class DimensionPlan(TypedDict):
    """planner 对单个维度的取证计划。"""
    dimension: str        # edu / exp / proj / skill / cert / lead / present
    strategy: str         # 取证策略，例如 "anchor_only" / "jd_grounded" / "deep_dive"
    jd_queries: list      # strategy 需要查 JD 库时，具体的检索问题（可空）


class DimensionResult(TypedDict, total=False):
    """scorer 对单个维度的打分输出。"""
    score: int            # 1-5
    rationale: str        # 不超过35字的中文理由
    evidence: list        # 逐字摘自简历的证据片段


class AgentState(TypedDict, total=False):
    """图的全共享状态。total=False：各 node 只写自己负责的键。"""
    resume_text: str             # 简历原文（输入）
    field_id: str                # 目标领域 id（输入）
    custom_field_name: Optional[str]  # 自定义方向名称（可空，输入）
    field: dict                  # resolve_field 解析出的领域对象（含权重/加分/短板）
    plan: list                   # list[DimensionPlan]，planner 输出
    scorer_output: Optional[dict]  # scorer 最新一次的完整输出（含 dimensions 等）
    critic_feedback: list        # critic 的质疑清单（通过时为空）
    critic_pass: bool            # critic 是否放行
    critic_corrections: list     # critic 直接修正的分数（v2：不再打回重跑）
    revision_round: int          # scorer 已跑的次数（含初次）
    final_result: Optional[dict]  # report_node 组装的最终结果（输出）
