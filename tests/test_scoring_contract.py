"""Offline regressions: no API calls and no real student data."""
import copy
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
import scoring


def fixture():
    return {'dimensions': {d['key']: {'score': 3, 'rationale': '示例依据', 'evidence': ['示例项目']} for d in scoring.load_framework()['dimensions']},
            'ats_keywords': [], 'vague_phrases': [], 'strong_phrases': [],
            'strengths': ['示例优势'], 'gaps': ['示例建议'], 'bonus_checked': [], 'stage_note': ''}


def response(raw, stop='end_turn'):
    return NS(content=[NS(type='text', text=raw)],stop_reason=stop,usage=NS(input_tokens=10,output_tokens=10))


class ScoringContract(unittest.TestCase):
    def setUp(self):
        self.framework=scoring.load_framework()
        self.field=scoring.get_field(self.framework,'teacher')

    def run_responses(self, responses):
        client=MagicMock()
        client.messages.create.side_effect=responses
        with patch.object(scoring.anthropic,'Anthropic',return_value=client):
            result=scoring.score_resume(resume_text='示例项目',field_id='teacher',api_key='offline-test')
        return result,client

    def test_bad_json_then_valid(self):
        result,client=self.run_responses([response('{"dimensions": {"edu": {"score":3 "rationale":"bad"}}}'), response(json.dumps(fixture()))])
        self.assertEqual(client.messages.create.call_count,2)
        self.assertEqual(result['total'],60)
        self.assertEqual(client.messages.create.call_args.kwargs['output_config']['format']['type'],'json_schema')

    def test_two_failures_have_actionable_error(self):
        with self.assertRaisesRegex(RuntimeError,'已自动重试一次'):
            self.run_responses([response('{broken'),response('{broken')])

    def test_truncation_retries(self):
        result,_=self.run_responses([response('{','max_tokens'),response(json.dumps(fixture()))])
        self.assertEqual(result['total'],60)

    def test_missing_text_retries(self):
        result,_=self.run_responses([NS(content=[],stop_reason='end_turn'),response(json.dumps(fixture()))])
        self.assertEqual(result['total'],60)

    def test_invalid_values_never_default_scores(self):
        variations=[]
        for value in [None,True,'3',0,6,3.5]:
            data=fixture();data['dimensions']['edu']['score']=value;variations.append(data)
        data=fixture();del data['dimensions']['edu'];variations.append(data)
        data=fixture();data['strengths']='wrong type';variations.append(data)
        data=fixture();data['bonus_checked']=[True];variations.append(data)
        for data in variations:
            with self.subTest(data=data),self.assertRaises(scoring._InvalidScoreResponseError):
                scoring._validate_score_data(data,self.framework,self.field)

    def test_text_blocks_joined(self):
        raw=json.dumps(fixture());r=response(raw)
        r.content=[NS(type='text',text=raw[:50]),NS(type='text',text=raw[50:])]
        result,_=self.run_responses([r]);self.assertEqual(result['total'],60)

    def test_refusal_not_retried(self):
        with self.assertRaisesRegex(RuntimeError,'确认上传的是简历'):
            self.run_responses([response('refused','refusal')])

    def test_fences_and_quotes(self):
        data=fixture();data['dimensions']['edu']['rationale']='quoted "x" and {braces}'
        self.assertEqual(scoring._parse_json_response('```json\n'+json.dumps(data)+'\n```'),data)

if __name__=='__main__': unittest.main()
