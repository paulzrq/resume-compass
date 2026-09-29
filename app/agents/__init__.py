"""简历罗盘 · 多智能体打分流水线（LangGraph）。

拓扑：
    plan（planner：定取证计划）
      → score（ReAct scorer：带 tools 逐维度打分）
      → critique（critic v2：审查并直接修正分数，不打回重跑）
      → report（确定性汇总，复用 scoring.py 的计分规则）

成本模式：build_graph(..., max_revisions=1) 时 critic 只做审计、不触发修订轮。
eval 证明修订轮不提升分数（28 对配对差值≈0），便宜模式约省一半 API 调用，
是从 $0.35/份降到 ~$0.18/份（Haiku）的主要手段。

分工：
- 脚手架（已搭好）：state 定义、graph 拓扑、nodes 接线、条件边、离线测试
- Paul 手写：prompts.py 的 3 个 prompt、tools.py 的 search_jd_library、
  nodes.py 里 report_node 的确定性总分、以及 eval harness（评估集）
"""
from .graph import build_graph

__all__ = ["build_graph"]
