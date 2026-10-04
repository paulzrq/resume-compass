"""Scorer ReAct agent 可用的 tools。

- get_dimension_rubric / verify_quote：纯查表/纯字符串比对，已实现（脚手架）。
- search_jd_library：v1 关键词检索已实现（Paul 定 C 方案：planner 生成英文
  jd_queries，英文查英文；标题命中双倍权重；无结果返回空字符串）。
  v2 可换成向量检索（embedding + 余弦相似度），面试可讲。
"""
from functools import lru_cache
import scoring
from langchain_core.tools import tool


def build_tools(resume_text: str) -> list:
    """按本次简历组装 tools。verify_quote 需要闭包绑定简历原文，
    所以 tools 不能在 build_graph 时一次性建好，必须每次打分现组装。"""
    framework = scoring.load_framework()

    # Cache only within this assessment; never share resume data across sessions.
    load_jd = lru_cache(maxsize=8)(scoring.load_jd_reference)

    @tool
    def get_dimension_rubric(dimension_key: str) -> str:
        """取某一个评分维度的评分锚点（1-5 分每一档的标准）。
        dimension_key 取值：edu / exp / proj / skill / cert / lead / present。
        打分前先查锚点，避免凭印象给分。
        """
        for d in framework["dimensions"]:
            if d["key"] == dimension_key:
                lines = [f"维度：{d['name']}（{d['desc']}）"]
                for i, a in enumerate(d["anchors"], start=1):
                    lines.append(f"{i}分：{a}")
                return "\n".join(lines)
        return f"未知维度：{dimension_key}，可用维度：edu/exp/proj/skill/cert/lead/present"

    @tool
    def search_jd_library(field_id: str, query: str, k: int = 2,
                          max_chars: int = 1200) -> str:
        """在 JD 参考库里检索与 query 最相关的片段，用于校准打分。
        field_id：目标领域 id（如 swe）；query：自然语言问题，
        如"企业对项目经历看重什么"；k：最多返回几个片段；
        max_chars：每条片段最多保留字符数（超长截断，控制 ReAct 历史膨胀——
        单条 JD 摘录原文约 1800 字符，top3 返回约 6000 字符，会在后续每步重发）。
        返回：带来源标记的 JD 摘录拼接文本，找不到返回空字符串。
        """
        # TODO(Paul)：手写实现。建议路线：
        #   v1（先跑通）：按 field_id 找到 jd-reference-library/<field_id>/*.md，
        #       用关键词重合度（query 分词后在每条 "## JD N" 片段里的命中数）排序取 top-k。
        #       复用 scoring.load_jd_reference(field_id) 读原文，
        #       注意先按 "## JD " 切条，内部笔记段落（"Implications for Our Framework"）
        #       不要返回——复用 scoring._strip_internal_notes。
        #   v2（加分项）：换成向量检索（embedding + 余弦相似度），面试可讲。
        # 契约：返回字符串；无结果时返回 ""（不要抛异常，scorer 会继续）。
        k = max(1, min(k, 2))
        max_chars = max(1, min(max_chars, 1200))
        md = load_jd(field_id)  # 复用：已去内部笔记、已限条数
        if not md.strip():
            return ""
        entries = md.split("\n## JD ")[1:]  # 按条目切分，第一段是 H1 标题
        if not entries:
            return ""
        # v1：英文关键词子串匹配。约定 query 为英文（planner 负责生成英文
        # jd_queries，因为 JD 库是英文的）——Paul 2026-09-28 选 C 方案。
        qwords = [w.lower() for w in query.replace(",", " ").split()
                  if len(w) > 2]
        scored = []
        for e in entries:
            text = e.lower()
            hits = sum(1 for w in qwords if w in text)
            title = e.split("\n", 1)[0].lower()
            title_hits = sum(1 for w in qwords if w in title)
            scored.append((hits + title_hits, e))  # 标题命中算双倍权重
        scored.sort(key=lambda x: x[0], reverse=True)
        top = [e for s, e in scored[:k] if s > 0]
        if not top:
            return ""  # 无结果返回空字符串，不抛异常
        out = []
        for e in top:
            text = "## JD " + e.strip()
            if len(text) > max_chars:
                text = text[:max_chars] + "...(truncated)"
            out.append(text)
        return "\n\n---\n\n".join(out)

    @tool
    def verify_quote(quote: str) -> bool:
        """校验一段引用是否逐字出现在简历原文里（防幻觉）。
        打分时摘录的每条 evidence，打完分后用这个 tool 抽查；
        返回 False 的引用必须删掉或重找，不能留进报告。
        """
        # 直接复用 scoring.py 的现成校验（含"PDF 提取丢空格"的兜底逻辑）
        return scoring._quote_in_resume(resume_text, quote)

    @tool
    def verify_quotes(quotes: list[str]) -> list[bool]:
        """Verify up to 21 evidence quotes in one call; results preserve input order.
        Submit all dimensions together. False means remove or re-source the quote.
        """
        if len(quotes) > 21:
            raise ValueError("Submit at most 21 quotes per batch")
        return [scoring._quote_in_resume(resume_text, q) for q in quotes]

    return [get_dimension_rubric, search_jd_library, verify_quote, verify_quotes]
