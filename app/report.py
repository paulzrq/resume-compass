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

    # Removed the report metadata strip; preserve all existing body positions.
    y -= 46

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
    label_w = _mixed_width(c, "Overall Competitiveness Score", label_size)
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
    _draw_mixed(c, "Overall Competitiveness Score", text_x, ty, label_size, color=SUB)
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

    y = section_title(y, "Dimension Performance")

    if not result.get("evidence_verification_available", True):
        y = _draw_wrapped_mixed(
            c,
            "ℹ️ This resume was uploaded as an image, so the quoted excerpts below could not be verified word-for-word. Please review with care.",
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

        _draw_mixed(c, "Developing", MARGIN, y, 6.5, color=SUB)
        _draw_mixed_centred(c, "Average", PAGE_W / 2, y, 6.5, color=SUB)
        _draw_mixed_right(c, "Top", PAGE_W - MARGIN, y, 6.5, color=SUB)
        y -= 12

        rtext = rationale.get(key, "")
        if rtext:
            y = _draw_wrapped_mixed(c, rtext, MARGIN, y, 8.3, CONTENT_W, 11, color=HexColor("#3a3a3a"))

        ev_list = evidence.get(key, [])
        ok_list = evidence_verified.get(key, [])
        for q, ok in zip(ev_list, ok_list):
            mark = "" if ok else "[Unverified] "
            y = new_page_if_needed(y, 20)
            y = _draw_wrapped_mixed(c, f"{mark}原文：“{q}”", MARGIN + 8, y, 7.3, CONTENT_W - 8, 10, color=SUB)
        y -= 10

    # ---- interpretation ----
    y = new_page_if_needed(y, 140)
    y = section_title(y, "Interpretation")
    col_w = (CONTENT_W - 20) / 2
    left_x, right_x = MARGIN, MARGIN + col_w + 20
    top_y = y

    _draw_mixed(c, "Strengths", left_x, top_y, 9.5, color=BLUE)
    yl = top_y - 14
    for s in result["strengths"]:
        yl = _draw_wrapped_mixed(c, "· " + s, left_x, yl, 8.3, col_w, 11)
        yl -= 4

    _draw_mixed(c, "Areas for Improvement", right_x, top_y, 9.5, color=BLUE)
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
        "This report was generated with AI assistance based on the Resume Compass student employability framework and its scoring anchors. "
        "For internal reference only; it does not guarantee final job outcomes."
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
            "Original resume PDF bytes not available — for image uploads this section is skipped by design "
            "(images have no PDF page coordinates for keyword highlighting). If you uploaded a PDF and still see this, "
            "it may be stale session state; re-upload the resume and evaluate again."
        )
    elif not ats_keywords and not vague_phrases and not strong_phrases:
        highlight_info["reason"] = "The AI did not identify any verifiable content to annotate this time"
    else:
        try:
            from highlight import highlight_pdf_multi
        except Exception as e:
            highlight_info["reason"] = (
                f"缺少依赖 pypdf，无法生成标注附页（{e}）。"
                "Please run 'pip install pypdf' in the venv and evaluate again."
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
                    highlight_info["reason"] = "Some content could not be precisely located on the resume layout (possibly due to unusual formatting or scans)"
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
        y = section_title(y, "Appendix: Annotated Original Resume")
        intro = (
            "Below is your uploaded original resume with three kinds of annotations: yellow highlights are AI-identified keywords that may help with ATS "
            "(Applicant Tracking System, the automated resume screening systems employers use); "
            "green highlights are well-written, data-backed quantified achievements in sections other than education and skills (work experience, projects, leadership & extracurriculars, honors, etc.) "
            "— this style is worth using more often; "
            "red highlights are vague, low-information phrases in those same sections that should be reworked "
            "(e.g., unclear what was done, what methods were used, or what resulted). All three are for reference only; "
            "what ultimately matters is the substance of your experience."
        )
        y = _draw_wrapped_mixed(c, intro, MARGIN, y, 8.5, CONTENT_W, 12, color=HexColor("#3a3a3a"))
        y -= 8
        if ats_matched:
            y = _draw_wrapped_mixed(
                c, "Yellow highlights · ATS keywords:" + "、".join(ats_matched),
                MARGIN, y, 8, CONTENT_W, 11, color=INK,
            )
            y -= 4
        if strong_matched:
            y = _draw_wrapped_mixed(
                c, "Green highlights · Strong quantified achievements:" + "；".join(strong_matched),
                MARGIN, y, 8, CONTENT_W, 11, color=HexColor("#3E8E3E"),
            )
            y -= 4
        if vague_matched:
            y = _draw_wrapped_mixed(
                c, "Red highlights · Phrases to improve:" + "；".join(vague_matched),
                MARGIN, y, 8, CONTENT_W, 11, color=HexColor("#B23A3A"),
            )
            y -= 4

        ats_unmatched = [k for k in ats_keywords if k not in ats_matched]
        vague_unmatched = [k for k in vague_phrases if k not in vague_matched]
        strong_unmatched = [k for k in strong_phrases if k not in strong_matched]
        skipped_notes = []
        if ats_unmatched:
            skipped_notes.append("ATS keywords:" + "、".join(ats_unmatched))
        if strong_unmatched:
            skipped_notes.append("Quantified achievements:" + "；".join(strong_unmatched))
        if vague_unmatched:
            skipped_notes.append("Phrases to improve:" + "；".join(vague_unmatched))
        if skipped_notes:
            y = _draw_wrapped_mixed(
                c,
                "The following could not be precisely located on the layout (possibly due to line breaks / unusual formatting); for reference only:" + "；".join(skipped_notes),
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
