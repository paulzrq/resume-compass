"""Graph nodes。LLM 调用统一走 _call_llm（裸 anthropic SDK，和 scoring.py 风格一致），
测试时可 monkeypatch nodes._call_llm。

prompt 尚未实现（Paul 还没写 prompts.py）时，各 node 走 FALLBACK 逻辑，
保证图能跑通、接线可验证；prompt 写好后自动切换到真实逻辑。
"""
import json
from copy import deepcopy

import anthropic
# 2026-10-04：langchain_anthropic / langgraph.prebuilt 原来是延迟到 _make_scorer_agent
# 里面才 import（函数内 import），这两个包首次导入比较重（langchain 生态常见情况），
# Python 的 sys.modules 缓存决定了"重"的那部分开销只会真正发生一次——但之前这个"一次"
# 恰好落在用户第一次点开 In-depth assessment、盯着界面等结果的那一刻，体验上就是
# "点下去好一会儿没反应"。这两个包不参与任何条件分支（没有 FALLBACK 不用它们的情况），
# 挪到模块顶层以后，这次性的导入开销会在 app 启动时（import agents.app_adapter 那一刻）
# 就付掉，而不是挪到用户交互路径上；跟 tools.py 顶部已经在模块级 import langchain_core.tools、
# graph.py 顶部已经在模块级 import langgraph.graph 保持一致的写法。
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage
from langgraph.prebuilt import create_react_agent

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
    """Planner：为 7 个维度定取证计划。
    2026-10-04：补上跟 scoring.py._score_resume_once 一样的"空输出/解析失败重试一次"保护——
    之前这里直接把 _call_llm 的裸输出丢给 _parse_json_array，模型这次调用如果没产出任何
    text 类型内容块（比如这次调用恰好把 max_tokens 都耗在别的内容上，没能力复现具体原因，
    但确实观测到过），raw 会是空字符串，json.loads("") 直接抛出一个 "Expecting value: line 1
    column 1 (char 0)" 这种让人摸不着头脑的原始报错，直接顶到 app.py 的 st.error 里，
    看着完全不像"模型这次没发挥好"。跟常规打分路径一样重试一次，重试还不行才把原始报错
    包成一句说得清楚的话再抛出去。"""
    try:
        system_prompt = prompts.build_planner_prompt(state["resume_text"], state["field"])
    except NotImplementedError:
        return {"plan": list(_FALLBACK_PLAN)}  # FALLBACK：prompt 未实现时全走 anchor_only
    keys = {d["key"] for d in scoring.load_framework()["dimensions"]}
    strategies = {"anchor_only", "jd_grounded", "deep_dive", "cross_check", "conservative_skip"}
    last_error = None
    for _attempt in range(2):
        raw = _call_llm(api_key, model, system_prompt,
                        "Output the evidence-gathering plan JSON array for this resume:")
        if not raw.strip():
            last_error = ValueError("Planner call returned no text output")
            continue
        try:
            plan = _parse_json_array(raw)
        except json.JSONDecodeError as e:
            last_error = e
            continue
        if (not isinstance(plan, list) or len(plan) != len(keys)
            or any(not isinstance(p, dict) or not isinstance(p.get("dimension"), str)
                   or not isinstance(p.get("strategy"), str)
                   or p.get("strategy") not in strategies
                   or not isinstance(p.get("jd_queries"), list)
                   or not all(isinstance(q, str) for q in p["jd_queries"]) for p in plan)
            or {p["dimension"] for p in plan} != keys):
            last_error = ValueError("The evidence-gathering plan must cover all seven dimensions with valid strategies and query formats")
            continue
        return {"plan": plan}
    raise RuntimeError(
        "In-depth assessment's planning step did not return a valid plan after one "
        "automatic retry. Please re-run the assessment later."
    ) from last_error


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
        return json.loads(text, strict=False)  # 不是数组也交给 json.loads 报一个明白的错
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
                return json.loads(text[start:i + 1], strict=False)
    return json.loads(text, strict=False)  # 被截断时原样交给 json.loads 报错，不悄悄吞掉


def _make_scorer_agent(api_key: str, model: str, tools: list):
    """标准 LangGraph ReAct agent（langgraph.prebuilt）。
    2026-10-04：这才是"打分这步重试两次都拿不到有效结果"的真正根因——`ChatAnthropic`
    不传 max_tokens 时，langchain_anthropic 会偷偷给默认值 1024（底层 anthropic SDK
    要求 max_tokens 必须是个具体的 int，没法像 ChatOpenAI 那样留空交给模型自己判断上限，
    这是 langchain-anthropic 的已知行为，见 langchain-ai/langchain#27067）。诊断报错里
    最后一条消息的 content 只有一个 {'signature': ...}——这是一个 thinking 内容块自己的
    签名，没有配套的 text 块：模型把这 1024 个 token 全部花在"思考"怎么综合 7 个维度的
    取证结果上，还没来得及写出最终那段 JSON 文字就被截断了，跟 scoring.py 里裸 SDK
    调用一直显式给 max_tokens=8000 是同一类坑、同一个解法。这里补上同样量级的
    max_tokens，跟 scoring.py 保持一致。"""
    llm = ChatAnthropic(model=model, api_key=api_key, max_tokens=8000)
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


def _validated_output(data, field):
    if not isinstance(data, dict):
        raise ValueError("The scoring result must be a JSON object")
    data = deepcopy(data)
    # Optional report annotations may be absent; dimensions must never be defaulted.
    for key in ("ats_keywords", "vague_phrases", "strong_phrases", "strengths", "gaps", "bonus_checked"):
        data.setdefault(key, [])
    data.setdefault("stage_note", "")
    scoring._validate_score_data(data, scoring.load_framework(), field)
    return data


def _score_shape_problem(data, field) -> str:
    """只用于诊断，不参与判定：指出打分 JSON 里第一处不合格的具体位置。

    判定本身仍然完全交给 scoring._validate_score_data（这里的检查顺序跟它一一对应），
    这个函数只是把它那句笼统的报错翻译成"具体是哪个字段、实际拿到的是什么类型"。
    只报字段名和类型，不把简历内容带进报错里。
    """
    try:
        if not isinstance(data, dict):
            return f"top-level is {type(data).__name__}, not an object"
        dims = data.get("dimensions")
        if not isinstance(dims, dict):
            return (f"'dimensions' is {type(dims).__name__}; "
                    f"top-level keys={sorted(map(str, data))[:12]}")
        for dimension in scoring.load_framework()["dimensions"]:
            key = dimension["key"]
            entry = dims.get(key)
            if not isinstance(entry, dict):
                return (f"dimension '{key}' is {type(entry).__name__}; "
                        f"dimension keys present={sorted(map(str, dims))[:12]}")
            score = entry.get("score")
            if type(score) is not int or not 1 <= score <= 5:
                return f"dimension '{key}' score={score!r} ({type(score).__name__})"
            if not isinstance(entry.get("rationale"), str):
                return (f"dimension '{key}' rationale is "
                        f"{type(entry.get('rationale')).__name__}")
            evidence = entry.get("evidence")
            if not isinstance(evidence, list):
                return f"dimension '{key}' evidence is {type(evidence).__name__}"
            if not all(isinstance(q, str) for q in evidence):
                return (f"dimension '{key}' evidence item types="
                        f"{[type(q).__name__ for q in evidence][:6]}")
        for key in ("ats_keywords", "vague_phrases", "strong_phrases", "strengths", "gaps"):
            value = data.get(key, [])
            if not isinstance(value, list):
                return f"'{key}' is {type(value).__name__}"
            if not all(isinstance(v, str) for v in value):
                return f"'{key}' item types={[type(v).__name__ for v in value][:6]}"
        bonuses = data.get("bonus_checked", [])
        bonus_count = len((field or {}).get("bonus") or [])
        if not isinstance(bonuses, list):
            return f"'bonus_checked' is {type(bonuses).__name__}"
        if any(type(i) is not int or not 0 <= i < bonus_count for i in bonuses):
            return (f"'bonus_checked'={bonuses[:10]!r} "
                    f"(valid indices are integers 0..{bonus_count - 1})")
        if len(set(bonuses)) != len(bonuses):
            return f"'bonus_checked' has duplicates: {bonuses[:10]!r}"
        stage_note = data.get("stage_note", "")
        if not isinstance(stage_note, str):
            return f"'stage_note' is {type(stage_note).__name__}"
    except Exception as exc:  # 诊断本身绝不能再制造新的报错
        return f"diagnostic failed: {exc!r}"
    return "no specific problem located"


def score_node(state: AgentState, api_key: str, model: str = DEFAULT_MODEL,
               scorer_agent=None) -> dict:
    """Scorer：ReAct agent 带 tools 逐维度打分。

    scorer_agent 可注入 fake（测试用）；不注入则现场组装真实 agent。
    """
    tools = build_tools(state["resume_text"])
    agent = scorer_agent or _make_scorer_agent(api_key, model, tools)
    try:
        system_prompt = prompts.build_scorer_prompt(state["field"], state.get("plan") or [])
    except NotImplementedError:
        # FALLBACK：prompt 未实现时的最小指令（能跑通，不保证质量）
        system_prompt = (
            "You are a resume scoring assistant. Available tools: get_dimension_rubric for scoring anchors, "
            "search_jd_library to retrieve JD references, verify_quote to verify quotes.\n"
            "Score the seven dimensions edu/exp/proj/skill/cert/lead/present one by one, 1-5 each: "
            "evidence first (quoted verbatim from the resume), then rationale (English, 25 words or fewer), then score.\n"
            "Output only a single JSON object with the same keys as scoring._score_schema; "
            "no markdown wrapping, no explanatory text."
        )
    system_prompt += "\nExact output JSON schema:\n" + json.dumps(scoring._score_schema(scoring.load_framework()))
    user_text = f"{system_prompt}\n\nHere is the resume text; please score it:\n\n{state['resume_text']}"
    feedback = state.get("critic_feedback")
    if feedback:
        # 修订轮：把 critic 的质疑清单喂给 scorer，要求针对性修正
        user_text += ("\n\nThe critic raised the following issues in the last round. Address each one "
                     "and re-output the complete scoring JSON (JSON only, no explanations):\n"
                     + "\n".join(f"- {f}" for f in feedback))
    # Anthropic prompt caching：指令+简历是 ReAct 每步重发的前缀，
    # 标记 cache breakpoint 后续步骤命中缓存按 1 折计费（write 1.25x 仅第一次）。
    # ReAct 场景的标准省钱手段，不改变模型行为。
    _cached_content = [{"type": "text", "text": user_text,
                        "cache_control": {"type": "ephemeral"}}]
    # 2026-10-04：跟 plan_node/critique_node 同一个坑，这里之前也完全没防护——
    # ReAct agent 最后一条消息偶尔会没有 text 内容块（比如停在一次 tool_call 上，
    # 或者这轮只产出了 thinking），_extract_message_text 返回空字符串，直接喂给
    # scoring._parse_json_response("") 就是那个讲不清楚的原始 json.JSONDecodeError。
    # 重试一次整个 ReAct 调用（不只是重试解析——本来就要重新让 agent 跑一遍才有机会
    # 真正产出带文字结论的最终消息），重试还不行才抛出说得清楚的报错。
    last_error = None
    last_diag = ""
    parsed = None
    for _attempt in range(2):
        messages = [HumanMessage(content=_cached_content)]
        if _attempt:
            messages.append(HumanMessage(content=(
                "The previous attempt did not produce complete valid scoring JSON. "
                "Return all seven dimensions and every required field using the exact schema. "
                "Diagnostic: " + last_diag)))
        result = agent.invoke({"messages": messages})
        history = result.get("messages", [])
        if not history:
            last_diag = "Scorer returned no messages"
            last_error = ValueError(last_diag)
            continue
        last_message = history[-1]
        stop_reason = (getattr(last_message, "response_metadata", None) or {}).get("stop_reason")
        if stop_reason == "max_tokens" or getattr(last_message, "tool_calls", None):
            last_diag = "Scorer output was truncated or still has pending tool calls"
            last_error = ValueError(last_diag)
            continue
        last_text = _extract_message_text(last_message.content)
        if not last_text.strip():
            # 2026-10-04：光一句"没有文字输出"排查不出到底卡在哪——这里多留一点诊断信息：
            # 最后一条消息到底是什么类型（AIMessage本该是文字结论，但如果ReAct还没决定
            # 收尾，可能停在一次pending的tool_call上；也可能是达到了LangGraph内部的步数
            # 上限被提前截断）、有没有待处理的tool_calls、content本身的结构长什么样。
            # 不打印简历原文/评分内容，只打印结构信息，不涉及隐私。
            msg_type = type(last_message).__name__
            has_tool_calls = bool(getattr(last_message, "tool_calls", None))
            content_repr = type(last_message.content).__name__
            step_count = len(result.get("messages", []))
            last_diag = (f"last_message_type={msg_type}, pending_tool_calls={has_tool_calls}, "
                        f"total_messages_in_run={step_count}, content_type={content_repr}")
            last_error = ValueError(f"Scorer agent's final message had no text output ({last_diag})")
            continue
        candidate = None
        try:
            # 复用 scoring.py 的鲁棒 JSON 解析（处理 markdown 包裹/截断兜底）
            candidate = scoring._parse_json_response(last_text)
            parsed = _validated_output(candidate, state["field"])
            break
        except json.JSONDecodeError as e:
            last_diag = f"Invalid JSON at line {e.lineno}, column {e.colno}"
            last_error = e
            continue
        except (ValueError, scoring._InvalidScoreResponseError) as e:
            # 2026-10-04：原来这里只接 ValueError，但 scoring._validate_score_data 校验
            # 失败时抛的其实是 _InvalidScoreResponseError（继承自 RuntimeError，不是
            # ValueError），之前完全没接住，会直接从 score_node 里原样甩出去，绕过了
            # 上面整套重试/诊断逻辑——这正是上一次报错"Score data is incomplete or
            # malformed"不带重试、不带诊断信息、直接从 app.py 顶层冒出来的原因。
            # 结构校验失败（比如7个维度里缺了几个——ReAct agent这次没把每个维度都走完，
            # 或者某个字段类型不对）跟 scoring.py._score_resume_once 对同类错误的处理
            # 保持一致：当成"这一次没发挥好"重试一次，而不是直接判定成功不了。
            # 2026-10-04：scoring._validate_score_data 不管哪个字段不合格都只抛同一句
            # "Score data is incomplete or malformed"，光看这句话没法知道到底是哪里不对。
            # 这里额外跑一遍只读的诊断，把第一处不合格的具体位置带进报错里。
            last_diag = (f"validation_error={e}; "
                         f"detail={_score_shape_problem(candidate, state['field'])}")
            last_error = e
            continue
    if parsed is None:
        raise RuntimeError(
            "In-depth assessment's scoring step did not return a valid result after one "
            f"automatic retry. Please re-run the assessment later. [{last_diag}]"
        ) from last_error
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
    # 2026-10-04：跟 plan_node 一样的坑——_call_llm 偶尔会返回空字符串（这次调用没产出
    # text 内容块），不加保护的话 scoring._parse_json_response("") 会直接抛出让人摸不着头脑
    # 的原始 json.JSONDecodeError。重试一次，重试还不行才包成一句说得清楚的话再抛出去。
    # 2026-10-04：跟 score_node 同一个教训——原来 data 的结构校验（pass 字段/
    # feedback/corrections 形状）整段都写在重试循环外面，一旦模型这次输出的 JSON
    # 解析成功但形状不对（比如漏了 pass 字段），会直接从这里抛出去，绕过重试，
    # 跟上次"Score data is incomplete or malformed"直接从 app.py 顶层冒出来是同一类问题。
    # 这版把形状校验挪进循环里，当成"这次没发挥好"一起重试；corrections 内容是否跟
    # 已有 scorer_output 对得上（下面 duplicate/old_score 那两个检查）属于另一类问题——
    # 真出现大概率是 prompt 本身有逻辑漏洞，不是"模型手滑"，所以保留在循环外，不重试。
    last_error = None
    data = None
    for _attempt in range(2):
        raw = _call_llm(api_key, model, system_prompt, "Output the review result JSON:")
        if not raw.strip():
            last_error = ValueError("Critic call returned no text output")
            continue
        try:
            # critic 输出是单个 JSON 对象，复用 scoring.py 的鲁棒解析（去围栏/抠对象）
            candidate = scoring._parse_json_response(raw)
        except json.JSONDecodeError as e:
            last_error = e
            continue
        if not isinstance(candidate, dict) or type(candidate.get("pass")) is not bool:
            last_error = ValueError("The critic result's pass field must be a boolean")
            continue
        feedback = candidate.get("feedback", [])
        corrections = candidate.get("corrections", [])
        if (not isinstance(feedback, list) or not all(isinstance(f, str) for f in feedback)
            or not isinstance(corrections, list)
            or any(not isinstance(c, dict) or not isinstance(c.get("dimension"), str)
                   or type(c.get("new_score")) is not int or not 1 <= c["new_score"] <= 5
                   or not isinstance(c.get("reason", ""), str) for c in corrections)):
            last_error = ValueError("Critic correction data is malformed")
            continue
        data = candidate
        break
    if data is None:
        raise RuntimeError(
            "In-depth assessment's critic step did not return a valid result after one "
            "automatic retry. Please re-run the assessment later."
        ) from last_error
    feedback = data.get("feedback", [])
    corrections = data.get("corrections", [])

    # 直接改分：把 corrections 合并进 scorer_output
    scorer_output = state.get("scorer_output") or {}
    applied = []
    seen = set()
    for correction in corrections:
        key = correction['dimension']
        current = (scorer_output.get('dimensions') or {}).get(key)
        if key in seen or not isinstance(current, dict):
            raise ValueError("Critic corrections contain duplicate or unknown dimensions")
        if 'old_score' in correction and correction['old_score'] != current.get('score'):
            raise ValueError("A correction's old_score does not match the scoring result")
        seen.add(key)
    if corrections:
        dims = {k: dict(v or {})
                for k, v in (scorer_output.get("dimensions") or {}).items()}
        for c in corrections:
            d, new_score = c.get("dimension"), c.get("new_score")
            if d in dims and type(new_score) is int and 1 <= new_score <= 5:
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
    out = _validated_output(state.get("scorer_output"), state.get("field") or {})
    dims = out["dimensions"]
    scores = {k: (v or {}).get("score", 0) for k, v in dims.items()}
    field = state.get("field") or {}
    framework = scoring.load_framework()
    resume_text = state.get("resume_text", "")

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
        # 和老链路 scoring.score_resume() 一样的确定性引用校验：
        # 渲染层（网页/PDF）靠这个字段决定打不打"[未核实]"标记。
        # 之前漏了这个字段，导致 Agent 模式下所有引用都被标成未核实。
        "dimension_evidence_verified": {
            k: [scoring._quote_in_resume(resume_text, q)
                for q in ((v or {}).get("evidence") or [])]
            for k, v in dims.items()
        },
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
