import unittest
from unittest.mock import patch
from test_agent_graph import _base_state
from agents.tools import build_tools
from agents.prompts import build_scorer_prompt

class DepthEfficiency(unittest.TestCase):
    def test_batch_matches_single_quote_checks(self):
        tools = {t.name:t for t in build_tools('Built a Python service. Led a team.')}
        quotes = ['Built a Python service.', 'missing', 'Led a team.']
        self.assertEqual(tools['verify_quotes'].invoke({'quotes':quotes}),
                         [tools['verify_quote'].invoke({'quote':q}) for q in quotes])

    def test_jd_caps_and_assessment_local_cache(self):
        md = '# Jobs' + ''.join('\n## JD '+str(i)+'\npython '*1000 for i in range(6))
        with patch('scoring.load_jd_reference', return_value=md) as load:
            tool = next(t for t in build_tools('') if t.name=='search_jd_library')
            result=tool.invoke({'field_id':'swe','query':'python','k':100,'max_chars':100000})
            self.assertEqual(len(result.split('\n\n---\n\n')),2)
            self.assertLess(len(result),2500)
            tool.invoke({'field_id':'swe','query':'other'})
            self.assertEqual(load.call_count,1)
            next(t for t in build_tools('') if t.name=='search_jd_library').invoke({'field_id':'swe','query':'python'})
            self.assertEqual(load.call_count,2)

    def test_all_anchors_are_supplied(self):
        import scoring
        prompt=build_scorer_prompt(_base_state()['field'],[])
        for d in scoring.load_framework()['dimensions']:
            for anchor in d['anchors']:
                self.assertIn(anchor,prompt)
        self.assertIn('verify_quotes',prompt)
