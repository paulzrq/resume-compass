"""search_jd_library v1 关键词检索的离线测试：不调真实 API。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from agents.tools import build_tools


def _search():
    tools = build_tools("张三，软件工程师，三年经验。")
    return next(t for t in tools if t.name == "search_jd_library")


class JdSearchTest(unittest.TestCase):
    def test_english_query_hits(self):
        out = _search().invoke({"field_id": "swe",
                                "query": "internship programming language requirements",
                                "k": 3})
        self.assertTrue(out.strip())
        self.assertIn("## JD", out)

    def test_no_result_returns_empty(self):
        out = _search().invoke({"field_id": "swe",
                                "query": "zzzzqqqq nonexistentkeyword",
                                "k": 3})
        self.assertEqual(out, "")

    def test_unknown_field_returns_empty(self):
        out = _search().invoke({"field_id": "no_such_field",
                                "query": "internship", "k": 3})
        self.assertEqual(out, "")

    def test_internal_notes_excluded(self):
        out = _search().invoke({"field_id": "swe", "query": "internship",
                                "k": 5})
        self.assertNotIn("Implications for Our Framework", out)
        self.assertNotIn("对我们框架的启示", out)


if __name__ == "__main__":
    unittest.main()
