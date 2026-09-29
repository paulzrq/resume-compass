"""Agent 图的离线测试：不调真实 API、不需要 API key。

约定沿用 tests/ 现有风格：unittest + sys.path 插入 app 目录。
"""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

import scoring
from agents import build_graph
from agents import nodes


def _scorer_json():
    dims = {d['key']: {'score': 3, 'rationale': '示例依据',
                       'evidence': ['示例项目']}
            for d in scoring.load_framework()['dimensions']}
    return {'dimensions': dims, 'ats_keywords': [], 'vague_phrases': [],
            'strong_phrases': [], 'strengths': ['示例优势'], 'gaps': ['示例建议'],
            'bonus_checked': [], 'stage_note': ''}


class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeScorerAgent:
    """替代真实 ReAct agent：直接返回写死的 scorer JSON。"""

    def __init__(self):
        self.calls = 0

    def invoke(self, _input):
        self.calls += 1
        return {'messages': [_FakeMsg(json.dumps(_scorer_json()))]}


def _base_state():
    framework = scoring.load_framework()
    return {'resume_text': '张三，软件工程师，三年经验。',
            'field_id': 'swe',
            'field': scoring.get_field(framework, 'swe'),
            'revision_round': 0}


def _plan_json():
    return json.dumps([
        {"dimension": d, "strategy": "anchor_only", "jd_queries": []}
        for d in ["edu", "exp", "proj", "skill", "cert", "lead", "present"]
    ])


def _planner_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
    """planner 专用 mock：prompt 已实现，走真实 plan_node 解析逻辑。"""
    return _plan_json()


class AgentGraphTest(unittest.TestCase):
    def test_graph_compiles(self):
        graph = build_graph(api_key='test-key-not-used')
        self.assertIsNotNone(graph)

    def test_planner_prompt_contract(self):
        """真实 planner prompt：能渲染、包含 5 种策略、输出可被 plan_node 解析。"""
        from agents import prompts
        framework = scoring.load_framework()
        field = scoring.get_field(framework, 'swe')
        text = prompts.build_planner_prompt('张三，软件工程师，三年经验。', field)
        for s in ['anchor_only', 'jd_grounded', 'deep_dive',
                  'conservative_skip', 'cross_check']:
            self.assertIn(s, text)
        self.assertIn(field['name'], text)
        # plan_node 用 mock LLM 走真实解析路径
        with patch.object(nodes, '_call_llm', _planner_llm):
            out = nodes.plan_node(
                {'resume_text': 'x', 'field': field}, api_key='x')
        self.assertEqual(len(out['plan']), 7)

    def test_scorer_prompt_contract(self):
        """scorer prompt：能渲染、包含 plan、覆盖 5 种策略与输出格式。"""
        from agents import prompts
        framework = scoring.load_framework()
        field = scoring.get_field(framework, 'swe')
        plan = [{"dimension": "proj", "strategy": "jd_grounded",
                 "jd_queries": ["SWE 岗位对项目经历中最看重哪些能力"]}]
        text = prompts.build_scorer_prompt(field, plan)
        for s in ['anchor_only', 'jd_grounded', 'deep_dive',
                  'conservative_skip', 'cross_check']:
            self.assertIn(s, text)
        self.assertIn('SWE 岗位对项目经历中最看重哪些能力', text)  # plan 被嵌入
        for key in ['dimensions', 'ats_keywords', 'bonus_checked',
                    'stage_note', 'verify_quote']:
            self.assertIn(key, text)

    def test_critic_prompt_contract(self):
        """critic prompt v2：能渲染、含四类硬伤+校准规则+corrections 输出格式。"""
        from agents import prompts
        framework = scoring.load_framework()
        field = scoring.get_field(framework, 'swe')
        fake_output = {"dimensions": {"proj": {"score": 5, "rationale": "理由",
                                                "evidence": ["原文片段"]}}}
        text = prompts.build_critic_prompt(field, fake_output,
                                           resume_text="简历原文片段")
        for key in ['审查员', 'pass', 'feedback', 'corrections', '35',
                    '直接修正', '四类硬伤']:
            self.assertIn(key, text)
        self.assertIn('原文片段', text)  # scorer 输出被嵌入
        self.assertIn('简历原文片段', text)  # 简历原文被嵌入（可核验证据）
        self.assertIn('[edu]', text)  # 评分锚点被嵌入

    def _report_state(self, scores, bonus_checked=None):
        framework = scoring.load_framework()
        dims = {k: {"score": s, "rationale": "r", "evidence": ["e"]}
                for k, s in scores.items()}
        return {
            "scorer_output": {"dimensions": dims,
                              "bonus_checked": bonus_checked or [],
                              "ats_keywords": [], "vague_phrases": [],
                              "strong_phrases": [], "strengths": [],
                              "gaps": [], "stage_note": ""},
            "field": scoring.get_field(framework, "swe"),
            "plan": [], "critic_feedback": [], "revision_round": 1,
        }

    def test_report_node_total_and_tier(self):
        """全 3 分 → base=60、无加分 → total=60，定级"中等，需针对性提升"。"""
        scores = {k: 3 for k in
                  ["edu", "exp", "proj", "skill", "cert", "lead", "present"]}
        out = nodes.report_node(self._report_state(scores))["final_result"]
        self.assertEqual(out["total"], 60)
        self.assertEqual(out["tier_label"], "中等，需针对性提升")

    def test_report_node_cap_binds(self):
        """base=91 但短板 3 个（>2）→ 封顶 87，够不到"顶尖竞争力"。"""
        scores = {"edu": 5, "exp": 5, "proj": 5, "skill": 5,
                  "cert": 2, "lead": 2, "present": 2}
        out = nodes.report_node(self._report_state(scores))["final_result"]
        self.assertEqual(out["total"], 87)
        self.assertEqual(out["tier_label"], "有较强竞争力")

    def test_full_run_critic_passes(self):
        """planner(真实prompt+mock LLM) → score(fake) → critique(真实prompt+mock LLM放行) → report。"""
        fake = _FakeScorerAgent()

        def fake_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
            if '审查员' in system_prompt:  # critic 调用：直接放行
                return json.dumps({'pass': True, 'feedback': []})
            return _planner_llm(api_key, model, system_prompt, user_text)

        with patch.object(nodes, '_call_llm', fake_llm):
            graph = build_graph(api_key='x', scorer_agent=fake)
            out = graph.invoke(_base_state())
        self.assertEqual(fake.calls, 1)
        self.assertTrue(out['critic_pass'])
        self.assertIn('dimension_scores', out['final_result'])
        self.assertEqual(out['final_result']['dimension_scores']['edu'], 3)
        self.assertEqual(out['final_result']['revision_rounds'], 1)

    def test_critic_revision_loop(self):
        """critic 第一次不通过 → 回 score 修订 → 第二次通过。"""
        fake = _FakeScorerAgent()
        calls = {'n': 0}

        def fake_critic_prompt(field, scorer_output, resume_text=""):
            return 'CRITIC_MARKER'

        def fake_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
            if 'CRITIC_MARKER' not in system_prompt:
                return _plan_json()  # planner 调用
            calls['n'] += 1
            if calls['n'] == 1:
                return json.dumps({'pass': False,
                                   'feedback': ['proj 维度证据偏弱，请补充检索 JD 再打分']})
            return json.dumps({'pass': True, 'feedback': []})

        with patch.object(nodes.prompts, 'build_critic_prompt', fake_critic_prompt), \
             patch.object(nodes, '_call_llm', fake_llm):
            graph = build_graph(api_key='x', scorer_agent=fake)
            out = graph.invoke(_base_state())
        self.assertEqual(fake.calls, 2)  # 初次 + 一轮修订
        self.assertTrue(out['critic_pass'])
        self.assertEqual(out['final_result']['revision_rounds'], 2)

    def test_max_revisions_cap(self):
        """critic 一直不通过 → 最多跑 MAX_REVISIONS 次后强制 report。"""
        fake = _FakeScorerAgent()

        def fake_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
            if 'CRITIC_MARKER' not in system_prompt:
                return _plan_json()  # planner 调用
            return json.dumps({'pass': False, 'feedback': ['还不够好']})

        with patch.object(nodes.prompts, 'build_critic_prompt',
                          lambda f, o, r='': 'CRITIC_MARKER'), \
             patch.object(nodes, '_call_llm', fake_llm):
            graph = build_graph(api_key='x', scorer_agent=fake)
            out = graph.invoke(_base_state())
        self.assertEqual(fake.calls, nodes.MAX_REVISIONS)
        self.assertIn('final_result', out)

    def test_cheap_mode_no_revision(self):
        """max_revisions=1：critic 不通过也不修订，直接 report，反馈记入报告。"""
        fake = _FakeScorerAgent()

        def fake_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
            if 'CRITIC_MARKER' not in system_prompt:
                return _plan_json()  # planner 调用
            return json.dumps({'pass': False, 'feedback': ['还不够好']})

        with patch.object(nodes.prompts, 'build_critic_prompt',
                          lambda f, o, r='': 'CRITIC_MARKER'), \
             patch.object(nodes, '_call_llm', fake_llm):
            graph = build_graph(api_key='x', scorer_agent=fake, max_revisions=1)
            out = graph.invoke(_base_state())
        self.assertEqual(fake.calls, 1)  # 只打一次分，不修订
        self.assertFalse(out['critic_pass'])
        self.assertEqual(out['final_result']['revision_rounds'], 1)
        self.assertEqual(out['final_result']['critic_feedback'], ['还不够好'])

    def test_critic_v2_direct_correction(self):
        """critic v2：corrections 直接合并进分数，不触发 scorer 重跑。"""
        fake = _FakeScorerAgent()  # _scorer_json 给 edu 打 3 分

        def fake_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
            if 'CRITIC_MARKER' not in system_prompt:
                return _plan_json()  # planner 调用
            return json.dumps({
                'pass': False, 'feedback': [],
                'corrections': [{'dimension': 'edu', 'old_score': 3,
                                 'new_score': 4, 'reason': '证据够4分档'}],
            })

        with patch.object(nodes.prompts, 'build_critic_prompt',
                          lambda f, o, r='': 'CRITIC_MARKER'), \
             patch.object(nodes, '_call_llm', fake_llm):
            graph = build_graph(api_key='x', scorer_agent=fake,
                                max_revisions=2)
            out = graph.invoke(_base_state())
        self.assertEqual(fake.calls, 1)  # 直接改分，不回炉重跑
        self.assertFalse(out['critic_pass'])
        fr = out['final_result']
        self.assertEqual(fr['dimension_scores']['edu'], 4)  # 3→4 已合并
        self.assertIn('critic修正 3→4', fr['dimension_rationale']['edu'])
        self.assertEqual(len(fr['critic_corrections']), 1)
        self.assertEqual(fr['critic_corrections'][0]['new_score'], 4)

    def test_critic_v2_pass_no_correction(self):
        """critic v2 通过时：分数不动，corrections 为空。"""
        fake = _FakeScorerAgent()

        def fake_llm(api_key, model, system_prompt, user_text, max_tokens=4000):
            if 'CRITIC_MARKER' not in system_prompt:
                return _plan_json()
            return json.dumps({'pass': True,
                               'feedback': ['[edu] rationale 可精简'],
                               'corrections': []})

        with patch.object(nodes.prompts, 'build_critic_prompt',
                          lambda f, o, r='': 'CRITIC_MARKER'), \
             patch.object(nodes, '_call_llm', fake_llm):
            graph = build_graph(api_key='x', scorer_agent=fake,
                                max_revisions=1)
            out = graph.invoke(_base_state())
        self.assertTrue(out['critic_pass'])
        self.assertEqual(out['final_result']['dimension_scores']['edu'], 3)
        self.assertEqual(out['final_result']['critic_corrections'], [])


    def test_scorer_block_list_content(self):
        """回归：真模型开 thinking 时 AIMessage.content 是 block 列表
        （[{"type": "thinking", ...}, {"type": "text", "text": json}]），
        score_node 必须只取 text 块解析，不能 str() 整个列表。"""
        blocks = [
            {'type': 'thinking', 'thinking': '先想想', 'signature': 'sig'},
            {'type': 'text', 'text': '```json\n' + json.dumps(_scorer_json()) + '\n```'},
        ]

        class _BlockAgent:
            def invoke(self, _input):
                return {'messages': [_FakeMsg(blocks)]}

        out = nodes.score_node(_base_state(), api_key='x',
                               scorer_agent=_BlockAgent())
        self.assertEqual(out['scorer_output']['dimensions']['edu']['score'], 3)


if __name__ == '__main__':
    unittest.main()
