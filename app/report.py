"""
简历罗盘 · PDF 报告生成（官方成绩单风 / Version C）
纯 reportlab 实现，不依赖浏览器或系统级图形库，方便本地直接运行。

中英文混排说明：内嵌的中文字体（DroidSansFallback）只覆盖CJK字符，不含拉丁字母/数字，
所以每个字符按码位路由到"中文字体"或reportlab内置的Helvetica（拉丁/数字/基础标点），
_mixed 开头的一组函数就是做这件事的。

如果调用时传入了 resume_pdf_bytes（学生上传的原始简历PDF字节），且打分结果里有
ats_keywords，会在报告最后追加一份"高亮标注版"的简历原文——用 highlight.py 定位
关键词坐标、盖一层半透明高亮，再用 pypdf 把这些页拼到报告后面。任何一步失败都不
应该影响主报告的生成，因此整段逻辑包在 try/except 里，失败就只返回不带附件的报告。
"""
from io import BytesIO
from pathlib import Path as _Path
from datetime import date

from reportlab.lib.pagesizes import A4
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as rl_canvas

from branding import draw_report_logo
from mascots import mascot_path

_FONT_PATH = _Path(__file__).resolve().parent / "fonts" / "DroidSansFallbackFull.ttf"
CJK_FONT = "DroidSansFallback"
LATIN_FONT = "Helvetica"
LATIN_FONT_BOLD = "Helvetica-Bold"
pdfmetrics.registerFont(TTFont(CJK_FONT, str(_FONT_PATH)))

BLUE = HexColor("#1B4F91")
LINE = HexColor("#C7D2DE")
INK = HexColor("#1B2733")
SUB = HexColor("#5A6B7D")
ZONES = [HexColor("#E8ECF2"), HexColor("#D3DEEB"), HexColor("#AEC5E0"), HexColor("#7FA3CC"), BLUE]

PAGE_W, PAGE_H = A4
MARGIN = 46
CONTENT_W = PAGE_W - 2 * MARGIN

CJK_THRESHOLD = 0x2E80  # 低于这个码位按拉丁文处理，交给Helvetica


def _font_for(ch: str, bold: bool = False) -> str:
    if ord(ch) < CJK_THRESHOLD:
        return LATIN_FONT_BOLD if bold else LATIN_FONT
    return CJK_FONT


def _mixed_width(c, text: str, size: float, bold: bool = False) -> float:
    return sum(c.stringWidth(ch, _font_for(ch, bold), size) for ch in text)


def _draw_mixed(c, text: str, x: float, y: float, size: float, color=INK, bold: bool = False):
    c.setFillColor(color)
    cx = x
    for ch in text:
        font = _font_for(ch, bold)
        c.setFont(font, size)
        c.drawString(cx, y, ch)
        cx += c.stringWidth(ch, font, size)
    return cx


def _draw_mixed_centred(c, text: str, cx: float, y: float, size: float, color=INK, bold: bool = False):
    w = _mixed_width(c, text, size, bold)
    _draw_mixed(c, text, cx - w / 2, y, size, color, bold)
    return w


def _draw_mixed_right(c, text: str, right_x: float, y: float, size: float, color=INK, bold: bool = False):
    w = _mixed_width(c, text, size, bold)
    _draw_mixed(c, text, right_x - w, y, size, color, bold)
    return w


def _wrap_mixed(c, text: str, size: float, max_width: float):
    lines, current = [], ""
    for ch in text:
        trial = current + ch
        if _mixed_width(c, trial, size) > max_width and current:
            lines.append(current)
            current = ch
        else:
            current = trial
    if current:
        lines.append(current)
    return lines


def _draw_wrapped_mixed(c, text: str, x: float, y: float, size: float, max_width: float, leading: float, color=INK):
    for line in _wrap_mixed(c, text, size, max_width):
        _draw_mixed(c, line, x, y, size, color)
        y -= leading
    return y


def _draw_mascot_image(c, image_path, cx, cy, box_size):
    """在 (cx, cy) 为中心、box_size 见方的区域内绘制完整的职业插画人物：
    不做圆形裁剪（保留人物完整造型，不会被裁掉手脚），
    并把画布本身接近纯白的底色转成透明，这样嵌入PDF白色页面时不会露出一块方形/圆形背景。
    调用方需要自行 try/except 兜底（比如图片损坏）。"""
    from PIL import Image as PILImage, ImageChops

    with PILImage.open(image_path) as im:
        im = im.convert("RGB")
        target_px = 320  # 控制嵌入体积，够PDF内清晰显示即可
        if max(im.size) > target_px:
            im.thumbnail((target_px, target_px), PILImage.LANCZOS)

        # 用"离纯白的最大偏移量"当作不透明度：越接近纯白（背景）越透明，
        # 只留一个窄的过渡带做抗锯齿，人物本身（哪怕是浅色衣服/皮肤）都远超这个偏移量。
        r, g, b = im.split()
        min_rgb = ImageChops.darker(ImageChops.darker(r, g), b)
        LOW, HIGH = 6, 34
        span = HIGH - LOW
        alpha = min_rgb.point(
            lambda v: 0 if (255 - v) < LOW else (255 if (255 - v) > HIGH else int((255 - v - LOW) * 255 / span))
        )

        rgba = im.convert("RGBA")
        rgba.putalpha(alpha)

        buf = BytesIO()
        rgba.save(buf, format="PNG")
        buf.seek(0)
        img_reader = ImageReader(buf)
        iw, ih = rgba.size

    scale = box_size / max(iw, ih)
    draw_w, draw_h = iw * scale, ih * scale
    c.drawImage(img_reader, cx - draw_w / 2, cy - draw_h / 2, width=draw_w, height=draw_h, mask="auto")


DIM_ORDER = ["edu", "exp", "proj", "skill", "cert", "lead", "present"]


def generate_pdf(result: dict, student_name: str, student_meta: str = "", resume_pdf_bytes: bytes = None) -> bytes:
    """result: scoring.score_resume() 的返回值。
    resume_pdf_bytes: 学生上传的原始简历PDF原始字节（可选）——传了才会在报告末尾
    追加ATS关键词高亮标注版简历。"""
    framework = result["framework"]
    field = result["field"]
    dims = {d["key"]: d for d in framework["dimensions"]}
    scores = result["dimension_scores"]
    rationale = result["dimension_rationale"]
    evidence = result.get("dimension_evidence", {})
    evidence_verified = result.get("dimension_evidence_verified", {})

    buf = BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=A4)

    def new_page_if_needed(y, needed):
        if y - needed < MARGIN + 30:
            c.showPage()
            return PAGE_H - MARGIN
        return y

    y = PAGE_H - MARGIN

    draw_report_logo(c, MARGIN, y + 8)

    # ---- letterhead ----
    c.setStrokeColor(BLUE)
    c.setLineWidth(1.8)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 12
    _draw_mixed(c, "简历罗盘 · 学生竞争力评估中心", MARGIN, y, 8, color=BLUE)
    _draw_mixed_right(
        c,
        f"报告编号 RC-{date.today().strftime('%Y%m%d')}　签发日期 {date.today().isoformat()}",
        PAGE_W - MARGIN, y, 8, color=SUB,
    )
    y -= 8
    c.setStrokeColor(BLUE)
    c.setLineWidth(0.6)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 26

    # ---- student ----
    _draw_mixed_centred(c, student_name, PAGE_W / 2, y, 16, color=INK)
    y -= 15
    if student_meta:
        _draw_mixed_centred(c, student_meta, PAGE_W / 2, y, 9, color=SUB)
        y -= 22
    else:
        y -= 10

    # ---- total score（有对应插画时，头像与分数左右排列；否则整体居中）----
    mpath = mascot_path(field.get("id", ""))

    total_str = f"{result['total']}"
    score_size, slash_size = 40, 13
    label_size, field_size = 8.5, 10.5

    c.setFont(LATIN_FONT_BOLD, score_size)
    score_w = c.stringWidth(total_str, LATIN_FONT_BOLD, score_size)
    slash_w = _mixed_width(c, "/100", slash_size)
    score_line_w = score_w + 4 + slash_w
    label_w = _mixed_width(c, "综合竞争力得分", label_size)
    field_text = f"目标领域：{field['name']}"
    field_w = _mixed_width(c, field_text, field_size)
    text_block_w = max(label_w, score_line_w, field_w)

    # 标签 / 分数 / 目标领域三行的行高，用来把文字块和插画人物整体垂直居中对齐
    row_h_label, row_gap1 = 12, 10
    row_h_score, row_gap2 = 34, 12
    row_h_field = 13
    text_block_h = row_h_label + row_gap1 + row_h_score + row_gap2 + row_h_field

    mascot_box, mascot_gap = 88, 20
    mascot_drawn = False
    text_x = PAGE_W / 2 - text_block_w / 2
    if mpath is not None:
        try:
            group_w = mascot_box + mascot_gap + text_block_w
            start_x = PAGE_W / 2 - group_w / 2
            mascot_cy = y - mascot_box / 2
            _draw_mascot_image(c, mpath, start_x + mascot_box / 2, mascot_cy, mascot_box)
            text_x = start_x + mascot_box + mascot_gap
            mascot_drawn = True
        except Exception:
            text_x = PAGE_W / 2 - text_block_w / 2

    block_h = max(mascot_box, text_block_h) if mascot_drawn else text_block_h
    ty = y - (block_h - text_block_h) / 2  # 文字块相对插画人物垂直居中

    ty -= row_h_label
    _draw_mixed(c, "综合竞争力得分", text_x, ty, label_size, color=SUB)
    ty -= row_gap1 + row_h_score
    baseline_y = ty
    c.setFont(LATIN_FONT_BOLD, score_size)
    c.setFillColor(BLUE)
    c.drawString(text_x, baseline_y, total_str)
    _draw_mixed(c, "/100", text_x + score_w + 4, baseline_y, slash_size, color=SUB)
    ty -= row_gap2 + row_h_field
    _draw_mixed(c, field_text, text_x, ty, field_size, color=BLUE)

    y -= block_h + 22

    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 26

    def section_title(y, text):
        _draw_mixed(c, text, MARGIN, y, 11, color=BLUE)
        return y - 16

    y = section_title(y, "分项表现区间")

    if not result.get("evidence_verification_available", True):
        y = _draw_wrapped_mixed(
            c,
            "ℹ️ 这份简历是以图片形式上传评估的，下面引用的原文片段没法逐字核对是否真实存在，请自行留意准确性。",
            MARGIN, y, 7.5, CONTENT_W, 10, color=SUB,
        )
        y -= 4

    band_w = CONTENT_W
    zone_w = band_w / 5
    for key in DIM_ORDER:
        d = dims[key]
        score = scores.get(key, 3)
        y = new_page_if_needed(y, 90)

        _draw_mixed(c, d["name"], MARGIN, y, 9.5, color=INK)
        _draw_mixed_right(c, f"{score} / 5", PAGE_W - MARGIN, y, 9.5, color=BLUE)
        y -= 12

        for i, zc in enumerate(ZONES):
            c.setFillColor(zc)
            c.rect(MARGIN + i * zone_w, y - 10, zone_w, 10, stroke=0, fill=1)
        marker_x = MARGIN + zone_w * score
        c.setFillColor(INK)
        p = c.beginPath()
        p.moveTo(marker_x - 4, y + 5)
        p.lineTo(marker_x + 4, y + 5)
        p.lineTo(marker_x, y - 2)
        p.close()
        c.drawPath(p, fill=1, stroke=0)
        y -= 20

        _draw_mixed(c, "薄弱", MARGIN, y, 6.5, color=SUB)
        _draw_mixed_centred(c, "中等", PAGE_W / 2, y, 6.5, color=SUB)
        _draw_mixed_right(c, "顶尖", PAGE_W - MARGIN, y, 6.5, color=SUB)
        y -= 12

        rtext = rationale.get(key, "")
        if rtext:
            y = _draw_wrapped_mixed(c, rtext, MARGIN, y, 8.3, CONTENT_W, 11, color=HexColor("#3a3a3a"))

        ev_list = evidence.get(key, [])
        ok_list = evidence_verified.get(key, [])
        for q, ok in zip(ev_list, ok_list):
            mark = "" if ok else "[未核实] "
            y = new_page_if_needed(y, 20)
            y = _draw_wrapped_mixed(c, f"{mark}原文：“{q}”", MARGIN + 8, y, 7.3, CONTENT_W - 8, 10, color=SUB)
        y -= 10

    # ---- interpretation ----
    y = new_page_if_needed(y, 140)
    y = section_title(y, "解读说明")
    col_w = (CONTENT_W - 20) / 2
    left_x, right_x = MARGIN, MARGIN + col_w + 20
    top_y = y

    _draw_mixed(c, "优势", left_x, top_y, 9.5, color=BLUE)
    yl = top_y - 14
    for s in result["strengths"]:
        yl = _draw_wrapped_mixed(c, "· " + s, left_x, yl, 8.3, col_w, 11)
        yl -= 4

    _draw_mixed(c, "建议提升方向", right_x, top_y, 9.5, color=BLUE)
    yr = top_y - 14
    for g in result["gaps"]:
        yr = _draw_wrapped_mixed(c, "· " + g, right_x, yr, 8.3, col_w, 11)
        yr -= 4

    y = min(yl, yr) - 14

    # ---- disclaimer ----
    y = new_page_if_needed(y, 60)
    c.setStrokeColor(LINE)
    c.setLineWidth(0.5)
    c.line(MARGIN, y, PAGE_W - MARGIN, y)
    y -= 12
    disclaimer = (
        "本报告依据「简历罗盘」学生求职竞争力评估框架、由AI辅助阅读简历并对照既定评分锚点生成，"
        "供内部参考，不构成对最终求职结果的保证。"
    )
    if result.get("stage_note"):
        disclaimer += " " + result["stage_note"]
    y = _draw_wrapped_mixed(c, disclaimer, MARGIN, y, 7.3, CONTENT_W, 10, color=SUB)

    # ---- 附：简历原文标注（ATS关键词=黄色 / 教育/技能之外部分建议优化的表述=红色，可选） ----
    # highlight_info 记录这一步到底成不成功、不成功是为什么——不再静默失败，
    # 好让 app.py 能把原因明确地展示给用户看（比如缺依赖、没识别到内容等）。
    ats_keywords = result.get("ats_keywords") or []
    vague_phrases = result.get("vague_phrases") or []
    strong_phrases = result.get("strong_phrases") or []
    highlighted_bytes = None
    ats_matched, vague_matched, strong_matched = [], [], []
    highlight_info = {
        "attached": False, "reason": "",
        "ats_matched": [], "ats_unmatched": [],
        "vague_matched": [], "vague_unmatched": [],
        "strong_matched": [], "strong_unmatched": [],
        # 独立的、只包含"标注版简历"那几页的PDF字节（不含前面的评分报告页）——
        # 供 app.py 在页面上直接预览标注效果用，不需要用户下载完整报告才能看到。
        "highlighted_resume_pdf_bytes": None,
    }

    if not resume_pdf_bytes:
        highlight_info["reason"] = (
            "没有拿到简历原始PDF字节——如果这份简历是以图片格式上传评估的，这一段本来就会跳过"
            "（图片没有PDF页面坐标信息，没法做关键词高亮定位）；如果上传的明明是PDF却看到这条提示，"
            "可能是旧版本session状态，重新上传一次简历再评估即可"
        )
    elif not ats_keywords and not vague_phrases and not strong_phrases:
        highlight_info["reason"] = "本次AI没有识别出可核实的标注内容"
    else:
        try:
            from highlight import highlight_pdf_multi
        except Exception as e:
            highlight_info["reason"] = (
                f"缺少依赖 pypdf，无法生成标注附页（{e}）。"
                "请在venv里执行 pip install pypdf 后重新评估一次。"
            )
        else:
            try:
                groups = [
                    {"keywords": ats_keywords, "rgb": (1, 0.85, 0.2), "alpha": 0.4},     # 黄色：ATS关键词
                    {"keywords": vague_phrases, "rgb": (1, 0.45, 0.45), "alpha": 0.42},  # 红色：可优化的表述
                    {"keywords": strong_phrases, "rgb": (0.55, 0.82, 0.45), "alpha": 0.42},  # 绿色：有力的量化成果
                ]
                highlighted_bytes, (ats_matched, vague_matched, strong_matched) = highlight_pdf_multi(
                    resume_pdf_bytes, groups
                )
                if not ats_matched and not vague_matched and not strong_matched:
                    highlight_info["reason"] = "内容未能在简历版面上精确定位（可能是特殊排版或扫描件）"
                    highlighted_bytes = None
            except Exception as e:
                highlight_info["reason"] = f"生成标注附页时出错：{e}"
                highlighted_bytes = None

    if highlighted_bytes:
        # 只有当前页剩余空间不够时才另起一页，避免像之前那样强制跳到新的一页、
        # 结果这一页只有几行字、下面一大片空白。够放的话就紧接着当前页往下画。
        y = new_page_if_needed(y, 160)
        if y < PAGE_H - MARGIN - 5:
            y -= 14
            c.setStrokeColor(LINE)
            c.setLineWidth(0.5)
            c.line(MARGIN, y, PAGE_W - MARGIN, y)
            y -= 20
        y = section_title(y, "附：简历原文标注")
        intro = (
            "以下附上你上传的简历原文，做了三类标注：黄色高亮是AI识别出的、对ATS"
            "（Applicant Tracking System，用人单位常用的简历初筛系统）筛选可能有帮助的关键词；"
            "绿色高亮是除教育背景、技能之外的部分（工作经历、项目经历、领导力与课外活动、荣誉奖项等）"
            "里写得好、有数据支撑的量化成果，这种写法可以多用；"
            "红色高亮是同样这些部分里偏笼统、缺乏具体信息量、建议重新打磨的表述"
            "（比如看不出具体做了什么、用了什么方法、产生了什么结果）。三者都仅供参考，"
            "内容本身的含金量才是决定录用的关键。"
        )
        y = _draw_wrapped_mixed(c, intro, MARGIN, y, 8.5, CONTENT_W, 12, color=HexColor("#3a3a3a"))
        y -= 8
        if ats_matched:
            y = _draw_wrapped_mixed(
                c, "黄色标注 · ATS关键词：" + "、".join(ats_matched),
                MARGIN, y, 8, CONTENT_W, 11, color=INK,
            )
            y -= 4
        if strong_matched:
            y = _draw_wrapped_mixed(
                c, "绿色标注 · 有力的量化成果：" + "；".join(strong_matched),
                MARGIN, y, 8, CONTENT_W, 11, color=HexColor("#3E8E3E"),
            )
            y -= 4
        if vague_matched:
            y = _draw_wrapped_mixed(
                c, "红色标注 · 建议优化的表述：" + "；".join(vague_matched),
                MARGIN, y, 8, CONTENT_W, 11, color=HexColor("#B23A3A"),
            )
            y -= 4

        ats_unmatched = [k for k in ats_keywords if k not in ats_matched]
        vague_unmatched = [k for k in vague_phrases if k not in vague_matched]
        strong_unmatched = [k for k in strong_phrases if k not in strong_matched]
        skipped_notes = []
        if ats_unmatched:
            skipped_notes.append("ATS关键词：" + "、".join(ats_unmatched))
        if strong_unmatched:
            skipped_notes.append("量化成果：" + "；".join(strong_unmatched))
        if vague_unmatched:
            skipped_notes.append("建议优化表述：" + "；".join(vague_unmatched))
        if skipped_notes:
            y = _draw_wrapped_mixed(
                c,
                "以下内容未能在版面上精确定位（可能是断行/特殊排版导致），仅供参考：" + "；".join(skipped_notes),
                MARGIN, y, 7, CONTENT_W, 10, color=SUB,
            )

        highlight_info["attached"] = True
        highlight_info["highlighted_resume_pdf_bytes"] = highlighted_bytes
        highlight_info["ats_matched"] = ats_matched
        highlight_info["ats_unmatched"] = ats_unmatched
        highlight_info["vague_matched"] = vague_matched
        highlight_info["vague_unmatched"] = vague_unmatched
        highlight_info["strong_matched"] = strong_matched
        highlight_info["strong_unmatched"] = strong_unmatched

    c.save()
    report_bytes = buf.getvalue()

    if not highlighted_bytes:
        return report_bytes, highlight_info

    try:
        from pypdf import PdfReader, PdfWriter
        writer = PdfWriter()
        for p in PdfReader(BytesIO(report_bytes)).pages:
            writer.add_page(p)
        for p in PdfReader(BytesIO(highlighted_bytes)).pages:
            writer.add_page(p)
        out = BytesIO()
        writer.write(out)
        return out.getvalue(), highlight_info
    except Exception as e:
        highlight_info["attached"] = False
        highlight_info["reason"] = f"合并PDF页面时出错：{e}"
        return report_bytes, highlight_info
