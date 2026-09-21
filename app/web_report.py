"""Responsive report preserving the existing PDF report's visual order and palette."""
import base64
from html import escape
from functools import lru_cache
from pathlib import Path
from branding import logo_svg_data_uri
from mascots import mascot_path

CSS = """
.rc-report, .rc-report *{box-sizing:border-box}
.rc-report{margin:0;background:#f5f6f7;color:#1b2733;font-family:Arial,"PingFang SC","Microsoft YaHei",sans-serif;font-size:16px}
.rc-report{max-width:1000px;margin:0 auto;background:white;padding:36px 50px 48px}
.rc-report .logo{display:block;width:185px;margin:0 0 32px}
.rc-report .summary{display:flex;align-items:center;justify-content:center;gap:36px;margin-bottom:40px}
.rc-report .mascot{width:140px;height:140px;object-fit:contain}
.rc-report .score-label{font-size:19px;color:#5a6b7d}
.rc-report .score{color:#1b4f91;font-size:62px;font-weight:700;line-height:1.2;margin:8px 0 12px}
.rc-report .score small{font-size:22px;color:#5a6b7d;font-weight:400}
.rc-report .field{font-size:18px;color:#1b4f91}
.rc-report h2{font-weight:400;font-size:20px;color:#1b4f91;margin:26px 0 10px}
.rc-report .rule{border-top:1px solid #c7d2de}
.rc-report .dimension{margin-bottom:20px}
.rc-report .label{display:flex;justify-content:space-between;font-size:17px;margin-bottom:17px}
.rc-report .value{color:#1b4f91}
.rc-report .band{height:18px;position:relative;background:linear-gradient(to right,#e8ecf2 0% 20%,#d3deeb 20% 40%,#aec5e0 40% 60%,#7fa3cc 60% 80%,#1b4f91 80% 100%)}
.rc-report .band i{position:absolute;top:-9px;transform:translateX(-50%);width:0;height:0;border-left:7px solid transparent;border-right:7px solid transparent;border-top:11px solid #1b2733}
.rc-report .scale{display:flex;justify-content:space-between;color:#5a6b7d;font-size:12px;margin-top:6px}
.rc-report p{margin:5px 0 0;line-height:1.5}
.rc-report .interpretation{display:grid;grid-template-columns:1fr 1fr;gap:30px}
.rc-report h3{font-size:17px;color:#1b4f91;font-weight:400;margin:0 0 10px}
.rc-report .interpretation p{font-size:15px}
.rc-report .note{border-top:1px solid #c7d2de;color:#5a6b7d;font-size:12px;margin-top:25px;padding-top:12px}
@media(max-width:600px){.rc-report{background:white}
.rc-report{padding:24px 20px 32px}
.rc-report .logo{width:135px;margin:0 0 22px}
.rc-report .summary{gap:16px;margin-bottom:30px}
.rc-report .mascot{width:105px;height:105px}
.rc-report .score-label{font-size:15px}
.rc-report .score{font-size:46px;margin:6px 0 8px}
.rc-report .score small{font-size:17px}
.rc-report .field{font-size:15px}
.rc-report .label{font-size:16px}
.rc-report .band{height:14px}
.rc-report h2{font-size:19px}
.rc-report .dimension{margin-bottom:24px}
.rc-report p{font-size:15px}
.rc-report .interpretation{grid-template-columns:1fr;gap:24px}}

.rc-report{width:100%;background:#fff;color:#1b2733;border-radius:0}.rc-report p{color:#1b2733}.rc-report .evidence{color:#5a6b7d;font-size:14px;margin:5px 0 0 8px}.rc-report .note{color:#5a6b7d}.rc-report .notice{color:#5a6b7d;font-size:14px;margin-bottom:16px}.rc-report .interpretation p{overflow-wrap:anywhere}.rc-report p,.rc-report .field{overflow-wrap:anywhere}
.rc-report h1,.rc-report h2,.rc-report h3{padding:0;letter-spacing:normal}.rc-report .summary>div{min-width:0}.rc-report .mascot{flex-shrink:0}@media(max-width:360px){.rc-report{padding-left:12px;padding-right:12px}.rc-report .mascot{width:90px}.rc-report .summary{gap:10px}}"""
DIM_ORDER = ("edu", "exp", "proj", "skill", "cert", "lead", "present")

def _text(value):
    return escape(str(value), quote=True).replace("\n", "<br>")

@lru_cache(maxsize=32)
def _mascot_uri(path):
    return "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode("ascii")

def report_html(result, student_name, student_meta=""):
    field = result["field"]
    path = mascot_path(field.get("id", ""))
    mascot = f'<img class="mascot" alt="职业人物" src="{_mascot_uri(str(path))}">' if path else ""
    meta = f'<p style="text-align:center">{_text(student_meta)}</p>' if student_meta else ""
    parts = [f'<style>{CSS}</style><article class="rc-report"><img class="logo" alt="仁港学院" src="{logo_svg_data_uri()}">{meta}<div class="summary">{mascot}<div><div class="score-label">综合竞争力得分</div><div class="score">{_text(result["total"])} <small>/100</small></div><div class="field">目标领域：{_text(field["name"])}</div></div></div><div class="rule"></div><h2>分项表现区间</h2>']
    if not result.get("evidence_verification_available", True):
        parts.append('<p class="notice">这份简历以图片形式上传，引用的原文片段无法逐字核对，请留意准确性。</p>')
    dims = {d["key"]: d for d in result["framework"]["dimensions"]}
    for key in DIM_ORDER:
        score = result["dimension_scores"][key]
        position = max(0, min(100, float(score) * 20))
        parts.append(f'<section class="dimension"><div class="label"><span>{_text(dims[key]["name"])}</span><span class="value">{_text(score)} / 5</span></div><div class="band" role="img" aria-label="{_text(dims[key]["name"])}：{_text(score)}分，满分5分"><i style="left:{position}%"></i></div><div class="scale"><span>薄弱</span><span>中等</span><span>顶尖</span></div><p>{_text(result["dimension_rationale"].get(key, ""))}</p>')
        verified = result.get("dimension_evidence_verified", {}).get(key, [])
        for i, quote in enumerate(result.get("dimension_evidence", {}).get(key, [])):
            mark = "" if i < len(verified) and verified[i] else "[未核实] "
            parts.append(f'<p class="evidence">{mark}原文：“{_text(quote)}”</p>')
        parts.append('</section>')
    parts.append('<h2>解读说明</h2><div class="interpretation">')
    for title, key in (("优势", "strengths"), ("建议提升方向", "gaps")):
        parts.append(f'<div><h3>{title}</h3>')
        parts.extend(f'<p>· {_text(item)}</p>' for item in result.get(key, []))
        parts.append('</div>')
    disclaimer = '本报告依据「简历罗盘」学生求职竞争力评估框架、由 AI 辅助阅读简历并对照既定评分锚点生成，供内部参考，不构成对最终求职结果的保证。'
    if result.get("stage_note"):
        disclaimer += ' ' + result['stage_note']
    parts.append(f'</div><p class="note">{_text(disclaimer)}</p></article>')
    # CommonMark HTML blocks must not contain unintentional blank lines.
    return "".join(parts).replace("\n", " ")

def render_web_report(result, student_name, student_meta=""):
    import streamlit as st
    st.markdown(report_html(result, student_name, student_meta), unsafe_allow_html=True)
