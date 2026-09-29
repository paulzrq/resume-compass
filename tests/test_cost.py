"""成本优化测试：零 API 花费。

1. search_jd_library 返回截断：每条片段 ≤ max_chars（+截断标记），
   防止 JD 长文本在 ReAct 历史里每步重发。
2. score_node 发出的 HumanMessage 带 cache_control breakpoint，
   指令+简历前缀后续步骤命中缓存按 1 折计费。

注意：cache 的真实命中率/省钱效果只能等有 API credit 后实测，
这里只验证标记被正确加上。
"""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import scoring
from agents import nodes
from agents.tools import build_tools


class _FakeMsg:
    def __init__(self, content):
        self.content = content


def _scorer_json():
    dims = {}
    for d in ["edu", "exp", "proj", "skill", "cert", "lead", "present"]:
        dims[d] = {"score": 4, "rationale": "理由" * 5, "evidence": ["证据原文"]}
    return {"dimensions": dims, "bonus_checked": [], "strengths": [],
            "gaps": [], "stage_note": ""}


class _CapturingScorerAgent:
    """捕获 invoke 输入、返回写死 JSON 的 fake agent。"""

    def __init__(self):
        self.seen_inputs = []

    def invoke(self, _input):
        self.seen_inputs.append(_input)
        return {"messages": [_FakeMsg(json.dumps(_scorer_json()))]}


class TestCostOptimizations(unittest.TestCase):
    def test_jd_search_truncates_long_entries(self):
        tools = build_tools("张三，软件工程师。")
        search = next(t for t in tools if t.name == "search_jd_library")
        out = search.invoke({"field_id": "swe", "query": "project experience",
                             "k": 3, "max_chars": 1200})
        self.assertTrue(out, "swe 库应有检索结果")
        for seg in out.split("\n\n---\n\n"):
            self.assertLessEqual(len(seg), 1200 + len("……（过长截断）"),
                                 f"片段超长未截断：{len(seg)} 字符")
        # 默认 max_chars=1200 生效
        out_default = search.invoke({"field_id": "swe",
                                     "query": "project experience"})
        for seg in out_default.split("\n\n---\n\n"):
            self.assertLessEqual(len(seg), 1200 + len("……（过长截断）"))

    def test_score_node_marks_cache_breakpoint(self):
        fake = _CapturingScorerAgent()
        framework = scoring.load_framework()
        nodes.score_node(
            {"resume_text": "张三，软件工程师，三年经验。",
             "field_id": "swe",
             "field": scoring.get_field(framework, "swe"),
             "revision_round": 0},
            api_key="x", scorer_agent=fake,
        )
        self.assertEqual(len(fake.seen_inputs), 1)
        msg = fake.seen_inputs[0]["messages"][0]
        content = msg.content
        self.assertIsInstance(content, list, "应为 block 列表以便加 cache_control")
        first = content[0]
        text = first["text"] if isinstance(first, dict) else first.text
        self.assertIn("张三", text, "简历原文应在缓存前缀里")
        cc = first.get("cache_control") if isinstance(first, dict) \
            else getattr(first, "cache_control", None)
        self.assertIsNotNone(cc, "缺少 cache_control 标记，省钱优化未生效")


if __name__ == "__main__":
    unittest.main()
