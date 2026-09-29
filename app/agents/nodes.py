"""Graph nodes。LLM 调用统一走 _call_llm（裸 anthropic SDK，和 scoring.py 风格一致），
测试时可 monkeypatch nodes._call_llm。

prompt 尚未实现（Paul 还没写 prompts.py）时，各 node 走 FALLBACK 逻辑，
保证图能跑通、接线可验证；prompt 写好后自动切换到真实逻辑。
"""
import json

import anthropic

import scoring
from scoring import DEFAULT_MODEL
from . import prompts
from .state import AgentState
from .tools import build_tools

MAX_REVISIONS = 2  # scorer 最多跑几次（含初次）：1 初次 + 1 轮修订


def _call_llm(api_key: str, model: str, system_prompt: str, user_text: str,
              max_tokens: int = 4000) -> str:
    """一次裸 API 调用，返回模型文本。JSON 解析由调用方做。"""
    client = anthropic.Anthropic(api_key=api_key)
    resp = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": user_text}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


_FALLBACK_PLAN = [
    {"dimension": d, "strategy": "anchor_only", "jd_queries": []}
    for d in ["edu", "exp", "proj", "skill", "cert", "lead", "present"]
]


def plan_node(state: AgentState, api_key: str, model: str = DEFAULT_MODEL) -> dict:
    """Planner：为 7 个维度定取证计划。"""
    try:
        system_prompt = prompts.build_planner_prompt(state["resume_text"], state["field"])
    except NotImplementedError:
        return {"plan": list(_FALLBACK_PLAN)}  # FALLBACK：prompt 未实现时全走 anchor_only
    raw = _call_llm(api_key, model, system_prompt,
                    "请输出本份简历的取证计划 JSON 数组：")
    plan = _parse_json_array(raw)
    assert isinstance(plan, list) and len(plan) == 7, "planner 输出必须是 7 个维度的数组"
    return {"plan": plan}


def _parse_json_array(raw: str) -> list:
    """解析 planner 的 JSON 数组输出：去 markdown 围栏，抠第一个完整的 [...]。
    Haiku 喜欢在外面包 ```json 围栏，Sonnet 直接裸输出——两种都要能吃。"""
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            first_line, rest = text.split("\n", 1)
            text = rest if first_line.strip().lower() in ("json", "") else text
    text = text.strip()
    start = text.find("[")
    if start == -1:
        return json.loads(text)  # 不是数组也交给 json.loads 报一个明白的错
    depth, in_string, escape = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    return json.loads(text)  # 被截断时原样交给 json.loads 报错，不悄悄吞掉


def _make_scorer_agent(api_key: str, model: str, tools: list):
    """标准 LangGraph ReAct agent（langgraph.prebuilt）。"""
    from langchain_anthropic import ChatAnthropic
    from langgraph.prebuilt import create_react_agent
    llm = ChatAnthropic(model=model, api_key=api_key)
    return create_react_agent(llm, tools)


def _extract_message_text(content) -> str:
    """取 ReAct agent 最后一条消息的纯文本。

    langchain_anthropic 在模型开 thinking 时，AIMessage.content 不是 str，
    而是 block 列表：[{"type": "thinking", ...}, {"type": "text", "text": ...}]。
    只拼接 type=="text" 的块，跳过 thinking/signature。旧链路在 scoring.py 里
    也是按 block.type=="text" 过滤的，两边口径一致。
    """
    if isinstance(content, str):
        return content
    parts = []
    for b in content or []:
        if isinstance(b, dict):
            if b.get("type") == "text":
                parts.append(b.get("text") or "")
        elif getattr(b, "type", None) == "text":
            parts.append(getattr(b, "text", "") or "")
    return "\n".join(parts)


def score_node(state: AgentState, api_key: str, model: str = DEFAULT_MODEL,
               scorer_agent=None) -> dict:
    """Scorer：ReAct agent 带 tools 逐维度打分。

    scorer_agent 可注入 fake（测试用）；不注入则现场组装真实 agent。
    """
    from langchain_core.messages import HumanMessage
    tools = build_tools(state["resume_text"])
    agent = scorer_agent or _make_scorer_agent(api_key, model, tools)
    try:
        system_prompt = prompts.build_scorer_prompt(state["field"], state.get("plan") or [])
    except NotImplementedError:
        # FALLBACK：prompt 未实现时的最小指令（能跑通，不保证质量）
        system_prompt = (
            "你是简历打分助手。可用工具：get_dimension_rubric 查评分锚点，"
            "search_jd_library 检索 JD 参考，verify_quote 校验引用。\n"
            "对 edu/exp/proj/skill/cert/lead/present 七个维度逐项打 1-5 分，"
            "先摘证据（逐字摘自简历）、再写理由（中文35字内）、最后打分。\n"
            "只输出一个 JSON 对象，键与 scoring._score_schema 一致，"
            "不要 markdown 包裹、不要解释文字。"
        )
    user_text = f"{system_prompt}\n\n以下是简历原文，请打分：\n\n{state['resume_text']}"
    feedback = state.get("critic_feedback")
    if feedback:
        # 修订轮：把 critic 的质疑清单喂给 scorer，要求针对性修正
        user_text += ("\n\n上一轮 critic 给出了以下质疑，请针对性修正后重新输出"
                     "完整的打分 JSON（只输出 JSON，不要解释）：\n"
                     + "\n".join(f"- {f}" for f in feedback))
    # Anthropic prompt caching：指令+简历是 ReAct 每步重发的前缀，
    # 标记 cache breakpoint 后续步骤命中缓存按 1 折计费（write 1.25x 仅第一次）。
    # ReAct 场景的标准省钱手段，不改变模型行为。
    _cached_content = [{"type": "text", "text": user_text,
                        "cache_control": {"type": "ephemeral"}}]
    result = agent.invoke({"messages": [HumanMessage(content=_cached_content)]})
    last_text = _extract_message_text(result["messages"][-1].content)
    # 复用 scoring.py 的鲁棒 JSON 解析（处理 markdown 包裹/截断兜底）
    parsed = scoring._parse_json_response(last_text)
    return {
        "scorer_output": parsed,
        "revision_round": state.get("revision_round", 0) + 1,
    }


def critique_node(state: AgentState, api_key: str, model: str = DEFAULT_MODEL) -> dict:
    """Critic v2：审查并直接修正分数。

    不再打回 scorer 重跑（eval 证明修订轮不提分）：critic 在 corrections 里
    给出修正后的分数，本函数直接合并进 scorer_output。修正过的维度 rationale
    会追加修正说明，保证可审计。
    """
    try:
        system_prompt = prompts.build_critic_prompt(
            state["field"], state["scorer_output"], state.get("resume_text", ""))
    except NotImplementedError:
        return {"critic_pass": True, "critic_feedback": []}  # FALLBACK：prompt 未实现时默认放行
    raw = _call_llm(api_key, model, system_prompt, "请输出审查结果 JSON：")
    # critic 输出是单个 JSON 对象，复用 scoring.py 的鲁棒解析（去围栏/抠对象）
    data = scoring._parse_json_response(raw)
    feedback = data.get("feedback") or []
    corrections = data.get("corrections") or []

    # 直接改分：把 corrections 合并进 scorer_output
    scorer_output = state.get("scorer_output") or {}
    applied = []
    if corrections:
        dims = {k: dict(v or {})
                for k, v in (scorer_output.get("dimensions") or {}).items()}
        for c in corrections:
            d, new_score = c.get("dimension"), c.get("new_score")
            if d in dims and isinstance(new_score, int) and 1 <= new_score <= 5:
                old_score = dims[d].get("score")
                dims[d]["score"] = new_score
                dims[d]["rationale"] = (
                    (dims[d].get("rationale") or "")
                    + f"【critic修正 {old_score}→{new_score}：{c.get('reason', '')}】")
                applied.append({"dimension": d, "old_score": old_score,
                                "new_score": new_score,
                                "reason": c.get("reason", "")})
        scorer_output = {**scorer_output, "dimensions": dims}

    return {"critic_pass": bool(data.get("pass")) and not applied,
            "critic_feedback": feedback,
            "critic_corrections": applied,
            "scorer_output": scorer_output}


def route_after_critique(state: AgentState, max_revisions: int = MAX_REVISIONS) -> str:
    """条件边：critic 放行，或修订次数用完 → report；否则回 score 修订。

    max_revisions 由 build_graph 传入：=1 时 critic 只做审计（反馈记入报告，
    不触发修订），是 eval 验证过的便宜模式（修订轮不提升分数，见 eval 报告）。
    v2：critic 已直接修正分数（critic_corrections 非空）→ 直接 report，
    不再回炉（修正本身就是修订）。
    """
    if state.get("critic_corrections"):
        return "report"
    if state.get("critic_pass") or state.get("revision_round", 0) >= max_revisions:
        return "report"
    return "revise"


def report_node(state: AgentState) -> dict:
    """确定性汇总：加权总分 + 封顶 + 定级。

    纯 Python 计算，不调 LLM——"LLM 不做算术"。公式与
    scoring.score_resume() 完全一致（加权 base + bonus 上限 10 分 +
    _score_cap 封顶 + tiers 定级），新旧链路总分口径统一，
    eval 对比时才有意义。
    """
    out = state.get("scorer_output") or {}
    dims = out.get("dimensions") or {}
    scores = {k: (v or {}).get("score", 0) for k, v in dims.items()}
    field = state.get("field") or {}
    framework = scoring.load_framework()

    weights = field.get("weights") or {}
    base = sum(weights.get(k, 0) * (scores.get(k, 0) / 5) for k in weights)
    bonus = field.get("bonus") or []
    checked = out.get("bonus_checked") or []
    bonus_pts = min(sum(bonus[i][1] for i in checked
                        if isinstance(i, int) and 0 <= i < len(bonus)
                        and isinstance(bonus[i], (list, tuple))
                        and len(bonus[i]) > 1), 10)
    total = round(min(scoring._score_cap(scores, framework), base + bonus_pts))
    tier = next(t for t in framework["tiers"] if total >= t["min"])

    return {"final_result": {
        "dimension_scores": scores,
        "dimension_rationale": {k: (v or {}).get("rationale", "")
                               for k, v in dims.items()},
        "dimension_evidence": {k: (v or {}).get("evidence", [])
                               for k, v in dims.items()},
        "ats_keywords": out.get("ats_keywords", []),
        "vague_phrases": out.get("vague_phrases", []),
        "strong_phrases": out.get("strong_phrases", []),
        "bonus_checked": checked,
        "strengths": out.get("strengths", []),
        "gaps": out.get("gaps", []),
        "stage_note": out.get("stage_note", ""),
        "total": total,
        "tier_label": tier["label"],
        "plan": state.get("plan"),
        "critic_pass": state.get("critic_pass"),
        "critic_feedback": state.get("critic_feedback", []),
        "critic_corrections": state.get("critic_corrections", []),
        "revision_rounds": state.get("revision_round", 0),
        "field": field,
    }}
