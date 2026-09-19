"""
简历罗盘 · 打分逻辑
负责：加载 framework.json、拼装评分 prompt、调用 Anthropic API、
      用 Python 确定性地计算加权总分（不信任模型做算术）。
"""
import json
import re
import statistics
from pathlib import Path

import anthropic

APP_DIR = Path(__file__).resolve().parent
FRAMEWORK_PATH = APP_DIR.parent / "framework.json"
JD_LIBRARY_DIR = APP_DIR.parent / "jd-reference-library"

# 2026-09-19：每个领域每天自动+1条JD，库会一直涨（现在9条/领域，最多能涨到35条/领域才自动暂停），
# 如果每次打分都把某个领域全部JD塞进prompt，成本会跟着JD库无限期一起涨。这里限定"每次打分最多
# 注入这个领域最近的N条JD"，节流的是"喂给模型的量"，不影响"jd-reference-library/里存了多少"——
# 库本身照常积累、README里的统计口径不变，只是喂给模型时抽最近的一部分做参考，防止成本失控。
MAX_JD_ENTRIES_PER_FIELD = 6

DEFAULT_MODEL = "claude-sonnet-5"
CHEAP_MODEL = "claude-haiku-4-5-20251001"


def load_framework() -> dict:
    return json.loads(FRAMEWORK_PATH.read_text(encoding="utf-8"))


def get_field(framework: dict, field_id: str) -> dict:
    for f in framework["fields"]:
        if f["id"] == field_id:
            return f
    raise ValueError(f"未知领域 id: {field_id}")


CUSTOM_FIELD_ID = "custom"

# 29个预设领域权重的平均值，四舍五入到刚好凑成100，作为自定义方向没有专属权重时的通用兜底。
_CUSTOM_FIELD_WEIGHTS = {"edu": 14, "exp": 25, "proj": 22, "skill": 15, "cert": 6, "lead": 9, "present": 9}


def make_custom_field(name: str) -> dict:
    """用户自己输入的自定义求职方向、且没能自动匹配到任何现有领域时的兜底方案：
    权重用现有29个预设领域权重的平均值，加分项和短板留空，
    靠模型自己对这个方向在真实招聘市场上的通用理解来打分（严谨程度天然低于预设领域/成功匹配的情况）。"""
    name = name.strip()
    if not name:
        raise ValueError("自定义方向名称不能为空")
    return {
        "id": CUSTOM_FIELD_ID,
        "name": name,
        "category": "用户自定义",
        "weights": dict(_CUSTOM_FIELD_WEIGHTS),
        "bonus": [],
        "gaps": [],
    }


_DIM_KEYS = ["edu", "exp", "proj", "skill", "cert", "lead", "present"]


def _round_weights_to_100(raw_weights: dict) -> dict:
    """把一组浮点权重四舍五入成整数，同时保证总和精确等于100（最大余数法）：
    先每个都向下取整，再把因为取整损失掉的份额，按小数部分从大到小分给对应维度补1。
    直接对每个维度独立四舍五入的话，总和可能变成99或101，会破坏"总分=按权重加权求和"这个不变量。"""
    floors = {k: int(v) for k, v in raw_weights.items()}
    remainder = 100 - sum(floors.values())
    order = sorted(raw_weights.keys(), key=lambda k: raw_weights[k] - floors[k], reverse=True)
    for k in order[:remainder]:
        floors[k] += 1
    return floors


def match_custom_field_to_existing(name: str, framework: dict, api_key: str, top_k: int = 3):
    """用一次很便宜的分类调用（固定用 CHEAP_MODEL，跟主打分用什么模型无关），
    把用户自己输入的自定义方向匹配到最多 top_k 个现有的预设领域，并给出归一化后（加起来等于1）的匹配权重，
    供后续按比例融合这些领域的权重/加分项/短板/JD参考。
    返回 (matches, usage)：matches 是 [(field_id, weight), ...]，按weight从高到低排序；
    匹配失败/解析不出有效结果时 matches 是空列表，调用方应该在这种情况下退回到 make_custom_field
    的通用兜底权重，而不是让整个打分失败。usage 是这次分类调用实际花掉的token（哪怕匹配失败，
    只要真的发起了调用就有花费，要如实计入总成本，不能因为匹配失败就当这次调用没发生过）。"""
    empty_usage = {k: 0 for k in _USAGE_KEYS}
    valid_ids = {f["id"] for f in framework["fields"]}
    field_list_desc = "\n".join(f"- {f['id']}：{f['name']}（{f['category']}）" for f in framework["fields"])
    system_prompt = (
        "你是一个职业方向分类助手。给你一个学生自己描述的目标求职方向，"
        "以及一份预先定义好的职业领域列表（每项包含id、名称、所属大类）。\n"
        "请判断这个自定义方向，最多由列表里的1到3个现有领域按怎样的比例组合最能代表它，"
        "组合权重要大致反映\"这个自定义方向在多大程度上等同于/接近该现有领域\"，所有权重之和必须等于1。\n"
        "如果这个自定义方向其实就是列表里某个领域换了个说法（高度重合），可以只给1个领域、权重1.0；"
        "如果确实是几个领域的交叉/复合方向，给2-3个最相关的即可，不相关或关联很弱的领域不要硬凑进来。\n\n"
        f"预设领域列表：\n{field_list_desc}\n\n"
        "只输出一个JSON对象作为回复，不要有任何其他文字、不要用markdown代码块包裹：\n"
        '{"matches":[{"field_id":"列表里的某个id","weight":0到1之间的数字},...]}'
    )
    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CHEAP_MODEL,
            max_tokens=512,
            system=system_prompt,
            messages=[{"role": "user", "content": f"自定义方向：{name}"}],
            output_config={"effort": "low"},
        )
        usage = _extract_usage(response)
        raw_text = None
        for block in response.content:
            if getattr(block, "type", None) == "text":
                raw_text = block.text
                break
        if raw_text is None:
            return [], usage
        data = _parse_json_response(raw_text)
    except Exception:
        # 分类调用失败（网络问题/解析失败/模型返回异常等）不应该导致整个打分失败，
        # 上层会在收到空列表时自动退回通用兜底权重。这种情况下我们没拿到真实usage（可能压根没发出去、
        # 也可能是解析response阶段就出错了），如实记成0，不编造数字。
        return [], dict(empty_usage)

    matches = []
    for m in (data.get("matches") or [])[: top_k]:
        if not isinstance(m, dict):
            continue
        fid = m.get("field_id")
        weight = m.get("weight")
        if fid in valid_ids and isinstance(weight, (int, float)) and weight > 0:
            matches.append((fid, float(weight)))
    if not matches:
        return [], usage
    total_weight = sum(w for _, w in matches)
    matches = [(fid, w / total_weight) for fid, w in matches]
    matches.sort(key=lambda x: x[1], reverse=True)
    return matches, usage


def make_blended_custom_field(name: str, framework: dict, matches: list) -> dict:
    """按 match_custom_field_to_existing 给出的匹配权重，把对应的现有领域的权重/加分项/短板融合成
    一个专属于这个自定义方向的临时field对象：
    - 7个维度权重：各现有领域权重按匹配权重加权求和，再用最大余数法保证四舍五入后总和精确等于100
    - 加分项/短板：直接把匹配到的现有领域的加分项/短板都汇总进来（按匹配权重从高到低排列，同名去重）——
      不按权重打折扣，因为最终能不能拿到分还是要看简历里有没有实际证据支持，加分项列表只是"可能相关"的候选
    - matched_fields：记录这次具体匹配到了哪些领域、各自权重，用于展示给用户 & 传给 build_system_prompt 说明"""
    fields_by_id = {f["id"]: f for f in framework["fields"]}
    raw_weights = {k: 0.0 for k in _DIM_KEYS}
    bonus, gaps = [], []
    seen_bonus_labels, seen_gaps = set(), set()
    for fid, w in matches:
        f = fields_by_id[fid]
        for k in _DIM_KEYS:
            raw_weights[k] += w * f["weights"][k]
        for label, pts in f["bonus"]:
            if label not in seen_bonus_labels:
                seen_bonus_labels.add(label)
                bonus.append([label, pts])
        for g in f["gaps"]:
            if g not in seen_gaps:
                seen_gaps.add(g)
                gaps.append(g)
    return {
        "id": CUSTOM_FIELD_ID,
        "name": name,
        "category": "用户自定义",
        "weights": _round_weights_to_100(raw_weights),
        "bonus": bonus,
        "gaps": gaps,
        "matched_fields": [
            {"field_id": fid, "name": fields_by_id[fid]["name"], "weight": w} for fid, w in matches
        ],
    }


def load_blended_jd_reference(matches: list, framework: dict) -> str:
    """按匹配到的现有领域，把它们各自积累的真实JD参考库内容拼接起来（标明来源领域和匹配度），
    没有匹配到任何领域、或匹配到的领域都没有JD参考文件时返回空字符串。"""
    fields_by_id = {f["id"]: f for f in framework["fields"]}
    parts = []
    for fid, w in matches:
        content = load_jd_reference(fid)
        if not content:
            continue
        field_name = fields_by_id[fid]["name"]
        parts.append(f"### 参考领域：{field_name}（与该自定义方向的匹配度约{round(w * 100)}%）\n\n{content}")
    return "\n\n---\n\n".join(parts)


def resolve_field(framework: dict, field_id: str, api_key: str = None, custom_field_name: str = None):
    """解析出本次打分实际要用的field对象和对应的JD参考文本，返回 (field, jd_reference, match_usage)。
    - 非自定义方向：照常从framework.json里按field_id查，match_usage 是 None（没有发起过分类调用）。
    - 自定义方向：先用一次便宜的分类调用，把用户输入的方向匹配到最多3个现有领域并按比例融合出
      权重/加分项/短板，JD参考库内容也按同样的匹配比例从这几个领域的真实JD参考里融合进来；
      匹配失败或没匹配出结果时，退回 make_custom_field 的通用兜底权重（不注入JD参考）。
      match_usage 是这次分类调用花掉的token（哪怕匹配失败也如实记录，因为调用确实发生了、要花钱），
      调用方（score_resume）需要把它并进最终返回的usage总量里，不然费用展示会少算这一小笔。
    这个函数只应该在 score_resume 里调用一次（不要放进多次重复调用的 _score_resume_once 里），
    否则稳定性模式下2次调用可能匹配到不同的领域组合，'2次打分依据的field定义不一致'会让取中位数失去意义。"""
    if not custom_field_name:
        field = get_field(framework, field_id)
        jd_reference = load_jd_reference(field_id)
        return field, jd_reference, None

    if api_key:
        matches, match_usage = match_custom_field_to_existing(custom_field_name, framework, api_key)
    else:
        matches, match_usage = [], None
    if matches:
        field = make_blended_custom_field(custom_field_name, framework, matches)
        jd_reference = load_blended_jd_reference(matches, framework)
    else:
        field = make_custom_field(custom_field_name)
        jd_reference = ""
    return field, jd_reference, match_usage


def _strip_internal_notes(md_text: str) -> str:
    """去掉"Implications for Our Framework"这类内部笔记段落，只保留真实JD的要求内容——
    那些启示是留给我们自己校准框架用的，不需要喂给模型（省token、也避免模型
    被"框架设计笔记"这种元信息干扰它对简历本身的判断）。兼容新旧两种标题写法。"""
    sections = md_text.split("\n## ")
    kept = [sections[0]]  # 第一段是 H1 标题，保留
    internal_headers = ("Implications for Our Framework", "对我们框架的启示")
    for sec in sections[1:]:
        if sec.strip().startswith(internal_headers):
            continue
        kept.append(sec)
    return "\n## ".join(kept).strip()


def _cap_jd_entries(md_text: str, max_entries: int) -> str:
    """只保留最近的 max_entries 条JD记录（按文件里"## JD N: ..."出现的顺序，取最后N条——
    JD编号是追加时按写入顺序递增的，所以"最后N条"就是"最新的N条"）。H1标题始终保留。
    条目数本来就不超过上限时原样返回，不做任何改动。"""
    sections = md_text.split("\n## JD ")
    header = sections[0]
    entries = sections[1:]
    if len(entries) <= max_entries:
        return md_text
    kept = entries[-max_entries:]
    return header + "".join(f"\n## JD {e}" for e in kept)


def load_jd_reference(field_id: str) -> str:
    """读取该领域已积累的真实JD参考文件（jd-reference-library/<field_id>/*.md，
    每个领域一个专属子文件夹），没有的话返回空字符串（不影响正常打分，只是少一份参考）。
    喂给模型之前会按 MAX_JD_ENTRIES_PER_FIELD 只取最近的一部分——库本身不受影响，
    只是控制每次打分实际注入prompt的量，见上面常量定义处的说明。"""
    field_dir = JD_LIBRARY_DIR / field_id
    if not field_dir.is_dir():
        return ""
    matches = sorted(field_dir.glob("*.md"))
    if not matches:
        return ""
    parts = [
        _cap_jd_entries(_strip_internal_notes(p.read_text(encoding="utf-8")), MAX_JD_ENTRIES_PER_FIELD)
        for p in matches
    ]
    return "\n\n---\n\n".join(parts)


def build_system_prompt(framework: dict, field: dict, jd_reference: str = "") -> str:
    lines = [
        "你是一名资深职业发展顾问，正在使用「简历罗盘」框架评估一名学生简历在特定领域的求职竞争力。",
        "请严格按照以下7个通用维度的评分锚点，为这份简历逐项打1-5分。",
        "对每个维度，请按「先摘证据、再写理由、最后打分」的顺序思考：先从简历原文里找出与该维度直接相关的具体语句"
        "（逐字摘录，不要翻译、不要改写、不要概括），再基于这些证据写一句不超过35字的中文理由（一句话说清楚就好，不用展开），最后才给出1-5分。"
        "如果某个维度确实没有相关证据（比如简历完全没提到），evidence可以是空数组，但要在理由里说明缺什么；"
        "找到证据不代表就该打高分，证据的数量、含金量、与该维度的实际关联度都要纳入判断，"
        "打分要基于简历原文的实际证据，不要臆测简历中没有写明的信息，证据不充分时倾向打更保守的分数。",
        "",
        "## 七个通用维度与评分锚点",
    ]
    for d in framework["dimensions"]:
        lines.append(f"\n### {d['name']}（{d['desc']}）")
        for i, a in enumerate(d["anchors"], start=1):
            lines.append(f"{i}分：{a}")

    lines.append(f"\n## 当前目标领域：{field['name']}")
    if field["bonus"]:
        lines.append("该领域的加分项（仅在简历中有明确证据支持时才勾选，不要臆测，宁缺毋滥）：")
        for i, item in enumerate(field["bonus"]):
            label, pts = item
            lines.append(f"[{i}] {label}（+{pts}分）")
    if field["gaps"]:
        lines.append("\n该领域常见短板参考（供你判断是否适用于这份简历）：")
        for g in field["gaps"]:
            lines.append(f"- {g}")
    if field["id"] == CUSTOM_FIELD_ID:
        matched_fields = field.get("matched_fields")
        if matched_fields:
            match_desc = "、".join(f"{m['name']}({round(m['weight'] * 100)}%)" for m in matched_fields)
            lines.append(
                "\n注意：「" + field["name"] + "」是用户自己输入的自定义方向，不在我们预先校准好的领域库里。"
                f"我们把它按相似程度匹配到了这些现有领域并按比例融合出了上面的权重/加分项/短板：{match_desc}。"
                "这种融合只是一个近似，实际判断时请你结合这个自定义方向本身的特点来取舍——"
                "如果某个融合进来的加分项/短板明显不适用于这个具体方向，不要生搬硬套，"
                "仍以你对这个方向真实情况的理解为准。"
            )
        else:
            lines.append(
                "\n注意：「" + field["name"] + "」是用户自己输入的自定义方向，不在我们预先校准好的领域库里，"
                "没有为它准备加分项清单、常见短板参考或真实招聘JD摘录，上面给的权重也只是通用兜底值。"
                "请你依据自己对这个具体方向在真实招聘市场上通常看重什么的理解来打分——"
                "比如相关专业背景、有代表性的实习或项目经历、需要的核心技能/工具、是否需要相关资质证书、"
                "以及这类岗位常见的简历呈现方式，尽量贴近这个方向的实际情况，不要套用一个笼统通用的标准，"
                "也不用因为没有加分项清单就完全不给加分——如果简历里有明显契合该方向的突出经历，"
                "可以直接体现在相应维度的分数和理由里。"
            )

    if jd_reference:
        lines.append(
            "\n## 该领域的真实岗位JD参考（供你校准判断，不是硬性checklist）"
        )
        lines.append(
            "以下是我们过往收集的该领域真实招聘JD摘录及对框架的分析笔记。"
            "打分时可以参考这些JD里体现出的\"企业实际看重什么\"来校准你的判断"
            "（例如：某项能力在真实JD里是硬性门槛还是加分项、某类经历是否被企业认可为等效经验），"
            "但不要把某份JD的具体条目当成这份简历必须满足的清单——不同公司、不同细分方向的要求本就有差异，"
            "以下内容仅供辅助判断，最终仍以本框架给出的7维度锚点和该领域的权重/加分项/短板为准。"
        )
        lines.append(f"\n{jd_reference}")

    lines.append(
        "\n注意：如果简历显示学生处于本科早期阶段（大一大二），评分应按同阶段学生的合理预期校准，"
        "不要用应届生标准苛责，但要在 stage_note 字段里如实说明这一点；如果看不出年级信息，stage_note 留空字符串。"
    )

    lines.append(
        "\n另外请额外提取 ats_keywords：逐字摘自简历原文、对ATS（招聘方简历筛选系统）有帮助的关键词或短语"
        "（比如工具/技能名称、职位相关术语、证书名称），最多10条，去重，不要输出整句话。"
        "如果上面提供了该领域的真实JD参考，请优先挑选那些JD里反复出现、招聘方明显看重的词汇是否在这份简历里也逐字出现过；"
        "没有JD参考时凭你对该领域招聘惯例的判断来挑。这些词必须是简历原文里真实存在的，不能生成简历里没有的词。"
    )

    lines.append(
        "\n另外请额外提取 vague_phrases：范围是简历里除了「教育背景/Education」和「技能/Skills」之外的所有部分"
        "（比如工作经历/实习经历、项目经历、领导力与课外活动、荣誉奖项、个人总结等），按以下两个条件联合判断："
        "(1) 以弱动词/被动式开头——这类动词只说明\"做了这件事\"，但看不出判断力、方法深度或个人贡献，"
        "比如\"参与了\"\"协助\"\"负责\"\"参加了\"\"帮助\"\"支持\"\"使用了\""
        "\"involved in\"\"assisted with\"\"participated in\"\"helped with\"\"responsible for\"\"supported\"\"used\"\"utilized\""
        "这类，不要泛化到\"conducted\"\"performed\"\"handled\"\"worked on\"\"开展\"\"进行了\"\"处理\"这种更常见于扎实描述里的动词；"
        "且 (2) 这句话里没有任何具体数字、比例、规模或明确产出——"
        "也就是看不出量化的影响或结果。两个条件都满足才算命中。"
        "如果弱动词后面其实跟了具体数据/规模/职责范围（比如\"参与了一个日活500万用户的项目，独立负责推荐算法模块\"），"
        "不算，不要标。\n"
        "命中之后，不要摘录整句话，只摘出问题所在的那个具体片段就够了——通常就是弱动词开头到第一个逗号/自然停顿处"
        "（大概几个字到十几个字，不超过一句话的一半），不需要整句。\n"
        "最多6条，逐字摘自原文，不要生成简历里没有的内容；如果这些部分本身写得具体、没有这类问题，"
        "vague_phrases就返回空数组，不要为了凑数硬挑。"
    )

    lines.append(
        "\n另外请额外提取 strong_phrases：范围是简历里除了「教育背景/Education」和「技能/Skills」之外的所有部分"
        "（比如工作经历/实习经历、项目经历、领导力与课外活动、荣誉奖项、个人总结等），"
        "摘录其中写得好、有说服力的量化成果片段——"
        "判断标准是：包含具体数字/比例/规模/明确产出，"
        "并且清楚说明了是谁、用什么方法做到的（是\"动作+可衡量的结果\"这种组合，不是孤立的一个数字）。\n"
        "摘录范围以包住那个核心数字/产出和紧邻的说明文字为准，不需要摘整句，但也不用像vague_phrases那样卡得很短，"
        "只要一看就知道\"这是一条写得好的量化成果\"就行。\n"
        "最多6条，逐字摘自原文，不要生成简历里没有的内容；如果这些部分本身没有这类量化成果，"
        "strong_phrases就返回空数组，不要为了凑数硬挑一般般的内容。"
    )

    lines.append(
        "\n另外请提取 strengths（简历的核心优势）和 gaps（短板/建议），"
        "分别最多4条，每条不超过25字，只挑最关键、最能体现这份简历在该领域竞争力的几点，不用穷举凑数。"
    )

    lines.append(
        "\n请只输出一个JSON对象作为回复，不要有任何其他文字、不要用markdown代码块包裹、不要输出解释。"
        "JSON结构如下（键名必须完全一致，7个维度键名固定为 edu/exp/proj/skill/cert/lead/present）：\n"
        '{"dimensions":{'
        '"edu":{"evidence":["逐字摘自简历原文的证据片段，最多2条，每条不超过30字，没有则为空数组"],'
        '"rationale":"简短依据，不超过35字，中文，一句话","score":1-5的整数},'
        '"exp":{"evidence":[...],"rationale":"...","score":1-5的整数},'
        '"proj":{"evidence":[...],"rationale":"...","score":1-5的整数},'
        '"skill":{"evidence":[...],"rationale":"...","score":1-5的整数},'
        '"cert":{"evidence":[...],"rationale":"...","score":1-5的整数},'
        '"lead":{"evidence":[...],"rationale":"...","score":1-5的整数},'
        '"present":{"evidence":[...],"rationale":"...","score":1-5的整数}},'
        '"ats_keywords":["..."],'
        '"vague_phrases":["..."],'
        '"strong_phrases":["..."],'
        '"bonus_checked":[适用的加分项index组成的数组，例如[0,2]，没有则为空数组],'
        '"strengths":["优势1","优势2"],'
        '"gaps":["短板/建议1","短板/建议2"],'
        '"stage_note":"如学生处于早期阶段则说明，否则为空字符串"}'
    )
    return "\n".join(lines)


def _extract_json_object(raw: str) -> str:
    """从原始文本里抠出第一个完整的花括号JSON对象——正常情况下raw本身就是干净的JSON，
    这一步基本是空操作；但模型偶尔会在JSON前后多带几个字（哪怕提示里明确说了不要），
    或者输出被max_tokens截断导致末尾缺右括号，这里做兜底：
    - 前后有多余文字：找到第一个'{'和跟它配对的'}'，只取中间这一段。
    - 结尾被截断（找不到配对的'}'）：原样返回，交给上层json.loads报错，
      调用方会识别出这是截断问题并给出针对性的报错信息，而不是被这里悄悄吞掉。"""
    start = raw.find("{")
    if start == -1:
        return raw
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return raw[start:]  # 没找到配对的右括号（很可能是被截断了），原样交给json.loads去报错


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if "\n" in raw:
            first_line, rest = raw.split("\n", 1)
            raw = rest if first_line.strip().lower() in ("json", "") else raw
    raw = _extract_json_object(raw.strip())
    return json.loads(raw)


def _normalize_for_match(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _quote_in_resume(resume_text: str, quote: str) -> bool:
    """粗略校验一段摘录是否真的出现在简历原文里（忽略空白差异），
    用来抓模型编造证据/关键词的情况——不额外调API，纯字符串比对。

    2026-09-19：加了一道"去掉所有空白再比一次"的兜底。起因是有些PDF（尤其某些模板/字体导出的）
    经pdfplumber提取后，某些片段的单词之间会丢失空格（比如"Designed and implemented"被提取成
    "Designedandimplemented"），这时模型摘录证据时是按人类正常读法给出带空格的引用，
    跟原文这段"粘连"文本逐字比对必然对不上，会把货真价实的简历内容误判成"编造证据"。
    先按原来的方式（保留单个空格）比一次，不命中的话再各自去掉全部空白比一次——
    只在直接匹配失败时才启用，不会让真正编造的证据变得更容易蒙混过关，纯粹是修正
    "PDF提取丢空格"这一类误报。"""
    if not quote or not quote.strip():
        return False
    normalized_quote = _normalize_for_match(quote)
    normalized_resume = _normalize_for_match(resume_text)
    if normalized_quote in normalized_resume:
        return True
    stripped_quote = normalized_quote.replace(" ", "")
    stripped_resume = normalized_resume.replace(" ", "")
    return bool(stripped_quote) and stripped_quote in stripped_resume


_USAGE_KEYS = ["input_tokens", "output_tokens", "thinking_tokens", "cache_creation_tokens", "cache_read_tokens"]


def _extract_usage(response) -> dict:
    """从一次API响应里提取token用量，包括缓存相关的两个字段——
    cache_creation_tokens：这次调用把多少输入token写进了缓存（按缓存写入价计费，比原价贵一点）；
    cache_read_tokens：这次调用命中缓存、直接读的有多少token（按缓存读取价计费，是原价的一折）。
    没有开缓存或者没命中的情况下这两个值就是0，跟以前的行为完全一样。"""
    usage = getattr(response, "usage", None)
    thinking_tokens = 0
    if usage is not None:
        details = getattr(usage, "output_tokens_details", None)
        thinking_tokens = getattr(details, "thinking_tokens", 0) or 0
    return {
        "input_tokens": getattr(usage, "input_tokens", None) if usage else None,
        "output_tokens": getattr(usage, "output_tokens", None) if usage else None,
        "thinking_tokens": thinking_tokens,
        "cache_creation_tokens": (getattr(usage, "cache_creation_input_tokens", 0) or 0) if usage else 0,
        "cache_read_tokens": (getattr(usage, "cache_read_input_tokens", 0) or 0) if usage else 0,
    }


def _merge_usage(usage_list: list) -> dict:
    """把多次调用（比如稳定性模式的2次打分调用，加上自定义方向那次分类匹配调用）的token用量加总，
    忽略列表里的 None（比如没做过自定义方向匹配时 match_usage 是 None）。"""
    usage_list = [u for u in usage_list if u]
    return {k: sum((u.get(k) or 0) for u in usage_list) for k in _USAGE_KEYS}


class _TruncatedResponseError(RuntimeError):
    """模型输出在打分JSON写完整之前就被max_tokens截断了——不是"没打完分"的业务问题，
    是这次调用本身没跑完，值得重试一次（重试是全新的一次调用，不是接着写）。"""


def _score_resume_once(
    resume_text: str,
    field: dict,
    jd_reference: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    cache_resume: bool = False,
) -> dict:
    """在 _score_resume_once_attempt 外面包一层重试：JSON解析失败/输出被截断这两类问题，
    本质上是"这一次调用没有正常产出完整结果"，而不是简历或参数有问题，重试一次全新的调用
    通常就能拿到正常结果（这也是"有时候评估会失败"最常见的成因——尤其是稳定性模式下，
    2次调用里只要有1次踩到就会让整个评估失败，重试一次能大幅降低用户实际感知到的失败率）。
    其他类型的报错（比如API key无效、网络彻底不通）重试没有意义，直接原样抛出。

    cache_resume 透传给 _score_resume_once_attempt，见那边的说明。"""
    last_error = None
    for attempt in range(2):
        try:
            return _score_resume_once_attempt(
                resume_text, field, jd_reference, api_key, model=model, cache_resume=cache_resume
            )
        except (json.JSONDecodeError, _TruncatedResponseError) as e:
            last_error = e
            continue
    raise last_error


def _score_resume_once_attempt(
    resume_text: str,
    field: dict,
    jd_reference: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    cache_resume: bool = False,
) -> dict:
    """单次调用API打一次分，返回结构和以前的 score_resume 完全一样。
    Claude Sonnet 5 已经取消了 temperature/top_p/top_k 这几个采样参数（设成非默认值会直接400报错），
    没有办法在API层面强制"完全确定性输出"。所以稳定性改由外层的 score_resume 通过多次调用取中位数来实现，
    这个函数只负责老老实实地跑一次、如实返回这一次的结果。
    field/jd_reference 由调用方（score_resume）通过 resolve_field 提前解析好再传进来，
    这里不再自己查/自己判断是不是自定义方向——自定义方向的"匹配现有领域"这一步只应该做一次，
    不能每次调用都重新匹配一遍，否则2次调用可能匹配到不同的领域组合，取中位数就没有意义了。"""
    framework = load_framework()
    system_prompt = build_system_prompt(framework, field, jd_reference)

    client = anthropic.Anthropic(api_key=api_key)
    response = client.messages.create(
        model=model,
        # 7个维度的证据+理由，加上ATS关键词/优缺点等額外字段，用中文和"先摘证据再打分"的结构写完整，
        # 偶尔会逼近甚至超过4096token导致JSON在写完前被截断（这也是"有时候评估失败"的一个主因）。
        # 调大上限本身不增加实际花费——按真实产出的token计费，不是按这个上限——只是给"写得比较完整"
        # 的正常输出留够空间，不会被半路打断。
        max_tokens=8000,
        # system prompt标成可缓存（cache_control）：即使是单次调用（runs=1，现在的默认），
        # system prompt这部分（框架+当前领域的JD参考，通常占输入token的大头）也不是每次都白白多花钱——
        # 只要连续评估同一个领域的多份不同简历，5分钟缓存有效期内后面几份还是能命中缓存按1折计费，
        # 只有第一份要多付1.25倍的"写缓存"费用，纯粹是省钱，不影响打分内容和质量。
        #
        # 简历原文则相反：只有 cache_resume=True（稳定性模式，runs>1，同一份简历真的会被
        # 原样再发一次）时才标缓存——单次调用模式下这份简历只发一次、以后不会再原样发第二次，
        # 标了缓存反而白白多付1.25倍"写缓存"费用却永远用不上这份缓存，是纯浪费。
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"以下是学生简历原文，请按上述标准评分：\n\n{resume_text}",
                        **({"cache_control": {"type": "ephemeral"}} if cache_resume else {}),
                    }
                ],
            }
        ],
        # 按固定评分标准打分属于结构化任务，不需要模型深度自由推理，
        # 用 low 档位大幅减少"思考"消耗的token（思考token按输出token计费，之前偏贵的主因）。
        # 现在改成"先摘证据再打分"的JSON结构后，证据摘录本身承担了一部分"想清楚再下结论"的作用，
        # 如果发现打分质量明显下降，可以改成 "medium" 再试。
        output_config={"effort": "low"},
    )
    raw_text = None
    for block in response.content:
        if getattr(block, "type", None) == "text":
            raw_text = block.text
            break
    if raw_text is None:
        raise ValueError("模型响应中没有文本内容（可能只返回了思考过程），请重试")
    if getattr(response, "stop_reason", None) == "max_tokens":
        # 输出没写完JSON就被截断了——不要直接扔给json.loads产出一句看不懂的解析错误，
        # 用专门的异常类型标出来，外层_score_resume_once会据此自动重试一次。
        raise _TruncatedResponseError(
            "模型输出在打分JSON写完整之前就达到了max_tokens上限，这次响应不完整"
        )
    data = _parse_json_response(raw_text)

    dim_keys = [d["key"] for d in framework["dimensions"]]
    dims_raw = data.get("dimensions", {})
    scores, rationale, evidence, evidence_verified = {}, {}, {}, {}
    for key in dim_keys:
        entry = dims_raw.get(key, {}) or {}
        scores[key] = int(entry.get("score", 3))
        rationale[key] = entry.get("rationale", "")
        ev_list = [q for q in (entry.get("evidence") or []) if isinstance(q, str) and q.strip()]
        evidence[key] = ev_list
        evidence_verified[key] = [_quote_in_resume(resume_text, q) for q in ev_list]

    weights = field["weights"]
    base = sum(weights[k] * (scores[k] / 5) for k in weights)

    bonus_checked = [i for i in data.get("bonus_checked", []) if 0 <= i < len(field["bonus"])]
    bonus_pts = min(sum(field["bonus"][i][1] for i in bonus_checked), 10)

    total = round(min(100, base + bonus_pts))
    tier = next(t for t in framework["tiers"] if total >= t["min"])

    ats_keywords_raw = [
        kw.strip() for kw in (data.get("ats_keywords") or []) if isinstance(kw, str) and kw.strip()
    ]
    ats_keywords, ats_keywords_unverified = [], []
    for kw in ats_keywords_raw:
        (ats_keywords if _quote_in_resume(resume_text, kw) else ats_keywords_unverified).append(kw)

    vague_phrases_raw = [
        s.strip() for s in (data.get("vague_phrases") or []) if isinstance(s, str) and s.strip()
    ]
    vague_phrases, vague_phrases_unverified = [], []
    for s in vague_phrases_raw:
        (vague_phrases if _quote_in_resume(resume_text, s) else vague_phrases_unverified).append(s)

    strong_phrases_raw = [
        s.strip() for s in (data.get("strong_phrases") or []) if isinstance(s, str) and s.strip()
    ]
    strong_phrases, strong_phrases_unverified = [], []
    for s in strong_phrases_raw:
        (strong_phrases if _quote_in_resume(resume_text, s) else strong_phrases_unverified).append(s)

    return {
        "framework": framework,
        "field": field,
        "dimension_scores": scores,
        "dimension_rationale": rationale,
        "dimension_evidence": evidence,
        "dimension_evidence_verified": evidence_verified,
        "ats_keywords": ats_keywords,
        "ats_keywords_unverified": ats_keywords_unverified,
        "vague_phrases": vague_phrases,
        "vague_phrases_unverified": vague_phrases_unverified,
        "strong_phrases": strong_phrases,
        "strong_phrases_unverified": strong_phrases_unverified,
        "bonus_checked": bonus_checked,
        "strengths": data.get("strengths", []),
        "gaps": data.get("gaps", []),
        "stage_note": data.get("stage_note", ""),
        "total": total,
        "tier_label": tier["label"],
        "raw_model_output": raw_text,
        "usage": _extract_usage(response),
    }


def score_resume(
    resume_text: str,
    field_id: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
    runs: int = 1,
    custom_field_name: str = None,
) -> dict:
    """稳定性包装层：同一份简历调用 _score_resume_once 多次，减少单次调用的随机波动。
    2026-09-19起默认改成 runs=1（单次调用，不做稳定性聚合）——2次取中位数虽然更稳，但费用翻倍，
    默认更看重便宜；app.py里"稳定性模式"勾选框打开时会显式传 runs=2，按需换稳定性。
    Claude Sonnet 5 已取消 temperature 等采样参数，API层面没法强制"完全确定性输出"，
    多次取中位数是目前能做到的最接近"稳定"的办法，代价是API调用次数/费用变成 runs 倍。

    聚合规则：
    - 每个维度的分数，取 runs 次结果里的中位数（用 median_low，保证中位数一定是某一次的真实取值，
      不会出现"4.5分"这种两次结果硬平均出来、实际上谁都没打过的分数）。
    - 每个维度的理由/证据文字，从"这次结果里该维度分数正好等于中位数"的那一次里取，
      保证界面上显示的分数和旁边解释它的文字，永远出自同一次模型输出，不会对不上。
    - 不分维度的内容（ATS关键词、优缺点、加分项等），从"整体最接近中位数"的那一次里整体取，
      保证这些内容是同一次、自洽的完整输出，而不是把几次结果的只言片语硬拼在一起。
    - 总分用中位数分数重新按权重算一遍（而不是两次总分的中位数），这样报告里"总分"和"各维度分数"
      之间的加权关系始终能对得上，用户自己按框架权重验证也验证得通。

    runs<=1 时跳过整套聚合逻辑，等价于以前的单次调用（调试用，不会多花钱）。

    custom_field_name 非空时，代表用户自己输入了一个不在预设领域库里的方向：会先用一次单独的、
    很便宜的分类调用（见 resolve_field/match_custom_field_to_existing），把这个方向匹配到最多3个
    现有领域并按比例融合出权重/加分项/短板/JD参考，只做这一次、结果被后面 runs 次打分共用——
    不能让每次打分都各自重新匹配一遍，否则2次可能匹配到不同的领域组合，取中位数就失去意义了。
    """
    framework = load_framework()
    field, jd_reference, match_usage = resolve_field(
        framework, field_id, api_key, custom_field_name=custom_field_name
    )

    if runs <= 1:
        result = _score_resume_once(resume_text, field, jd_reference, api_key, model=model, cache_resume=False)
        if match_usage:
            result["usage"] = _merge_usage([match_usage, result["usage"]])
        return result

    results = [
        _score_resume_once(resume_text, field, jd_reference, api_key, model=model, cache_resume=True)
        for _ in range(runs)
    ]

    dim_keys = list(results[0]["dimension_scores"].keys())
    median_scores = {
        k: statistics.median_low([r["dimension_scores"][k] for r in results])
        for k in dim_keys
    }

    rationale, evidence, evidence_verified = {}, {}, {}
    for k in dim_keys:
        source = next(r for r in results if r["dimension_scores"][k] == median_scores[k])
        rationale[k] = source["dimension_rationale"][k]
        evidence[k] = source["dimension_evidence"][k]
        evidence_verified[k] = source["dimension_evidence_verified"][k]

    def _distance(r):
        return sum(abs(r["dimension_scores"][k] - median_scores[k]) for k in dim_keys)

    rep_idx = min(range(runs), key=lambda i: _distance(results[i]))
    rep = results[rep_idx]

    field = rep["field"]
    framework = rep["framework"]
    weights = field["weights"]
    base = sum(weights[k] * (median_scores[k] / 5) for k in weights)
    bonus_pts = min(sum(field["bonus"][i][1] for i in rep["bonus_checked"]), 10)
    total = round(min(100, base + bonus_pts))
    tier = next(t for t in framework["tiers"] if total >= t["min"])

    return {
        "framework": framework,
        "field": field,
        "dimension_scores": median_scores,
        "dimension_rationale": rationale,
        "dimension_evidence": evidence,
        "dimension_evidence_verified": evidence_verified,
        "ats_keywords": rep["ats_keywords"],
        "ats_keywords_unverified": rep["ats_keywords_unverified"],
        "vague_phrases": rep["vague_phrases"],
        "vague_phrases_unverified": rep["vague_phrases_unverified"],
        "strong_phrases": rep["strong_phrases"],
        "strong_phrases_unverified": rep["strong_phrases_unverified"],
        "bonus_checked": rep["bonus_checked"],
        "strengths": rep["strengths"],
        "gaps": rep["gaps"],
        "stage_note": rep["stage_note"],
        "total": total,
        "tier_label": tier["label"],
        "raw_model_output": rep["raw_model_output"],
        "usage": _merge_usage(([match_usage] if match_usage else []) + [r["usage"] for r in results]),
        "stability": {
            "runs": runs,
            "representative_run_index": rep_idx,
            "dimension_scores_all_runs": [r["dimension_scores"] for r in results],
            "total_all_runs": [r["total"] for r in results],
        },
    }
