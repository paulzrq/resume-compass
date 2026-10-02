import json,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from agents import nodes
from agents.graph import build_graph
import scoring
from test_agent_graph import _base_state,_scorer_json,_plan_json,_FakeScorerAgent
class AgentRegressions(unittest.TestCase):
 def test_literal_control_character(self):
  self.assertEqual(scoring._parse_json_response('{"quote":"A\tB"}')['quote'],'A\tB')
  with self.assertRaises(json.JSONDecodeError):scoring._parse_json_response('{"quote":"unterminated}')
 def test_duplicate_plan_dimension_rejected(self):
  plan=json.loads(_plan_json());plan[-1]=plan[0]
  with patch.object(nodes,'_call_llm',return_value=json.dumps(plan)):
   with self.assertRaises(ValueError):nodes.plan_node(_base_state(),'test')
 def test_missing_dimension_and_duplicate_bonus_rejected(self):
  state=_base_state();out=_scorer_json();del out['dimensions']['edu'];state['scorer_output']=out
  with self.assertRaises(scoring._InvalidScoreResponseError):nodes.report_node(state)
  out=_scorer_json();out['bonus_checked']=[0,0];state['scorer_output']=out
  with self.assertRaises(scoring._InvalidScoreResponseError):nodes.report_node(state)
 def test_quote_flags_preserve_positions(self):
  state=_base_state();state['resume_text']='真实证据';state['scorer_output']=_scorer_json()
  state['scorer_output']['dimensions']['edu']['evidence']=['','真实证据','假证据']
  self.assertEqual(nodes.report_node(state)['final_result']['dimension_evidence_verified']['edu'],[False,True,False])
 def test_critic_rejects_string_bool_and_boolean_score(self):
  state=_base_state();state['scorer_output']=_scorer_json()
  for output in [{'pass':'false','feedback':[]},{'pass':False,'corrections':[{'dimension':'edu','new_score':True}]}]:
   with patch.object(nodes,'_call_llm',return_value=json.dumps(output)):
    with self.assertRaises(ValueError):nodes.critique_node(state,'test')
 def test_graph_retains_eval_snapshots(self):
  import agents.graph as gm
  original=nodes.score_node
  def record(*args,**kwargs):
   out=original(*args,**kwargs);out['eval_snapshots']=[out['scorer_output']];return out
  def llm(api_key,model,system_prompt,user_text,max_tokens=4000):
   return json.dumps({'pass':True,'feedback':[]}) if 'review auditor' in system_prompt else _plan_json()
  with patch.object(gm,'score_node',record),patch.object(nodes,'_call_llm',llm):
   state=build_graph('test',scorer_agent=_FakeScorerAgent(),max_revisions=1).invoke(_base_state())
  self.assertEqual(len(state['eval_snapshots']),1)
if __name__=='__main__':unittest.main()
