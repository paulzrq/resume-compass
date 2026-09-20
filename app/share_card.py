"""Generate a private, in-memory share card and invoke native file sharing."""
import base64
import colorsys
import io
from collections import deque
from functools import lru_cache
from pathlib import Path

import qrcode
import streamlit.components.v1 as components
from PIL import Image, ImageDraw, ImageFont
from branding import LOGO_PATH
from mascots import mascot_path

APP_URL = 'https://resume-compass.streamlit.app/'
FONT = str(Path(__file__).parent / 'fonts' / 'NotoSerifCJKsc-Regular.otf')


@lru_cache(maxsize=32)
def artwork(field_id):
    path = mascot_path(field_id)
    if not path:
        return None, (235, 231, 241), (62, 46, 83)
    im = Image.open(path).convert('RGBA')
    im.thumbnail((540, 540))
    # Remove only near-white pixels connected to the outside, preserving shirts.
    px = im.load()
    w, h = im.size
    queue = deque([(x, y) for x in range(w) for y in (0, h-1)] +
                  [(x, y) for y in range(h) for x in (0, w-1)])
    seen = set()
    while queue:
        x, y = queue.popleft()
        if (x, y) in seen or not (0 <= x < w and 0 <= y < h):
            continue
        seen.add((x, y))
        r, g, b, a = px[x, y]
        if min(r, g, b) < 230:
            continue
        px[x, y] = (r, g, b, 0)
        queue.extend(((x-1, y), (x+1, y), (x, y-1), (x, y+1)))
    bins = [0] * 36
    for r, g, b, a in im.getdata():
        hue, sat, val = colorsys.rgb_to_hsv(r/255, g/255, b/255)
        if a and sat > .25 and .2 < val < .95:
            bins[int(hue*36) % 36] += sat
    hue = (max(range(36), key=bins.__getitem__) + .5) / 36
    def rgb(s, v):
        return tuple(round(c*255) for c in colorsys.hsv_to_rgb(hue, s, v))
    return im, rgb(.065, .95), rgb(.42, .28)


def generate_share_card(field_id, field_name, total):
    mascot, bg, ink = artwork(field_id)
    card = Image.new('RGB', (1080, 1440), bg)
    draw = ImageDraw.Draw(card)
    def text(x, y, value, size, max_width=940):
        font = ImageFont.truetype(FONT, size)
        while draw.textbbox((0, 0), value, font=font)[2] > max_width and size > 12:
            size -= 1
            font = ImageFont.truetype(FONT, size)
        draw.text((x, y), value, font=font, fill=ink)
    logo = Image.open(LOGO_PATH).convert('RGBA')
    logo = logo.crop(logo.getchannel('A').getbbox())
    logo.thumbnail((370, 85))
    colored = Image.new('RGBA', logo.size, ink)
    colored.putalpha(logo.getchannel('A'))
    card.paste(colored, (80, 80), colored)
    text(80, 215, '简历罗盘', 32)
    text(80, 285, '我的', 112)
    text(80, 425, '简历评估', 112)
    # Small compass ornament, consistent with the approved card.
    cx, cy = 860, 290
    draw.ellipse((770, 200, 950, 380), outline=ink, width=1)
    draw.line((cx, 180, cx, 400), fill=ink, width=1)
    draw.line((750, cy, 970, cy), fill=ink, width=1)
    draw.polygon([(cx, 220), (875, 305), (cx, 290), (845, 275)], fill=ink)
    text(848, 155, 'N', 23)
    if mascot:
        card.paste(mascot, (520, 570), mascot)
    score = f'{float(total):.1f}'.rstrip('0').rstrip('.') if float(total) % 1 else str(int(total))
    text(80, 675, score, 172, 320)
    text(365, 820, '/100', 40, 150)
    text(80, 915, '简历综合评分', 48, 435)
    text(80, 1000, '目标方向 · ' + field_name, 31, 445)
    draw.line((70, 1160, 1010, 1160), fill=ink, width=1)
    text(80, 1200, '你的简历，还有哪些可能？', 46, 710)
    text(80, 1275, 'resume-compass.streamlit.app', 25, 720)
    qr = qrcode.QRCode(box_size=5, border=4)
    qr.add_data(APP_URL)
    qr.make(fit=True)
    qr_image = qr.make_image(fill_color=ink, back_color=bg).convert('RGB')
    qr_image = qr_image.resize((170, 170), Image.Resampling.NEAREST)
    card.paste(qr_image, (840, 1180))
    text(850, 1350, '扫码评估简历', 23, 170)
    text(80, 1380, 'AI 辅助评估，仅供参考', 20)
    output = io.BytesIO()
    card.save(output, format='PNG')
    return output.getvalue()


_share_component = components.declare_component(
    "resume_share", path=str(Path(__file__).parent / "share_component")
)


def render_share_button(png, key):
    return _share_component(png=base64.b64encode(png).decode('ascii'),
                            key=key, default=False)
