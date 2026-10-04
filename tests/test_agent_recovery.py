"""Offline coverage of malformed and interrupted In-depth responses."""
import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from test_agent_graph import _base_state, _scorer_json, _plan_json
from agents import nodes


def message(data, **kwargs):
    return {'messages': [SimpleNamespace(content=json.dumps(data), **kwargs)]}


class RecoveryTests(unittest.TestCase):
    def test_invalid_schema_retry_gets_feedback(self):
        bad = _scorer_json()
        del bad['dimensions']['edu']
        agent = Mock()
        agent.invoke.side_effect = [message(bad), message(_scorer_json())]
        result = nodes.score_node(_base_state(), 'test', scorer_agent=agent)
        self.assertEqual(len(result['scorer_output']['dimensions']), 7)
        retry = agent.invoke.call_args.args[0]['messages']
        self.assertIn('edu', retry[-1].content)
        self.assertIn('Exact output JSON schema', retry[0].content[0]['text'])

    def test_empty_truncated_and_pending_responses_retry(self):
        for bad in [
            {'messages': []},
            message(_scorer_json(), response_metadata={'stop_reason': 'max_tokens'}),
            message(_scorer_json(), tool_calls=[{'name': 'verify_quote'}]),
        ]:
            with self.subTest(bad=bad):
                agent = Mock()
                agent.invoke.side_effect = [bad, message(_scorer_json())]
                nodes.score_node(_base_state(), 'test', scorer_agent=agent)
                self.assertEqual(agent.invoke.call_count, 2)

    def test_invalid_json_diagnostic_does_not_include_resume(self):
        agent = Mock()
        agent.invoke.return_value = {'messages': [SimpleNamespace(content='PRIVATE RESUME TEXT')]}
        with self.assertRaises(RuntimeError) as error:
            nodes.score_node(_base_state(), 'test', scorer_agent=agent)
        self.assertNotIn('PRIVATE RESUME', str(error.exception))
        self.assertEqual(agent.invoke.call_count, 2)

    def test_planner_nonstring_strategy_recovers(self):
        bad = json.loads(_plan_json())
        bad[0]['strategy'] = []
        with patch.object(nodes, '_call_llm', side_effect=[json.dumps(bad), _plan_json()]):
            self.assertEqual(len(nodes.plan_node(_base_state(), 'test')['plan']), 7)
