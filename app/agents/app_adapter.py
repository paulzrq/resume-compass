"""Agent 模式的 app 接入适配层。

run_agent_assessment(...)：跑 LangGraph（便宜模式 max_revisions=1），
把 final_result 转成 app.py 结果页可直接渲染的 result 字典。

不花钱：纯格式转换；测试用 fake scorer_agent + mock _call_llm。
花钱：真实 invoke（等 credit 补充后验证，至少跑通 1-2 份）。
"""
import json

import scoring
from scoring import DEFAULT_MODEL

from .graph import build_graph


def run_agent_assessment(resume_text: str, field_id: str, api_key: str,
                         model: str = DEFAULT_MODEL, custom_field_name=None,
                         scorer_agent=None) -> dict:
    """跑一次 Agent 评估，返回 app 结果页可直接渲染的 result dict。

    scorer_agent：测试时注入 fake；不传则跑真实 ReAct agent（花钱）。
    """
    framework = scoring.load_framework()
    # 口径与 score_resume 一致：field 解析走同一套 resolve_field
    field, _jd_reference, _match_usage = scoring.resolve_field(
        framework, field_id, api_key, custom_field_name=custom_field_name)

    graph = build_graph(api_key=api_key, model=model,
                        scorer_agent=scorer_agent, max_revisions=1)
    state = graph.invoke({
        "resume_text": resume_text,
        "field_id": field_id,
        "custom_field_name": custom_field_name,
        "field": field,
        "revision_round": 0,
    })
    final = state.get("final_result") or {}

    result = dict(final)
    result["framework"] = framework
    result["agent_mode"] = True
    # 结果页调试 expander 直接读 result["raw_model_output"]（非 .get，会 KeyError），
    # 给它一份可读的 agent 轨迹 JSON。
    result["raw_model_output"] = json.dumps(
        {"plan": state.get("plan"),
         "scorer_output": state.get("scorer_output"),
         "critic_feedback": state.get("critic_feedback", []),
         "critic_corrections": state.get("critic_corrections", []),
         "revision_rounds": state.get("revision_round", 0)},
        ensure_ascii=False, indent=2, default=str)
    return result


__all__ = ["run_agent_assessment"]
