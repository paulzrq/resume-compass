"""
简历罗盘 · 本地版
运行方式：streamlit run app.py
"""
import base64
import io
import os
import textwrap
import uuid
from contextlib import nullcontext
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pdfplumber
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from scoring import load_framework, score_resume, DEFAULT_MODEL, CHEAP_MODEL, CUSTOM_FIELD_ID
from agents.app_adapter import run_agent_assessment
from branding import logo_svg_data_uri, logo_geometry
from home_ui import render_home_intro, render_career_picker, career_label, FIELD_LABELS, CATEGORY_LABELS
from report import generate_pdf
from local_modules import load_current_module

# Cloud can rerun this entry point while an earlier web_report/share_card/mascots remains in sys.modules.
_web_report = load_current_module("web_report")
render_web_report = _web_report.render_web_report
_share_card = load_current_module("share_card")
generate_share_card = _share_card.generate_share_card
render_share_button = _share_card.render_share_button
render_share_preview = _share_card.render_share_preview
CARD_VERSION = _share_card.CARD_VERSION
choose_share_layout = _share_card.choose_share_layout
from emailer import send_report_email
_mascots = load_current_module("mascots")
mascot_path = _mascots.mascot_path
mascot_accent_color = _mascots.mascot_accent_color
MASCOTS_DIR = _mascots.MASCOTS_DIR
FIELD_ID_TO_MASCOT_FILENAME = _mascots.FIELD_ID_TO_MASCOT_FILENAME


def _inject_background_decoration():
    """统一使用系统主题底色，以低对比度人物剪影装饰，避免图片白底造成色块分界。"""
    bg_path = Path(__file__).resolve().parent / "assets" / "bg_pattern.png"
    if not bg_path.is_file():
        return
    b64 = base64.b64encode(bg_path.read_bytes()).decode("ascii")
    # logo裁剪后的宽高（不是原始2000x2000画布的宽高）：h1::after的aspect-ratio要用这个比例，
    # 保证clamp()缩放时logo始终按真实比例显示，不被拉伸变形。
    (_, _), (_logo_left, _logo_top, _logo_right, _logo_bottom) = logo_geometry()
    logo_w, logo_h = _logo_right - _logo_left, _logo_bottom - _logo_top
    st.markdown(
        textwrap.dedent(f"""
        <style>
        #root, #root div:has(.stApp), .stApp,
        [data-testid="stAppViewContainer"], [data-testid="stAppViewContainer"] > div,
        [data-testid="stMain"] {{
            background-color: inherit;
            color: inherit;
            isolation: isolate;
        }}
        [data-testid="stMain"]::before {{
            content: "";
            position: fixed;
            inset: 0;
            z-index: -1;
            pointer-events: none;
            /* 从图片亮度中扣除白底，仅保留人物轮廓；颜色随主题文字色变化。 */
            background-color: currentColor;
            mask-image: linear-gradient(#fff, #fff), url("data:image/png;base64,{b64}");
            mask-mode: alpha, luminance;
            mask-composite: subtract;
            mask-repeat: no-repeat, repeat;
            opacity: 0.09;
        }}
        @supports not (mask-composite: subtract) {{
            [data-testid="stMain"]::before {{ display: none; }}
        }}
        [data-testid="stMainBlockContainer"],
        .block-container {{
            background-color: transparent;
            color: inherit;
            border-radius: 0;
            margin: 1rem auto 2rem;
            width: calc(100% - 2.5rem);
            padding: 1.5rem 2rem 2.5rem;
            box-shadow: none;
        }}
        /* 2026-09-20【修复"简历罗盘"标题在手机上换行，且保留logo可见——不是display:none】：
           logo靠h1::after绝对定位叠在标题右侧、用padding-right给它腾位置，本质上不参与正常的
           文字排版流。上一版做法是手机上直接隐藏logo（display:none），但产品要求手机上logo
           不能完全消失，只是缩小。现在改用clamp()做无断点的流式缩放：logo的宽度和h1预留的
           padding-right都用clamp(最小值, 随视口宽度vw变化, 最大值)表达，屏幕越窄，logo和
           预留空间同步等比缩小，最窄时也还剩56px的padding、48px的logo宽度——不会缩没，
           也不会因为固定预留宽度在窄屏上跟标题文字抢地方导致换行。logo的高度不再写死37px，
           改成从branding.logo_geometry()实时读取裁剪后的宽高比算出的aspect-ratio，
           这样以后换logo文件也不用来这里改数字。 */
        [data-testid="stMainBlockContainer"] h1 {{
            position:relative; box-sizing:border-box; max-width:100%;
            padding-right: clamp(56px, 18vw, 220px);
        }}
        [data-testid="stMainBlockContainer"] h1::after {{
            content:""; position:absolute; right:0; top:50%; transform:translateY(-50%);
            width: clamp(48px, 16vw, 200px);
            aspect-ratio: {logo_w} / {logo_h};
            background:url("{logo_svg_data_uri()}") right center / contain no-repeat;
        }}
        @media (max-width:640px) {{
            [data-testid="stMainBlockContainer"], .block-container {{
                width:calc(100% - 24px); padding-left:16px; padding-right:16px;
            }}
            [data-testid="stMainBlockContainer"] h1 {{
                display:flex; align-items:center; justify-content:space-between;
                gap:12px; width:100%; padding-right:0;
                font-size:clamp(18px, 5vw, 24px); line-height:1.25;
                white-space:nowrap; word-break:keep-all;
            }}
            [data-testid="stMainBlockContainer"] h1::after {{
                position:static; transform:none; flex:0 0 auto;
                width:clamp(110px, 30vw, 140px);
            }}
        }}
        </style>
        """),
        unsafe_allow_html=True,
    )


def _render_mascot_card(field: dict, dim_name_by_key: dict):
    """选定目标领域后，在旁边露出对应的职业插画卡片——不用等到PDF报告才第一次看到这个方向长什么样。
    没有对应插画的领域（比如biomed，或者自定义方向）mascot_path返回None，这里直接不渲染，
    不报错、不留空白占位——优雅降级。

    2026-09-19：去掉了卡片里"这个方向最看重：xx、xx"那一行文案（按需求直接删掉，不再展示），
    dim_name_by_key 参数因此暂时用不上了，保留在签名里是为了不用同步改调用方 app.py 里
    _render_mascot_card(fields_by_id[...], dim_name_by_key) 这一处传参。"""
    mpath = mascot_path(field.get("id", ""))
    if not mpath:
        return
    b64 = base64.b64encode(mpath.read_bytes()).decode("ascii")
    st.markdown(
        textwrap.dedent(f"""
        <div style="
            display:flex; align-items:center; gap:14px;
            background:transparent;
            border:none; border-radius:14px;
            padding:12px 14px; margin-bottom:8px;
        ">
            <img src="data:image/png;base64,{b64}" style="
                width:64px; height:64px; border-radius:10px;
                background:#ffffff; object-fit:contain; flex-shrink:0;
            ">
            <div>
                <div style="font-size:0.78rem; color:inherit; opacity:0.72; margin-bottom:2px;">Selected target field</div>
                <div style="font-size:1.02rem; font-weight:700; color:inherit;">{field['name']}</div>
            </div>
        </div>
        """),
        unsafe_allow_html=True,
    )


# 5档分级对应的强调色/浅底色，按 framework.json 里 tiers 数组从高到低的顺序对应
# （不是按tier文字匹配，是按顺序位置——tiers数组本身就是按分数从高到低排的）。
# 特意没用红色：这是给学生看的评估结果，最低那档也应该是"继续加油"的观感，不是"不及格"的警示色。
# tiers数量以后如果不是5档了，用 min(idx, len(palette)-1) 兜底，不会越界报错，只是最后几档会共用同一个颜色。
_TIER_PALETTE = [
    ("#c9982f", "#fbf3df"),  # 顶尖竞争力
    ("#3f7a5c", "#e8f2ec"),  # 有较强竞争力
    ("#3d6ea5", "#e8eff7"),  # 中等
    ("#c46a3c", "#f6e9df"),  # 竞争力较弱
    ("#8a8073", "#eee9e0"),  # 需大幅积累
]


def _render_score_badge(result: dict):
    """结果区顶部的"人物徽章"：插画+按分数填充的进度环+大号分数+分级标签，
    取代原来三个孤立的st.metric。进度环颜色按分级从framework.json的tiers顺序取色（见_TIER_PALETTE）。
    custom/biomed这类没有对应插画的情况，mascot_path返回None，退化成不带插画/进度环的纯文字版徽章，
    分数和分级信息不会丢，只是少了插画装饰——优雅降级，不是报错或留空。"""
    field = result["field"]
    framework = result["framework"]
    total = result["total"]
    tier_label = result["tier_label"]
    tier_idx = next(
        (i for i, t in enumerate(framework["tiers"]) if t["label"] == tier_label), 0
    )
    color, bg = _TIER_PALETTE[min(tier_idx, len(_TIER_PALETTE) - 1)]

    mpath = mascot_path(field.get("id", ""))
    radius, circumference = 58, 2 * 3.14159265 * 58
    frac = max(0, min(total, 100)) / 100
    dash = f"{circumference * frac:.1f} {circumference:.1f}"

    if mpath:
        b64 = base64.b64encode(mpath.read_bytes()).decode("ascii")
        visual_html = f"""
            <div style="position:relative; width:132px; height:132px; flex-shrink:0;">
                <svg width="132" height="132" viewBox="0 0 132 132" style="transform:rotate(-90deg);">
                    <circle cx="66" cy="66" r="{radius}" fill="none" stroke="#eee9e0" stroke-width="8"></circle>
                    <circle cx="66" cy="66" r="{radius}" fill="none" stroke="{color}" stroke-width="8"
                            stroke-linecap="round" stroke-dasharray="{dash}"></circle>
                </svg>
                <img src="data:image/png;base64,{b64}" style="
                    position:absolute; top:14px; left:14px; width:104px; height:104px;
                    border-radius:50%; background:#ffffff; object-fit:contain; padding:8px;
                ">
            </div>
        """.strip()
        # .strip()去掉首尾的换行——不去掉的话，这段多行字符串代入外层markdown模板那一行
        # 后，会在"{visual_html}"这个位置前多出一个只有空白字符的"空行"，Markdown会把它
        # 当成空行处理，导致外层<div>这个HTML块提前截断，分数/分级内容全部渲染失败
        # （跟下面visual_html=""那个坑是同一类问题，这里是"多余的前导换行"版本）。
    else:
        # 注意：这里不能给空字符串——空字符串代入下面markdown模板那一行后，
        # 那一行就变成纯空白，会被Markdown解析成"空行"，导致外层<div>这个HTML块
        # 提前截断，后面的分数/分级内容全部跟着渲染失败。用一个HTML注释占位，
        # 保证这一行永远不是空白行。
        visual_html = "<!-- no mascot -->"

    st.markdown(
        textwrap.dedent(f"""
        <div style="display:flex; align-items:center; gap:24px; padding:8px 4px 4px;">
            {visual_html}
            <div>
                <div style="font-size:0.9rem; color:inherit; opacity:0.72; margin-bottom:4px;">{field['name']} · Evaluation Result</div>
                <div style="display:flex; align-items:baseline; gap:6px; margin-bottom:8px;">
                    <span style="font-size:2.4rem; font-weight:800; line-height:1; color:inherit;">{total}</span>
                    <span style="font-size:1rem; color:inherit; opacity:0.72;">/ 100</span>
                </div>
                <span style="display:inline-block; font-size:0.85rem; font-weight:700;
                             padding:5px 14px; border-radius:999px; color:{color}; background:{bg};">
                    {tier_label}
                </span>
            </div>
        </div>
        """),
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner=False)
def _mascot_reel_assets():
    """给打分等待时的插画"连播"动画用的小尺寸缩略图（压到<=660px，适配220px显示尺寸的高分屏），只在Streamlit这个
    进程的生命周期里统一读盘+压缩一次并缓存住，不会因为每次点"开始评估"就重新处理一遍
    全部约28张原图（1254x1254px）。返回 (按固定顺序排列的field_id列表,
    {field_id: 660px缩略图的base64字符串})。"""
    order = []
    b64_by_id = {}
    for fid, filename in FIELD_ID_TO_MASCOT_FILENAME.items():
        path = MASCOTS_DIR / filename
        if not path.is_file():
            continue
        img = Image.open(path).convert("RGBA")
        img.thumbnail((660, 660), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_by_id[fid] = base64.b64encode(buf.getvalue()).decode("ascii")
        order.append(fid)
    return order, b64_by_id


def _render_agent_plan_tab(tab, result, dim_name_by_key):
    """Agent 模式 tab：planner 的取证计划。"""
    with tab:
        st.subheader("📋 Evidence Plan")
        st.caption("The planner sets an evidence-gathering strategy for the 7 dimensions first; the scorer then scores by the plan — think before acting.")
        plan = result.get("plan") or []
        if not plan:
            st.info("No evidence plan for this run.")
            return
        st.table([{
            "Dimension": dim_name_by_key.get(p.get("dimension"), p.get("dimension")),
            "Evidence strategy": p.get("strategy", ""),
            "JD search queries": "; ".join(p.get("jd_queries") or []) or "—",
        } for p in plan])


def _render_agent_scoring_tab(tab, result, dim_name_by_key):
    """Agent 模式 tab：scorer 的逐维度打分过程。"""
    with tab:
        st.subheader("🔍 Scoring Process")
        st.caption(
            f"The ReAct agent scores dimension by dimension with 3 tools "
            f"(get_dimension_rubric / search_jd_library / verify_quote); "
            f"the scorer ran {result.get('revision_rounds', 0)} round(s)."
        )
        scores = result.get("dimension_scores", {})
        rationale = result.get("dimension_rationale", {})
        evidence = result.get("dimension_evidence", {})
        for key, score in scores.items():
            with st.expander(
                    f"{dim_name_by_key.get(key, key)}: {score} / 5", expanded=False):
                if rationale.get(key):
                    st.write(rationale[key])
                for q in evidence.get(key) or []:
                    st.code(q)


def _render_agent_critic_tab(tab, result, dim_name_by_key):
    """Agent 模式 tab：critic v2 的审计与改分。"""
    with tab:
        st.subheader("🛡️ Critic Audit")
        corrections = result.get("critic_corrections") or []
        if result.get("critic_pass") and not corrections:
            st.success("Critic passed on the first round — no critical issues found.")
        elif corrections:
            st.warning(f"Critic found {len(corrections)} critical issue(s) and corrected the scores directly:")
            st.table([{
                "Dimension": dim_name_by_key.get(c.get("dimension"), c.get("dimension")),
                "Original": c.get("old_score"),
                "Corrected": c.get("new_score"),
                "Reason": c.get("reason", ""),
            } for c in corrections])
        else:
            st.info("Critic did not pass, but no scores were changed (revision limit reached; summarized directly).")
        feedback = result.get("critic_feedback") or []
        if feedback:
            st.caption("Critic feedback:")
            for f in feedback:
                st.write(f"- {f}")


_FULLSCREEN_OVERLAY_ID = "resume-compass-fullscreen-loading"


def _render_loading_reel(target_field: Optional[dict]):
    """打分等待时的插画"连播"全屏遮罩：先快速循环播放全部职业插画，然后逐步减速，精准停在
    用户选定方向的插画上（多绕一整圈增加"揭晓"仪式感），落地后插画持续轻微呼吸，提示AI
    还在算。没有对应插画的方向（biomed、自定义方向）返回None，调用方需要退回普通的文字版
    st.spinner——优雅降级，不报错也不留空白。

    工程上的一个关键点（跟之前的小卡片版一样）：目标插画在点"开始评估"的那一刻就已经确定了，
    所以这里"减速落地"的时间点不需要、也没法和AI打分请求真正返回的时刻精确同步——Streamlit是
    同步执行模型，发起打分请求后整个脚本会被这一次调用阻塞住，没办法在请求进行中途再给浏览器
    推一条"现在开始减速"的消息。所以落地动画是纯前端按固定节奏播完的，播完就落地转入呼吸循环
    等待，请求一旦真正返回，调用方会用 _clear_loading_reel 把这块内容清掉换成正式的分数徽章。

    2026-09-19：从"页面里的一张小卡片"改成"全屏遮罩"。这里有个Streamlit特有的坑：
    components.html渲染出来的东西是一个iframe，iframe自己的大小只受传给它的height/width控制，
    在iframe内部写position:fixed只能让元素铺满这个iframe自己的小盒子，铺不满真正的浏览器窗口。
    绕过办法：iframe和主页面是同源的，iframe里的脚本可以通过window.parent.document直接操作
    "外面"那个真正的页面——把遮罩元素直接挂到window.parent.document.body上，用position:fixed
    盖满整个视口，这样才是真的全屏，而不是"一个变大的iframe"。iframe自己则只留一条几乎看不见的
    高度（height=1），只是用来跑这段脚本，不承担任何可见内容。"""
    if target_field is None:
        return None
    order, b64_by_id = _mascot_reel_assets()
    target_id = target_field.get("id", "")
    if target_id not in b64_by_id or target_id not in order:
        return None

    field_name = target_field.get("name", "")
    imgs_html = "".join(
        f'<img data-id="{fid}" src="data:image/png;base64,{b64_by_id[fid]}">' for fid in order
    )
    order_json = str(order).replace("'", '"')

    bootstrap_js = f"""
    (function(){{
        var doc = window.parent.document;
        var OVERLAY_ID = "{_FULLSCREEN_OVERLAY_ID}";
        var old = doc.getElementById(OVERLAY_ID);
        if (old) {{ old.remove(); }}

        var styleTag = doc.createElement("style");
        styleTag.id = OVERLAY_ID + "-style";
        var oldStyle = doc.getElementById(styleTag.id);
        if (oldStyle) {{ oldStyle.remove(); }}
        styleTag.textContent = `
            #${{OVERLAY_ID}} * {{ box-sizing:border-box; font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif; }}
            #${{OVERLAY_ID}} .rc-reel-visual {{ position:relative; width:220px; height:220px; }}
            #${{OVERLAY_ID}} .rc-reel-visual img {{
                position:absolute; inset:0; width:100%; height:100%;
                border:none; border-radius:0; background:transparent; object-fit:contain; padding:20px;
                box-shadow:none;
                opacity:0; transition:opacity .16s ease;
            }}
            #${{OVERLAY_ID}} .rc-reel-visual img.show {{ opacity:1; }}
            #${{OVERLAY_ID}} .rc-reel-visual img.landed {{ animation: rcReelBreathe 2.2s ease-in-out infinite; }}
            @keyframes rcReelBreathe {{
                0%, 100% {{ transform:scale(1); opacity:1; }}
                50% {{ transform:scale(1.045); opacity:0.95; }}
            }}
            #${{OVERLAY_ID}} .rc-hint-pill {{
                position:absolute; top:22px; left:50%; transform:translateX(-50%);
                background:transparent; border:none; border-radius:999px;
                padding:7px 16px; font-size:0.8rem; color:#8a8073;
                box-shadow:none;
            }}
            #${{OVERLAY_ID}} .rc-reel-title {{ font-size:1.05rem; font-weight:700; color:#2b2620; text-align:center; margin-bottom:6px; }}
            #${{OVERLAY_ID}} .rc-reel-sub {{ font-size:0.88rem; color:#8a8073; text-align:center; }}
            #${{OVERLAY_ID}} .rc-reel-sub b {{ color:#c46a3c; }}
            #${{OVERLAY_ID}} .rc-progress-dots {{ display:flex; gap:6px; justify-content:center; }}
            #${{OVERLAY_ID}} .rc-progress-dots span {{
                width:6px; height:6px; border-radius:50%; background:#e7e1d6;
                animation:rcDotPulse 1.2s ease-in-out infinite;
            }}
            #${{OVERLAY_ID}} .rc-progress-dots span:nth-child(2) {{ animation-delay:0.15s; }}
            #${{OVERLAY_ID}} .rc-progress-dots span:nth-child(3) {{ animation-delay:0.3s; }}
            @keyframes rcDotPulse {{
                0%, 80%, 100% {{ background:#e7e1d6; transform:scale(1); }}
                40% {{ background:#c46a3c; transform:scale(1.3); }}
            }}
        `;
        doc.head.appendChild(styleTag);

        var overlay = doc.createElement("div");
        overlay.id = OVERLAY_ID;
        overlay.style.cssText = "position:fixed; inset:0; z-index:1000000; "
            + "background:#FFFFFF; "
            + "display:flex; flex-direction:column; align-items:center; justify-content:center; gap:28px;";
        overlay.innerHTML =
            '<div class="rc-hint-pill">Evaluating, please wait…</div>'
            + '<div class="rc-reel-visual" id="rcReelVisual">{imgs_html}</div>'
            + '<div><div class="rc-reel-title" id="rcReelTitle">Reading resume and scoring with the AI model</div>'
            + '<div class="rc-reel-sub" id="rcReelSub">Timing depends on model response; please do not resubmit</div></div>'
            + '<div class="rc-progress-dots"><span></span><span></span><span></span></div>';
        doc.body.appendChild(overlay);
        // iframe在重跑或停止时被卸载，也要移除属于它的遮罩。
        window.addEventListener("pagehide", function(){{
            overlay.remove();
            styleTag.remove();
        }}, {{ once:true }});

        var ORDER = {order_json};
        var TARGET = "{target_id}";
        var imgs = overlay.querySelectorAll(".rc-reel-visual img");
        var titleEl = doc.getElementById("rcReelTitle");
        var subEl = doc.getElementById("rcReelSub");
        var targetIdx = ORDER.indexOf(TARGET);

        function showIdx(i){{
            imgs.forEach(function(img, k){{ img.classList.toggle("show", k === i); }});
        }}

        var idx = Math.floor(Math.random() * ORDER.length);
        showIdx(idx);

        var fastSteps = 7;
        function fastLoop(){{
            if (fastSteps <= 0) {{ return land(); }}
            idx = (idx + 1) % ORDER.length;
            showIdx(idx);
            fastSteps -= 1;
            setTimeout(fastLoop, 95);
        }}

        function land(){{
            var steps = (targetIdx - idx + ORDER.length) % ORDER.length;
            steps += ORDER.length;
            var delay = 95;
            function step(){{
                if (steps <= 0){{
                    showIdx(targetIdx);
                    imgs[targetIdx].classList.add("landed");
                    titleEl.textContent = 'Evaluating: {field_name}';
                    subEl.textContent = "Still waiting for the model; results will appear automatically.";
                    return;
                }}
                idx = (idx + 1) % ORDER.length;
                showIdx(idx);
                steps -= 1;
                delay = Math.min(delay * 1.18, 420);
                setTimeout(step, delay);
            }}
            step();
        }}

        fastLoop();
    }})();
    """

    placeholder = st.empty()
    with placeholder.container():
        components.html(f"<script>{bootstrap_js}</script>", height=1, scrolling=False)
    return placeholder


def _clear_loading_reel(placeholder):
    """打分结果真正返回后调用：先往（已经没有可见内容的）iframe里塞一段清理脚本，
    通过window.parent.document把之前挂在主页面上的全屏遮罩摘掉。
    清理iframe必须保留到下一次rerun，否则Streamlit可能合并更新，导致清理脚本根本没有执行。
    直接placeholder.empty()是不够的——遮罩是_render_loading_reel用window.parent.document
    挂到"外面"那个主页面上的，不是这个iframe自己的子节点，iframe被销毁也不会带走它，
    不额外清理的话遮罩会一直卡在屏幕上出不去。"""
    if placeholder is None:
        return
    cleanup_js = f"""
    <script>
    (function(){{
        var el = window.parent.document.getElementById("{_FULLSCREEN_OVERLAY_ID}");
        if (el) {{ el.remove(); }}
        var styleEl = window.parent.document.getElementById("{_FULLSCREEN_OVERLAY_ID}-style");
        if (styleEl) {{ styleEl.remove(); }}
    }})();
    </script>
    """
    with placeholder.container():
        components.html(cleanup_js, height=1, scrolling=False)
    # 保留清理 iframe，让浏览器有机会执行脚本；不能立即 empty()。


def render_pdf_pages_as_images(pdf_bytes: bytes, dpi: int = 150):
    """把PDF每一页渲染成PNG图片字节，用于在Streamlit页面里直接预览，不用下载打开。"""
    try:
        import pymupdf  # PyMuPDF；新版包名是 pymupdf，import fitz 是旧写法，已弃用
    except ImportError as e:
        raise RuntimeError("Missing dependency pymupdf. Run 'pip install pymupdf' in the venv, then rerun.") from e
    doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
    zoom = dpi / 72
    mat = pymupdf.Matrix(zoom, zoom)
    images = []
    try:
        for page in doc:
            pix = page.get_pixmap(matrix=mat)
            images.append(pix.tobytes("png"))
    finally:
        doc.close()
    return images

APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR.parent
REPORT_DIR = PROJECT_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

st.set_page_config(page_title="Resume Compass", page_icon="🧭", layout="wide", initial_sidebar_state="collapsed")
# Hide host controls independently of optional background assets.
st.markdown(textwrap.dedent("""
<style>
[data-testid="stHeader"], [data-testid="stToolbar"],
[data-testid="stSidebar"], [data-testid="stSidebarCollapsedControl"],
[data-testid="collapsedControl"], [data-testid="stMainMenu"],
[data-testid="stAppDeployButton"], #MainMenu {
    display: none !important;
}
</style>
"""), unsafe_allow_html=True)
# 2026-09-19：重新启用背景装饰（见 _inject_background_decoration 里的说明）——
# 现在内容区自己盖了层不透明白卡片，装饰图案只会露在卡片外面的留白处，不会再挡文字。
_inject_background_decoration()

# 2026-09-19：加一个 view 状态，表单页("form")和结果页("result")互斥显示——
# 评估完成后切到"result"、隐藏掉上传表单，而不是像以前那样把结果一直往表单下面加；
# 结果页顶部有个"重新评估"按钮切回"form"并清空当次结果，开始下一份。
if "view" not in st.session_state:
    st.session_state.view = "form"
if "result" not in st.session_state:
    st.session_state.result = None
if "resume_pdf_bytes" not in st.session_state:
    st.session_state.resume_pdf_bytes = None
if "resume_original_bytes" not in st.session_state:
    st.session_state.resume_original_bytes = None
if "resume_original_filename" not in st.session_state:
    st.session_state.resume_original_filename = None

framework = load_framework()
field_options = {f["name"]: f["id"] for f in framework["fields"]}
fields_by_id = {f["id"]: f for f in framework["fields"]}
dim_name_by_key = {d["key"]: d["name"] for d in framework["dimensions"]}
CUSTOM_OPTION_LABEL = "🖊️ Other (custom field — I'll type it myself)"

# 2026-09-19：目标领域下拉改成"先选分类、再选具体方向"的两级结构，跟 jd-reference-library/README.md
# 里的分类表（技术与工程 / 商业与管理 / 专业服务与内容 / 设计与创意）保持一致——分类本身就是
# framework.json 每个field自带的 "category" 字段，不是在这里另外维护一份，以后 framework.json
# 里加新方向、改分类，这里的分组会跟着自动更新，不用同步改代码。
# fields_by_category 保留 framework.json 里 fields 数组本来的顺序（categories 列表按分类
# 第一次出现的顺序收集，不是按字母排序），跟 README 表格的呈现顺序一致。
FIELD_CATEGORIES: list = []
FIELDS_BY_CATEGORY: dict = {}
for _f in framework["fields"]:
    _cat = _f.get("category") or "Other"
    if _cat not in FIELDS_BY_CATEGORY:
        FIELD_CATEGORIES.append(_cat)
        FIELDS_BY_CATEGORY[_cat] = []
    FIELDS_BY_CATEGORY[_cat].append(_f["name"])

st.title("🧭 Resume Compass")
st.caption("Upload your resume to get an evaluation report")

def _get_secret_api_key() -> str:
    """优先读取 Streamlit Community Cloud 后台配置的 Secrets（部署到云端用这个）。
    本地开发机上如果没配置 .streamlit/secrets.toml，st.secrets 是空的，
    直接返回空字符串，不会报错、也不影响本地用环境变量的老用法。"""
    try:
        return st.secrets.get("ANTHROPIC_API_KEY", "")
    except Exception:
        return ""


secret_api_key = _get_secret_api_key()
env_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
api_key = secret_api_key or env_api_key
if not api_key:
    st.error("Assessment service is unavailable. Please contact the administrator.")

# Keep operational settings server-side; students only see the assessment form.
model = DEFAULT_MODEL
score_runs = 1

# 2026-09-19：除了PDF，现在也支持直接上传图片格式的简历（拍照/截图都可以）——
# 图片会原样作为视觉输入发给Claude，让模型自己"看图"评分，不再要求先提取出纯文字。
# key是Streamlit file_uploader给出的MIME类型，value是发给Anthropic API时用的media_type
# （两者目前一致，用一个字典是为了以后要是哪个类型写法不一样时只用改这一处）。
IMAGE_MEDIA_TYPES = {
    "image/png": "image/png",
    "image/jpeg": "image/jpeg",
    "image/webp": "image/webp",
}

if st.session_state.view == "form":
    render_home_intro()
    with st.container(key="home_workspace"):
        col1, col2 = st.columns([1, 1], gap="large")
        with col1:
            st.markdown('<div class="home-section">Upload resume</div>', unsafe_allow_html=True)
            uploaded = st.file_uploader(
                "Upload resume", type=["pdf", "png", "jpg", "jpeg", "webp"],
                label_visibility="collapsed", key="resume_upload")
            # No identity input or inference from potentially sensitive filenames.
            student_name = "Resume"
        with col2:
            st.markdown('<div class="home-section">Career path</div>', unsafe_allow_html=True)
            # 2026-10-02: cascader (iframe 组件) -> st.popover-based 卡片选择器，见 home_ui.py
            # render_career_picker 顶部注释：解决的是浮层被 iframe 包围盒裁切、顶开左右列高度
            # 的问题。
            chosen, is_custom_field = render_career_picker(
                fields_by_id=fields_by_id,
                field_categories=FIELD_CATEGORIES,
                fields_by_category=FIELDS_BY_CATEGORY,
                field_options=field_options,
                custom_field_id=CUSTOM_FIELD_ID,
            )
            field_name = fields_by_id[chosen]["name"] if chosen and not is_custom_field else None
            custom_field_name = ""
            if is_custom_field:
                custom_field_name = st.text_input("Your career path", placeholder="e.g. Climate Policy Analyst")
                st.caption("Custom paths use the closest available scoring framework.")
            student_meta = ""
        st.divider()
        options_col, action_col = st.columns([2, 1])
        with options_col:
            agent_mode = st.toggle("In-depth assessment", value=False, key="home_agent",
                                   help="Experimental: multi-step analysis and review. PDF only; takes longer.")
            st.caption("Experimental · Multi-step analysis and review · PDF only")
        field_ready = bool(custom_field_name.strip()) if is_custom_field else bool(field_name)
        with action_col:
            run = st.button("Start assessment", type="primary", use_container_width=True,
                            disabled=not (uploaded and api_key and field_ready))
    st.markdown('<div class="home-foot">AI-assisted assessment. For reference only.</div>', unsafe_allow_html=True)

    if run:
        field_id = CUSTOM_FIELD_ID if is_custom_field else field_options[field_name]
        target_field_for_reel = None if is_custom_field else fields_by_id[field_id]
        reel_placeholder = _render_loading_reel(target_field_for_reel)
        spinner_cm = (
            nullcontext()
            if reel_placeholder is not None
            else st.spinner("Reading resume and scoring with the AI model…")
        )
        try:
            with spinner_cm:
                raw_bytes = uploaded.getvalue()
                is_image = uploaded.type in IMAGE_MEDIA_TYPES
                resume_text = None
                resume_image = None
                # resume_pdf_bytes 只在真正是PDF时才保留——report.py靠它生成"附原文标注"那一段，
                # 那段逻辑是按PDF页面坐标做关键词高亮定位的，图片没有对应的坐标信息，保持None
                # 会让report.py自然跳过那一段（它本来就有"没拿到PDF字节"的兜底分支），而不是
                # 硬塞一个根本不是PDF的字节串进去导致后面解析报错。
                resume_pdf_bytes = None
                proceed = True
                if is_image:
                    resume_image = {
                        "media_type": IMAGE_MEDIA_TYPES[uploaded.type],
                        "data": base64.b64encode(raw_bytes).decode("ascii"),
                    }
                else:
                    resume_pdf_bytes = raw_bytes
                    with pdfplumber.open(uploaded) as pdf:
                        resume_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                    if not resume_text.strip():
                        st.error(
                            "Could not extract text from this PDF — it may be a scanned image. "
                            "Try exporting or screenshotting it as a PNG/JPG image and uploading again; image uploads are now evaluated directly."
                        )
                        proceed = False

                if proceed and agent_mode and resume_text is None:
                    st.error("In-depth assessment supports PDF resumes only. Please upload a PDF.")
                    proceed = False

                if proceed:
                    if agent_mode:
                        result = run_agent_assessment(
                            resume_text=resume_text,
                            field_id=field_id,
                            api_key=api_key,
                            model=model,
                            custom_field_name=custom_field_name.strip() if is_custom_field else None,
                        )
                    else:
                        result = score_resume(
                            resume_text=resume_text,
                            resume_image=resume_image,
                            field_id=field_id,
                            api_key=api_key,
                            model=model,
                            runs=score_runs,
                            custom_field_name=custom_field_name.strip() if is_custom_field else None,
                        )
                    st.session_state.pop("report_artifact", None)
                    st.session_state.pop("report_email_status", None)
                    st.session_state.share_layout = choose_share_layout()
                    st.session_state.report_unlocked = False
                    st.session_state.assessment_id = uuid.uuid4().hex
                    st.session_state.result = result
                    st.session_state.resume_pdf_bytes = resume_pdf_bytes
                    st.session_state.resume_original_bytes = raw_bytes
                    st.session_state.resume_original_filename = uploaded.name
                    st.session_state.student_name = student_name
                    st.session_state.student_meta = student_meta
                    st.session_state.view = "result"
        except Exception as e:
            st.error(f"Evaluation failed: {e}")
        finally:
            _clear_loading_reel(reel_placeholder)
    if st.session_state.view == "result":
        # 刚评估完、切到了结果视图——重跑一次让下面的 elif 分支接管渲染，
        # 而不是让表单区和结果区在同一次运行里都画出来。
        st.rerun()

elif st.session_state.view == "result" and st.session_state.result:
    result = st.session_state.result
    if st.button("← Evaluate another resume"):
        st.session_state.view = "form"
        st.session_state.result = None
        st.session_state.resume_pdf_bytes = None
        st.session_state.resume_original_bytes = None
        st.session_state.resume_original_filename = None
        st.rerun()
    st.divider()
    if result.get("agent_mode"):
        tab_report, tab_plan, tab_scoring, tab_critic = st.tabs(
            ["📄 Final Report", "📋 Evidence Plan", "🔍 Scoring Process", "🛡️ Critic Audit"])
        _render_agent_plan_tab(tab_plan, result, dim_name_by_key)
        _render_agent_scoring_tab(tab_scoring, result, dim_name_by_key)
        _render_agent_critic_tab(tab_critic, result, dim_name_by_key)
        report_ctx = tab_report
    else:
        report_ctx = nullcontext()
    with report_ctx:
        try:
            if "share_layout" not in st.session_state:
                st.session_state.share_layout = choose_share_layout()
            share_key = (result["field"].get("id", ""), result["field"]["name"], result["total"], st.session_state.share_layout)
            if st.session_state.get("share_card_key") != (CARD_VERSION, share_key):
                st.session_state.share_card_png = generate_share_card(*share_key)
                st.session_state.share_card_key = (CARD_VERSION, share_key)
            render_share_preview(st.session_state.share_card_png)
            accent_color = mascot_accent_color(result["field"].get("id", ""))
            if render_share_button(st.session_state.share_card_png, accent_color,
                                   key="share-" + CARD_VERSION + "-" + st.session_state.get("assessment_id", "legacy")):
                st.session_state.report_unlocked = True
        except Exception:
            st.warning("The share card could not be generated. Please retry.")
            if st.button("Retry share card"):
                st.session_state.pop("share_card_key", None)
                st.rerun()
        if not st.session_state.get("report_unlocked", False):
            st.caption("Tap the share card to view the full report.")
            st.stop()

        render_web_report(result, st.session_state.student_name, st.session_state.student_meta)

        if result["field"].get("id") == CUSTOM_FIELD_ID:
            matched_fields = result["field"].get("matched_fields")
            if matched_fields:
                match_desc = " / ".join(
                    f"{m['name']} {round(m['weight'] * 100)}%" for m in matched_fields
                )
                st.caption(
                    f"✏️ Target field \"{result['field']['name']}\" is a custom field you entered. "
                    f"It was auto-matched to existing fields, and this scoring's weights/bonuses/gaps/JD references were blended proportionally: {match_desc}"
                )
            else:
                st.caption(
                    f"✏️ Target field \"{result['field']['name']}\" is a custom field you entered. "
                    "It could not be matched to a similar existing field, so generic fallback weights were used, based on the model's general understanding of this field. For reference only."
                )

        stability = result.get("stability")
        if stability:
            totals = stability["total_all_runs"]
            spread = max(totals) - min(totals)
            st.caption(
                f"🔁 Stability mode: scored {stability['runs']} time(s); totals were "
                f"{' / '.join(str(t) for t in totals)}, spread {spread} points. Each dimension above takes the median."
            )

        try:
            if "report_artifact" not in st.session_state:
                pdf_bytes, highlight_info = generate_pdf(
                    result,
                    st.session_state.student_name,
                    st.session_state.student_meta,
                    resume_pdf_bytes=st.session_state.resume_pdf_bytes,
                )
                st.session_state.report_artifact = (pdf_bytes, highlight_info)
            pdf_bytes, highlight_info = st.session_state.report_artifact
        except Exception as e:
            # 打分结果已经在上面完整展示了；PDF生成这一步单独兜底，
            # 失败也不影响用户看到刚才的评分和分析，只是拿不到PDF报告。
            st.error(f"Failed to generate the PDF report: {e} (the scores above are unaffected — you can review them first)")
            pdf_bytes, highlight_info = None, None

        if pdf_bytes is not None and (result.get("ats_keywords") or result.get("vague_phrases") or result.get("strong_phrases")):
            if highlight_info["attached"]:
                parts = []
                if highlight_info["ats_matched"]:
                    parts.append(f"Yellow / ATS keywords: {', '.join(highlight_info['ats_matched'])}")
                if highlight_info["strong_matched"]:
                    parts.append(f"Green / quantified achievements: {'; '.join(highlight_info['strong_matched'])}")
                if highlight_info["vague_matched"]:
                    parts.append(f"Red / phrases to improve: {'; '.join(highlight_info['vague_matched'])}")
                st.caption("Annotated in the original resume below — " + "; ".join(parts))
            else:
                st.warning(f"Could not generate the annotated resume: {highlight_info['reason']}")

        if pdf_bytes is not None:
            annotated_pdf = highlight_info.get("highlighted_resume_pdf_bytes")
            if annotated_pdf:
                st.subheader("Annotated Original Resume")
                st.caption("Original layout preserved. Yellow = ATS keywords, green = quantified achievements, red = phrases to improve.")
                try:
                    page_images = render_pdf_pages_as_images(annotated_pdf, dpi=200)
                    for img_bytes in page_images:
                        encoded = base64.b64encode(img_bytes).decode("ascii")
                        st.markdown(f'<div style="max-width:1000px;margin:0 auto"><img alt="标注版简历原文" src="data:image/png;base64,{encoded}" style="width:100%;height:auto"></div>', unsafe_allow_html=True)
                except Exception:
                    st.info("The annotation preview can't be displayed right now. Please download the annotated resume to view it.")
                st.download_button("Download annotated resume", data=annotated_pdf,
                                   file_name="annotated_resume.pdf", mime="application/pdf")
            out_name = f"{st.session_state.student_name}_evaluation_{date.today().isoformat()}.pdf"
            out_path = REPORT_DIR / out_name
            out_path.write_bytes(pdf_bytes)
            st.download_button("Download PDF report", data=pdf_bytes, file_name=out_name, mime="application/pdf")

            # 2026-09-19：顺手把报告存档邮件发出去——免费版Streamlit Cloud容器重启后
            # reports/文件夹会清空，邮箱是目前最省事的长期留存方式。
            # 2026-09-19 补充：之前这里"secrets没配置、静默跳过"和"真的发送成功了"在界面上
            # 完全看不出区别（都是"什么提示都没有"），调试时没法判断到底是配置没生效还是
            # 发送本身出了问题。现在send_report_email()会返回"sent"/"skipped"，这里分情况
            # 给出明确提示——真发送失败了（密码错、网络问题、邮箱那边没开SMTP AUTH等）
            # 仍然只提示一句，不影响上面已经展示的评分结果和下载按钮。
            try:
                if "report_email_status" not in st.session_state:
                    email_status = send_report_email(
                        pdf_bytes,
                        out_name,
                        st.session_state.student_name,
                        result["field"].get("name", "Unknown field"),
                        result["total"],
                        result["tier_label"],
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                        resume_bytes=st.session_state.resume_original_bytes,
                        resume_filename=st.session_state.resume_original_filename,
                    )
                    st.session_state.report_email_status = email_status
                email_status = st.session_state.report_email_status
                if email_status == "sent":
                    st.caption("📧 The report and original resume were emailed for archiving")
                else:
                    st.caption("ℹ️ Archive email is not configured (sender email/password missing in Secrets); skipped")
            except Exception as e:
                st.warning(f"Archive email failed (scores and downloads above are unaffected): {e}")

        usage = result.get("usage") or {}
        in_tok = usage.get("input_tokens")
        out_tok = usage.get("output_tokens")
        think_tok = usage.get("thinking_tokens")
        cache_write_tok = usage.get("cache_creation_tokens") or 0
        cache_read_tok = usage.get("cache_read_tokens") or 0
        if in_tok is not None and out_tok is not None:
            # 价格取当前所用模型的官方单价，仅供估算参考。
            # 注：自定义方向那次分类匹配调用固定用的是Haiku（比这里的model便宜），
            # 但它的token量很小（通常几百token），混进来按主模型单价估算，误差可以忽略不计。
            price_in, price_out = (1.0, 5.0) if model == CHEAP_MODEL else (2.0, 10.0)
            # 缓存写入价是原价的1.25倍（首次调用把system prompt/简历原文写进缓存），
            # 缓存命中价是原价的1折（后续调用直接读缓存，稳定性模式下第2次调用大部分会命中）。
            cache_write_price = price_in * 1.25
            cache_read_price = price_in * 0.1
            est_cost = (
                in_tok / 1_000_000 * price_in
                + cache_write_tok / 1_000_000 * cache_write_price
                + cache_read_tok / 1_000_000 * cache_read_price
                + out_tok / 1_000_000 * price_out
            )
            cache_note = f", including {cache_read_tok} cache-hit tokens (billed at 10%)" if cache_read_tok > 0 else ""
            st.caption(
                f"This run used: {in_tok} input tokens, {out_tok} output tokens"
                f" (incl. {think_tok} thinking tokens){cache_note}, estimated cost ${est_cost:.4f}"
            )

        with st.expander("View raw model output (debug)"):
            st.code(result["raw_model_output"])

else:
    # 防御性兜底：view是"result"但session里没有对应的result数据
    # （理论上不会出现，除非session状态被意外清空过），退回表单页而不是渲染一个空白结果区。
    st.session_state.view = "form"
    st.rerun()