import sys,io,json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'app'),str(ROOT/'tests')]
from unittest.mock import patch
from pathlib import Path
from streamlit.testing.v1 import AppTest
import report,scoring,share_card,emailer
from test_scoring_contract import fixture,response
from unittest.mock import MagicMock
client=MagicMock();client.messages.create.return_value=response(json.dumps(fixture()))
with patch.object(scoring.anthropic,'Anthropic',return_value=client):
 result=scoring.score_resume(resume_text='示例项目',field_id='teacher',api_key='test')
app=AppTest.from_file(str(ROOT/'app/app.py'),default_timeout=30)
for k,v in dict(view='result',result=result,report_unlocked=False,assessment_id='offline-test',student_name='虚构测试',student_meta='',resume_pdf_bytes=None,resume_original_bytes=None,resume_original_filename=None).items():app.session_state[k]=v
with patch.object(share_card,'render_share_button',return_value=False) as share,patch.object(report,'generate_pdf',wraps=report.generate_pdf) as pdf,patch.object(emailer,'send_report_email',return_value='skipped') as mail,patch.object(Path,'write_bytes',return_value=0):
 app.run();assert not app.exception,list(app.exception);assert pdf.call_count==0;assert mail.call_count==0
 print('PASS locked actual app: no PDF, no email')
 share.return_value=True
 app.run();assert not app.exception,list(app.exception);assert pdf.call_count==1;assert mail.call_count==1
 print('PASS share click actual app: PDF generated once')
 b,info=app.session_state['report_artifact'];assert b.startswith(b'%PDF')
 import pymupdf
 doc=pymupdf.open(stream=b,filetype='pdf');assert len(doc)>0
 assert '虚构测试' in ''.join(p.get_text() for p in doc)
 png=doc[0].get_pixmap(matrix=pymupdf.Matrix(1,1)).tobytes('png')
 share.return_value=False
 app.run();assert not app.exception;assert pdf.call_count==1;assert mail.call_count==1
 print('PASS rerun: no repeated PDF generation or email')
 app.button[0].click().run();assert not app.exception;assert app.session_state['view']=='form'
 print('PASS return to form')
# Rendering is checked in memory; no student artifacts are written.
print('PASS PDF readable and student text correct')
