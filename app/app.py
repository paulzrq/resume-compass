"""
简历罗盘 · 本地版
运行方式：streamlit run app.py
"""
import base64
import io
import os
import textwrap
from contextlib import nullcontext
from datetime import date
from pathlib import Path
from typing import Optional

import pdfplumber
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

from scoring import load_framework, score_resume, DEFAULT_MODEL, CHEAP_MODEL, CUSTOM_FIELD_ID
from report import generate_pdf
from mascots import mascot_path, MASCOTS_DIR, FIELD_ID_TO_MASCOT_FILENAME


def _inject_background_decoration():
    """用全部28个职业插画拼成的低透明度背景图，铺满整个页面，作为简历罗盘的视觉装饰。
    图片本身已经按最终展示尺寸生成（7列x4行，每格一个人物），平铺时按原始像素1:1显示，
    不需要再用CSS缩放。

    2026-09-19：重新启用，但换了个更安全的做法——之前是直接铺在.stApp整个背景上，
    跟内容区的文字撞在一起导致看不清（那次的bug），这次让实际内容区（.block-container）
    盖一层不透明白色"卡片"（圆角+留白+外边距），插画图案只会在卡片以外的页面留白处
    露出来，不会出现在文字后面。因为页面用的是wide布局，卡片本身还是接近满宽，
    露出来的主要是卡片四周这一圈留白，不是那种大面积背景——这是"要装饰、也要保证
    文字永远清晰"这两个目标之间的折中，不是完整的全页平铺效果。
    如果实测发现Streamlit这个版本用的CSS选择器（.main .block-container /
    [data-testid="stAppViewContainer"]）跟这里写的对不上、卡片没生效，
    需要打开浏览器开发者工具看一下实际的class/data-testid再调整选择器。"""
    bg_path = Path(__file__).resolve().parent / "assets" / "bg_pattern.png"
    if not bg_path.is_file():
        return
    b64 = base64.b64encode(bg_path.read_bytes()).decode("ascii")
    st.markdown(
        textwrap.dedent(f"""
        <style>
        .stApp {{
            background-color: #FFFFFF;
            background-image: url("data:image/png;base64,{b64}");
            background-repeat: repeat;
        }}
        [data-testid="stAppViewContainer"] .main .block-container,
        .main .block-container {{
            background-color: #FFFFFF;
            border-radius: 20px;
            margin: 1rem 1.25rem 2rem;
            padding: 1.5rem 2rem 2.5rem;
            box-shadow: 0 1px 4px rgba(43,38,32,0.05);
        }}
        </style>
        """),
        unsafe_allow_html=True,
    )


def _render_mascot_card(field: dict, dim_name_by_key: dict):
    """选定目标领域后，在旁边露出对应的职业插画卡片——不用等到PDF报告才第一次看到这个方向长什么样。
    卡片文字里"最看重"的两个维度，是按这个领域7维度权重从高到低动态算出来的（不是写死的文案），
    以后调整framework.json里的权重，这里的文案会跟着自动更新，不用同步改代码。
    没有对应插画的领域（比如biomed，或者自定义方向）mascot_path返回None，这里直接不渲染，
    不报错、不留空白占位——优雅降级。"""
    mpath = mascot_path(field.get("id", ""))
    if not mpath:
        return
    top_dims = sorted(field["weights"].items(), key=lambda kv: kv[1], reverse=True)[:2]
    top_dim_names = "、".join(dim_name_by_key.get(k, k) for k, _ in top_dims)
    b64 = base64.b64encode(mpath.read_bytes()).decode("ascii")
    st.markdown(
        textwrap.dedent(f"""
        <div style="
            display:flex; align-items:center; gap:14px;
            background:linear-gradient(135deg, #ffffff, #f4e2d3 180%);
            border:1px solid #e7e1d6; border-radius:14px;
            padding:12px 14px; margin-bottom:8px;
        ">
            <img src="data:image/png;base64,{b64}" style="
                width:64px; height:64px; border-radius:10px;
                background:#ffffff; object-fit:contain; flex-shrink:0;
            ">
            <div>
                <div style="font-size:0.78rem; color:#8a8073; margin-bottom:2px;">已选定目标方向</div>
                <div style="font-size:1.02rem; font-weight:700; color:#2b2620; margin-bottom:3px;">{field['name']}</div>
                <div style="font-size:0.8rem; color:#8a8073;">这个方向最看重：{top_dim_names}</div>
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
                <div style="font-size:0.9rem; color:#8a8073; margin-bottom:4px;">{field['name']} · 评估结果</div>
                <div style="display:flex; align-items:baseline; gap:6px; margin-bottom:8px;">
                    <span style="font-size:2.4rem; font-weight:800; line-height:1; color:{color};">{total}</span>
                    <span style="font-size:1rem; color:#8a8073;">/ 100</span>
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
    """给打分等待时的插画"连播"动画用的小尺寸缩略图（压到<=90px），只在Streamlit这个
    进程的生命周期里统一读盘+压缩一次并缓存住，不会因为每次点"开始评估"就重新处理一遍
    全部约28张原图（1254x1254px）。返回 (按固定顺序排列的field_id列表,
    {field_id: 90px缩略图的base64字符串})。"""
    order = []
    b64_by_id = {}
    for fid, filename in FIELD_ID_TO_MASCOT_FILENAME.items():
        path = MASCOTS_DIR / filename
        if not path.is_file():
            continue
        img = Image.open(path).convert("RGBA")
        img.thumbnail((90, 90), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64_by_id[fid] = base64.b64encode(buf.getvalue()).decode("ascii")
        order.append(fid)
    return order, b64_by_id


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
                border-radius:50%; background:#fff; object-fit:contain; padding:20px;
                box-shadow:0 10px 34px rgba(43,38,32,0.10);
                opacity:0; transition:opacity .16s ease;
            }}
            #${{OVERLAY_ID}} .rc-reel-visual img.show {{ opacity:1; }}
            #${{OVERLAY_ID}} .rc-reel-visual img.landed {{ animation: rcReelBreathe 2.2s ease-in-out infinite; }}
            @keyframes rcReelBreathe {{
                0%, 100% {{ transform:scale(1); opacity:1; }}
                50% {{ transform:scale(1.045); opacity:0.95; }}
            }}
            #${{OVERLAY_ID}} .rc-landed-ring {{
                position:absolute; inset:-8px; border-radius:50%;
                border:4px solid #3f7a5c; opacity:0; transform:scale(0.85);
                transition:opacity .25s ease, transform .25s ease;
            }}
            #${{OVERLAY_ID}} .rc-landed-ring.show {{ opacity:1; transform:scale(1); }}
            #${{OVERLAY_ID}} .rc-hint-pill {{
                position:absolute; top:22px; left:50%; transform:translateX(-50%);
                background:#fff; border:1px solid #e7e1d6; border-radius:999px;
                padding:7px 16px; font-size:0.8rem; color:#8a8073;
                box-shadow:0 4px 14px rgba(0,0,0,0.05);
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
            + "background:radial-gradient(circle at 50% 42%, #fffdf9 0%, #faf8f4 65%); "
            + "display:flex; flex-direction:column; align-items:center; justify-content:center; gap:28px;";
        overlay.innerHTML =
            '<div class="rc-hint-pill">正在评估中，请稍候…</div>'
            + '<div class="rc-reel-visual" id="rcReelVisual">{imgs_html}<div class="rc-landed-ring" id="rcReelRing"></div></div>'
            + '<div><div class="rc-reel-title" id="rcReelTitle">正在读取简历并调用AI模型打分</div>'
            + '<div class="rc-reel-sub" id="rcReelSub">这个过程一般要十几秒，别急着切走页面</div></div>'
            + '<div class="rc-progress-dots"><span></span><span></span><span></span></div>';
        doc.body.appendChild(overlay);

        var ORDER = {order_json};
        var TARGET = "{target_id}";
        var imgs = overlay.querySelectorAll(".rc-reel-visual img");
        var ring = doc.getElementById("rcReelRing");
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
                    ring.classList.add("show");
                    imgs[targetIdx].classList.add("landed");
                    titleEl.innerHTML = '已为你分析完 <b>{field_name}</b> 方向';
                    subEl.textContent = "评估结果生成中，即将为你呈现…";
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
    通过window.parent.document把之前挂在主页面上的全屏遮罩摘掉，再把placeholder本身清空。
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
    placeholder.empty()


def render_pdf_pages_as_images(pdf_bytes: bytes, dpi: int = 150):
    """把PDF每一页渲染成PNG图片字节，用于在Streamlit页面里直接预览，不用下载打开。"""
    try:
        import pymupdf  # PyMuPDF；新版包名是 pymupdf，import fitz 是旧写法，已弃用
    except ImportError as e:
        raise RuntimeError("缺少依赖 pymupdf，请在venv里执行 pip install pymupdf 后重新运行") from e
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

st.set_page_config(page_title="简历罗盘", page_icon="🧭", layout="wide")
# 2026-09-19：重新启用背景装饰（见 _inject_background_decoration 里的说明）——
# 现在内容区自己盖了层不透明白卡片，装饰图案只会露在卡片外面的留白处，不会再挡文字。
_inject_background_decoration()

if "result" not in st.session_state:
    st.session_state.result = None
if "resume_pdf_bytes" not in st.session_state:
    st.session_state.resume_pdf_bytes = None

framework = load_framework()
field_options = {f["name"]: f["id"] for f in framework["fields"]}
fields_by_id = {f["id"]: f for f in framework["fields"]}
dim_name_by_key = {d["key"]: d["name"] for d in framework["dimensions"]}
CUSTOM_OPTION_LABEL = "🖊️ 其他（自定义方向，我自己填）"

st.title("🧭 简历罗盘")
st.caption("上传简历获取评估报告")

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
default_api_key = secret_api_key or env_api_key

with st.sidebar:
    st.subheader("设置")
    if default_api_key:
        st.success("已经有可用的 API Key（来自部署配置的 Secrets 或系统环境变量），下面可以留空直接用；如果想临时换一个，填了就会优先用你填的这个。")
    api_key_input = st.text_input(
        "Anthropic API Key",
        type="password",
        help="只保存在本次运行的内存里，不会被写入磁盘或上传。留空则使用部署时配置好的 Key（如果有的话）。",
    )
    api_key = api_key_input or default_api_key
    use_cheap = st.checkbox("使用更便宜的 Haiku 模型（速度快、成本更低，但判断可能略粗）", value=False)
    model = CHEAP_MODEL if use_cheap else DEFAULT_MODEL
    st.caption(f"当前模型：`{model}`")
    st.divider()
    # 2026-09-19：默认改回单次调用——2次取中位数虽然更稳，但费用是2倍，
    # 权衡下来先把"便宜"作为默认，稳定性模式留着，需要更谨慎的场合（比如正式发给学生前）
    # 可以手动勾选，按需多花这一倍费用换稳定性，而不是每次都默认多花。
    stable_mode = st.checkbox(
        "稳定性模式：同一份简历调用2次取中位数分数，减少单次波动（费用变成2倍，默认关闭）",
        value=False,
        help=(
            "Claude Sonnet 5 已取消 temperature 等采样参数，模型没法在API层面强制输出完全一致，"
            "调用2次取中位数是目前能做到的最接近\"稳定\"的办法，但要多花一倍费用，默认不开。"
            "平时单次调用已经够用；如果是要正式发给学生、比较看重分数前后一致性的场合，"
            "可以手动勾上，用多一倍费用换一次更稳的结果。"
        ),
    )
    score_runs = 2 if stable_mode else 1
    st.divider()
    st.caption("没有 API Key？去 [console.anthropic.com](https://console.anthropic.com/settings/keys) 免费注册获取。")

col1, col2 = st.columns([1, 1])
with col1:
    uploaded = st.file_uploader("上传简历（PDF）", type=["pdf"])
    student_name = st.text_input("学生姓名", value=(uploaded.name.rsplit(".", 1)[0] if uploaded else ""))
with col2:
    field_name = st.selectbox("目标领域", list(field_options.keys()) + [CUSTOM_OPTION_LABEL])
    is_custom_field = field_name == CUSTOM_OPTION_LABEL
    if not is_custom_field:
        _render_mascot_card(fields_by_id[field_options[field_name]], dim_name_by_key)
    custom_field_name = ""
    if is_custom_field:
        custom_field_name = st.text_input(
            "请输入目标方向名称",
            placeholder="比如：碳中和政策研究、跨境电商运营……",
            help=(
                "这个方向不在左边下拉列表里，没有为它预先准备好加分项清单、常见短板参考、真实招聘JD摘录，"
                "打分会靠模型对这个方向在真实招聘市场上的通用理解来判断，严谨程度会低于列表里的领域，仅供参考。"
            ),
        )
    student_meta = ""

field_ready = (not is_custom_field) or bool(custom_field_name.strip())
run = st.button(
    "开始评估",
    type="primary",
    disabled=not (uploaded and api_key and student_name and field_ready),
)

if run:
    field_id = CUSTOM_FIELD_ID if is_custom_field else field_options[field_name]
    target_field_for_reel = None if is_custom_field else fields_by_id[field_id]
    reel_placeholder = _render_loading_reel(target_field_for_reel)
    spinner_cm = (
        nullcontext()
        if reel_placeholder is not None
        else st.spinner("正在读取简历并调用AI模型打分…")
    )
    try:
        with spinner_cm:
            resume_pdf_bytes = uploaded.getvalue()
            with pdfplumber.open(uploaded) as pdf:
                resume_text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            if not resume_text.strip():
                st.error("没能从这份PDF里提取到文字，可能是扫描件图片版，暂不支持。")
            else:
                result = score_resume(
                    resume_text,
                    field_id,
                    api_key,
                    model=model,
                    runs=score_runs,
                    custom_field_name=custom_field_name.strip() if is_custom_field else None,
                )
                st.session_state.result = result
                st.session_state.resume_pdf_bytes = resume_pdf_bytes
                st.session_state.student_name = student_name
                st.session_state.student_meta = student_meta
    except Exception as e:
        st.error(f"评估失败：{e}")
    finally:
        _clear_loading_reel(reel_placeholder)

result = st.session_state.result
if result:
    st.divider()
    _render_score_badge(result)
    if result.get("stage_note"):
        st.info(result["stage_note"])

    if result["field"].get("id") == CUSTOM_FIELD_ID:
        matched_fields = result["field"].get("matched_fields")
        if matched_fields:
            match_desc = "　/　".join(
                f"{m['name']} {round(m['weight'] * 100)}%" for m in matched_fields
            )
            st.caption(
                f"✏️ 目标方向「{result['field']['name']}」是你自己输入的自定义方向，"
                f"已自动匹配到现有领域并按比例融合出本次打分依据的权重/加分项/短板/JD参考：{match_desc}"
            )
        else:
            st.caption(
                f"✏️ 目标方向「{result['field']['name']}」是你自己输入的自定义方向，"
                "没能自动匹配到相近的现有领域，用的是通用兜底权重，打分依据的是模型对这个方向的通用理解，仅供参考。"
            )

    stability = result.get("stability")
    if stability:
        totals = stability["total_all_runs"]
        spread = max(totals) - min(totals)
        st.caption(
            f"🔁 稳定性模式：本次调用了{stability['runs']}次，{stability['runs']}次总分分别为 "
            f"{' / '.join(str(t) for t in totals)}，波动{spread}分，最终各维度取中位数得到上面这个结果"
        )

    st.subheader("七维度评分")
    dims = {d["key"]: d for d in result["framework"]["dimensions"]}
    for key in ["edu", "exp", "proj", "skill", "cert", "lead", "present"]:
        d = dims[key]
        score = result["dimension_scores"].get(key, 0)
        rationale = result["dimension_rationale"].get(key, "")
        ev_list = result.get("dimension_evidence", {}).get(key, [])
        ok_list = result.get("dimension_evidence_verified", {}).get(key, [])
        bar_col, text_col = st.columns([1, 4])
        with bar_col:
            st.write(f"**{d['name']}**")
            st.progress(score / 5, text=f"{score}/5")
        with text_col:
            st.write(rationale)
            for q, ok in zip(ev_list, ok_list):
                mark = "" if ok else "⚠️ 未在原文核实到："
                st.caption(f"{mark}原文：“{q}”")

    colA, colB = st.columns(2)
    with colA:
        st.subheader("优势")
        for s in result["strengths"]:
            st.markdown(f"- {s}")
    with colB:
        st.subheader("建议提升方向")
        for g in result["gaps"]:
            st.markdown(f"- {g}")

    if result["bonus_checked"]:
        st.subheader("命中的加分项")
        for i in result["bonus_checked"]:
            label, pts = result["field"]["bonus"][i]
            st.markdown(f"- {label}（+{pts}）")

    if result.get("ats_keywords"):
        st.subheader("识别到的ATS关键词")
        st.write("、".join(result["ats_keywords"]))

    if result.get("strong_phrases"):
        st.subheader("工作经历/项目经历中有力的量化成果")
        st.caption("有数据支撑、写得好的句子——在下方的标注版简历里会用绿色标出，这种写法可以多用")
        for s in result["strong_phrases"]:
            st.markdown(f"- {s}")

    if result.get("vague_phrases"):
        st.subheader("工作经历中可优化的表述")
        st.caption("偏笼统、缺乏具体信息量的句子——在下方的标注版简历里会用红色标出，供你参考修改")
        for s in result["vague_phrases"]:
            st.markdown(f"- {s}")

    try:
        pdf_bytes, highlight_info = generate_pdf(
            result,
            st.session_state.student_name,
            st.session_state.student_meta,
            resume_pdf_bytes=st.session_state.resume_pdf_bytes,
        )
    except Exception as e:
        # 打分结果已经在上面完整展示了；PDF生成这一步单独兜底，
        # 失败也不影响用户看到刚才的评分和分析，只是拿不到PDF报告。
        st.error(f"生成PDF报告失败：{e}（上面的评分结果不受影响，可以先看这些）")
        pdf_bytes, highlight_info = None, None

    if pdf_bytes is not None and (result.get("ats_keywords") or result.get("vague_phrases") or result.get("strong_phrases")):
        if highlight_info["attached"]:
            parts = []
            if highlight_info["ats_matched"]:
                parts.append(f"黄色/ATS关键词：{'、'.join(highlight_info['ats_matched'])}")
            if highlight_info["strong_matched"]:
                parts.append(f"绿色/量化成果：{'；'.join(highlight_info['strong_matched'])}")
            if highlight_info["vague_matched"]:
                parts.append(f"红色/可优化表述：{'；'.join(highlight_info['vague_matched'])}")
            st.caption("已在下方的报告预览里标注 —— " + "；".join(parts))
        else:
            st.warning(f"没能生成标注版简历：{highlight_info['reason']}")

    if pdf_bytes is not None:
        st.subheader("📄 报告预览")
        if highlight_info["attached"]:
            st.caption("黄色=ATS关键词　绿色=有力的量化成果　红色=建议优化的表述，不用下载PDF也能直接看完整报告")
        else:
            st.caption("不用下载PDF也能直接看完整报告")
        try:
            # dpi调到300（接近打印质量），源图分辨率够高，不管列宽最终撑到多大都不会糊
            page_images = render_pdf_pages_as_images(pdf_bytes, dpi=300)
            # 用居中的列限制预览宽度，避免图片在宽屏布局下把整页撑满，但也别太窄导致字看不清
            _, preview_col, _ = st.columns([1, 3, 1])
            with preview_col:
                for i, img_bytes in enumerate(page_images):
                    st.image(img_bytes, use_container_width=True)
                    if i < len(page_images) - 1:
                        st.divider()
        except Exception as e:
            st.info(f"页面预览暂时生成不了（{e}），可以用下面的按钮下载PDF查看。")
        out_name = f"{st.session_state.student_name}_评估_{date.today().isoformat()}.pdf"
        out_path = REPORT_DIR / out_name
        out_path.write_bytes(pdf_bytes)
        st.success(f"PDF报告已自动保存到：reports/{out_name}")
        st.download_button("下载PDF报告", data=pdf_bytes, file_name=out_name, mime="application/pdf")

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
        cache_note = f"，其中 {cache_read_tok} tokens 命中缓存（按1折计费）" if cache_read_tok > 0 else ""
        st.caption(
            f"本次调用消耗：输入 {in_tok} tokens，输出 {out_tok} tokens"
            f"（其中思考过程 {think_tok} tokens）{cache_note}，预估费用 ${est_cost:.4f}"
        )

    with st.expander("查看模型原始输出（调试用）"):
        st.code(result["raw_model_output"])
