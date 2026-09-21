import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
from web_report import report_html,DIM_ORDER
class WebReportTests(unittest.TestCase):
 def test_content_escaping_and_missing_verification(self):
  r=dict(field={'id':'custom','name':'<领域>'},total=78,framework={'dimensions':[{'key':k,'name':k} for k in DIM_ORDER]},dimension_scores={k:4 for k in DIM_ORDER},dimension_rationale={k:'内容<script>alert(1)</script>' for k in DIM_ORDER},dimension_evidence={'edu':['证据1','证据2']},dimension_evidence_verified={'edu':[True]},strengths=['优势'],gaps=['建议'],stage_note='阶段说明')
  html=report_html(r,'<学生>')
  self.assertNotIn('<script>',html)
  self.assertIn('&lt;学生&gt;',html)
  self.assertIn('[未核实] 原文：“证据2”',html)
  self.assertIn('阶段说明',html)
  self.assertEqual(html.count('<section class="dimension">'),7)
  self.assertNotIn('\n',html)
if __name__=='__main__':unittest.main()
