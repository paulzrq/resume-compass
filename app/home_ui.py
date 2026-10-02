"""Presentation-only English home screen; scoring keeps canonical field IDs."""
import base64
from html import escape
import streamlit as st
from mascots import mascot_path
FIELD_LABELS = {'swe': 'Software Engineer', 'ds': 'Data Scientist', 'mle': 'Machine Learning Engineer', 'engineering': 'Hardware Engineer', 'cybersecurity': 'Cybersecurity Engineer', 'robotics': 'Robotics / Automation Engineer', 'fintech_eng': 'Fintech Engineer', 'finance': 'Investment Banking Analyst', 'risk_analyst': 'Risk Analyst', 'actuary': 'Actuary', 'consulting': 'Consultant', 'marketing': 'Marketing', 'pm': 'Product Manager', 'ba': 'Business Analyst', 'ops': 'Supply Chain Management', 'data_analyst': 'Data Analyst', 'accounting': 'Accountant / Auditor', 'hr': 'Human Resources', 'sales': 'Sales', 'law': 'Lawyer', 'clinical_research': 'Clinical Research Assistant', 'media': 'Media / Public Relations', 'film_production': 'Film Producer', 'teacher': 'Teacher', 'ux': 'UI/UX Designer', 'graphic_design': 'Graphic Designer', 'architecture': 'Architect', 'game_design': 'Game Designer'}
CATEGORY_LABELS = {"技术与工程":"Technology & Engineering","商业与管理":"Business & Management","专业服务与内容":"Professional Services & Content","设计与创意":"Design & Creative"}
CSS = '\n.home-hero{text-align:center;padding:38px 0 34px;color:inherit}.home-hero h2{font-size:clamp(30px,4.2vw,52px)!important;letter-spacing:-1.8px;font-weight:700;padding:0!important;line-height:1.12}.home-hero p{font-size:18px;opacity:.62;margin-top:16px}\n.st-key-home_workspace{padding:32px;border-radius:24px;background:var(--background-color,#fff);box-shadow:0 8px 40px #15233708;border:1px solid #88888812}\n.st-key-home_workspace [data-testid="stFileUploaderDropzone"]{min-height:220px;border:1px dashed #9aa9ba66;border-radius:16px;display:flex;flex-direction:column;justify-content:center;gap:18px;align-items:center;background:#8b9aaa08}\n.st-key-home_workspace [data-testid="stFileUploaderDropzoneInstructions"]{display:flex!important;align-items:center;text-align:center}\n.st-key-home_workspace [data-testid="stFileUploaderDropzone"] button{color:#0071e3;border-radius:30px;border:1px solid #0071e322}\n.st-key-home_workspace [data-testid="stBaseButton-primary"]{background:#0071e3;border:0;border-radius:28px;min-height:48px;padding:10px 32px;font-weight:600;color:white}\n.st-key-home_workspace [data-testid="stBaseButton-primary"]:disabled{opacity:.4}\n.home-section{font-size:21px;font-weight:650;margin:0 0 18px}.home-mascot{text-align:center;padding:18px 0}.home-mascot img{width:240px;max-width:85%;height:230px;object-fit:contain}.home-mascot strong{display:block;font-size:20px}.home-mascot small{opacity:.6}.home-foot{text-align:center;font-size:12px;opacity:.55;padding-top:20px}\n@media(max-width:640px){.home-hero{padding:24px 0}.home-hero h2{letter-spacing:-1px}.home-hero p{font-size:15px}.st-key-home_workspace{padding:18px 14px;border-radius:20px}.st-key-home_workspace [data-testid="stFileUploaderDropzone"]{min-height:160px}.home-mascot{padding:8px}.home-mascot img{width:140px;height:135px}.home-section{font-size:18px}.st-key-home_workspace [data-testid="stBaseButton-primary"]{width:100%}}\n'

CSS += '\n.st-key-home_workspace [data-testid="stFileUploaderDropzone"]::before{content:"Drop your resume here";font-size:18px;font-weight:600;display:block;margin-top:14px}.st-key-home_workspace [data-testid="stFileUploaderDropzone"]>div{align-items:center;justify-content:center}\n'

def render_home_intro():
    st.markdown('<style>'+CSS+'</style><div class="home-hero"><h2>Give your resume direction.</h2><p>Upload your resume, choose a career path, and get your assessment.</p></div>', unsafe_allow_html=True)

def career_label(field):
    return CATEGORY_LABELS.get(field.get('category'), 'Other')+' / '+FIELD_LABELS.get(field['id'],field['name'])

def render_career(field):
    path=mascot_path(field['id'])
    art=''
    if path:
        data=base64.b64encode(path.read_bytes()).decode('ascii')
        art=f'<img alt="Selected career illustration" src="data:image/png;base64,{data}">'
    name=escape(FIELD_LABELS.get(field['id'],field['name']))
    st.markdown(f'<div class="home-mascot">{art}<strong>{name}</strong><small>Selected career path</small></div>',unsafe_allow_html=True)
