"""app_adapter 测试：零 API 花费（fake scorer + mock _call_llm）。

验证 run_agent_assessment 返回的 result dict 能被 app.py 结果页直接消费：
- 结果页直接读的键：field / framework / total / tier_label / dimension_scores /
  dimension_rationale / dimension_evidence / raw_model_output
- .get() 读的键：ats_keywords / vague_phrases / strong_phrases / usage / stability
- agent 专属：agent_mode / plan / critic_corrections
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "app"))

import agents.nodes as nodes
from agents.app_adapter import run_agent_assessment


class _FakeMsg:
    def __init__(self, content):
        self.content = content


def _scorer_json():
    dims = {}
    for d in ["edu", "exp", "proj", "skill", "cert", "lead", "present"]:
        dims[d] = {"score": 4, "rationale": "理由" * 5,
                   "evidence": ["张三，软件工程师，三年经验。"]}
    return {"dimensions": dims, "bonus_checked": [],
            "strengths": ["强项"], "gaps": ["短板"], "stage_note": ""}


class _FakeScorerAgent:
    def invoke(self, _input):
        return {"messages": [_FakeMsg(json.dumps(_scorer_json()))]}


def _plan_json():
    return json.dumps([
        {"dimension": d, "strategy": "anchor_only", "jd_queries": []}
        for d in ["edu", "exp", "proj", "skill", "cert", "lead", "present"]
    ])


def _fake_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
    if "review auditor" in system_prompt:  # critic：直接放行
        return json.dumps({"pass": True, "feedback": []})
    return _plan_json()  # planner


class TestAppAdapter(unittest.TestCase):
    def test_result_keys_cover_result_page(self):
        fake = _FakeScorerAgent()
        with patch.object(nodes, "_call_llm", _fake_llm):
            result = run_agent_assessment(
                resume_text="张三，软件工程师，三年经验。",
                field_id="swe",
                api_key="x",
                scorer_agent=fake,
            )
        # 结果页直接读的键（缺一即 KeyError 崩溃）
        for key in ("field", "framework", "total", "tier_label",
                    "dimension_scores", "dimension_rationale",
                    "dimension_evidence", "raw_model_output"):
            self.assertIn(key, result, f"缺键 {key} 会导致结果页崩溃")
        # agent 专属
        self.assertTrue(result["agent_mode"])
        self.assertEqual(len(result["plan"]), 7)
        self.assertTrue(result["critic_pass"])
        self.assertIsInstance(result["total"], int)
        self.assertEqual(len(result["dimension_scores"]), 7)
        # raw_model_output 是可读 JSON（含 agent 轨迹）
        trace = json.loads(result["raw_model_output"])
        self.assertIn("critic_corrections", trace)


if __name__ == "__main__":
    unittest.main()
