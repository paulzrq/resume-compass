"""
简历罗盘 · ATS关键词高亮
在用户上传的原始简历PDF（保留原始排版）上，把AI识别出的ATS关键词用半透明高亮
标注出来。做法：pdfplumber 定位关键词在页面上的坐标 -> reportlab 画一层高亮矩形
的透明叠加页 -> pypdf 把叠加页合并回原始PDF页面。不改变简历本身的任何内容。
"""
import re
from io import BytesIO

import pdfplumber
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas

HIGHLIGHT_RGB = (1, 0.85, 0.2)  # 半透明黄色
HIGHLIGHT_ALPHA = 0.4


def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _strip_punct(s: str) -> str:
    return re.sub(r"[^\w\s]", "", s)


def _token(s: str) -> str:
    return _strip_punct(_normalize(s))


def find_keyword_boxes(pdf_bytes: bytes, keywords: list) -> tuple:
    """在PDF每一页里找关键词出现的位置。
    返回 (每页的高亮框列表, 实际被定位到的关键词列表)。
    每页框列表里每个元素是 {"x0":..,"x1":..,"top":..,"bottom":..}（pdfplumber坐标系，
    从页面左上角起算，单位pt）。关键词允许是多个词组成的短语，按连续词匹配。"""
    keywords = [kw for kw in keywords if kw and kw.strip()]
    boxes_per_page = []
    matched_keywords = set()

    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            words = page.extract_words(x_tolerance=1.5, keep_blank_chars=False) or []
            norm_words = [_token(w["text"]) for w in words]
            page_boxes = []
            for kw in keywords:
                kw_tokens = [t for t in (_token(part) for part in kw.split()) if t]
                if not kw_tokens:
                    continue
                n = len(kw_tokens)
                for i in range(len(norm_words) - n + 1):
                    if norm_words[i:i + n] == kw_tokens:
                        span = words[i:i + n]
                        page_boxes.append({
                            "x0": min(w["x0"] for w in span),
                            "x1": max(w["x1"] for w in span),
                            "top": min(w["top"] for w in span),
                            "bottom": max(w["bottom"] for w in span),
                        })
                        matched_keywords.add(kw)
            boxes_per_page.append(page_boxes)
    return boxes_per_page, sorted(matched_keywords)


def highlight_pdf_multi(pdf_bytes: bytes, groups: list) -> tuple:
    """支持同一份简历上叠加多种颜色的高亮（比如黄色=ATS关键词，红色=可优化的表述）。
    groups: [{"keywords": [...], "rgb": (r,g,b), "alpha": 0.4}, ...]
    返回 (高亮后的PDF字节, 每组实际被定位到的关键词/短语列表——顺序、长度都跟groups一一对应)。
    任何一步出错都不应该让整个报告生成失败，由调用方 try/except 兜底。"""
    per_group = []
    for g in groups:
        boxes_per_page, matched = find_keyword_boxes(pdf_bytes, g.get("keywords") or [])
        per_group.append({"boxes_per_page": boxes_per_page, "matched": matched})

    if not any(gi["matched"] for gi in per_group):
        return pdf_bytes, [gi["matched"] for gi in per_group]

    reader = PdfReader(BytesIO(pdf_bytes))
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        page_has_boxes = any(
            i < len(gi["boxes_per_page"]) and gi["boxes_per_page"][i] for gi in per_group
        )
        if page_has_boxes:
            page_w = float(page.mediabox.width)
            page_h = float(page.mediabox.height)
            overlay_buf = BytesIO()
            oc = rl_canvas.Canvas(overlay_buf, pagesize=(page_w, page_h))
            for g, gi in zip(groups, per_group):
                page_boxes = gi["boxes_per_page"][i] if i < len(gi["boxes_per_page"]) else []
                if not page_boxes:
                    continue
                oc.setFillColorRGB(*g.get("rgb", HIGHLIGHT_RGB))
                oc.setFillAlpha(g.get("alpha", HIGHLIGHT_ALPHA))
                for b in page_boxes:
                    x = b["x0"] - 1
                    y = page_h - b["bottom"] - 1
                    w = (b["x1"] - b["x0"]) + 2
                    h = (b["bottom"] - b["top"]) + 2
                    oc.rect(x, y, w, h, stroke=0, fill=1)
            oc.save()
            overlay_buf.seek(0)
            overlay_page = PdfReader(overlay_buf).pages[0]
            page.merge_page(overlay_page)
        writer.add_page(page)

    out_buf = BytesIO()
    writer.write(out_buf)
    return out_buf.getvalue(), [gi["matched"] for gi in per_group]


def highlight_pdf(pdf_bytes: bytes, keywords: list) -> tuple:
    """单一颜色版本，保留给只需要一组关键词时用（内部转调 highlight_pdf_multi）。
    返回 (高亮后的PDF字节, 实际被高亮到的关键词列表)。"""
    out_bytes, matched_by_group = highlight_pdf_multi(
        pdf_bytes, [{"keywords": keywords, "rgb": HIGHLIGHT_RGB, "alpha": HIGHLIGHT_ALPHA}]
    )
    return out_bytes, matched_by_group[0]
