"""Generate a private, in-memory share card and invoke native file sharing."""
import base64
import colorsys
import io
import math
import secrets
from collections import deque
from functools import lru_cache
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components
from PIL import Image, ImageDraw, ImageFont
from branding import LOGO_PATH
from mascots import mascot_path

APP_URL = 'https://resume-compass.streamlit.app/'
QR_CODE_PATH = Path(__file__).parent / 'assets' / 'qr_code.png'
CARD_VERSION = 'layouts-abdef-1'
SCALE = 2
BOLD_FONT = str(Path(__file__).parent / 'fonts' / 'NotoSerifCJKsc-Bold.otf')
FONT = str(Path(__file__).parent / 'fonts' / 'NotoSerifCJKsc-Regular.otf')


@lru_cache(maxsize=8)
def artwork(field_id):
    path = mascot_path(field_id)
    if not path:
        return None, (235, 231, 241), (62, 46, 83)
    im = Image.open(path).convert('RGBA')
    im.thumbnail((1254, 1254), Image.Resampling.LANCZOS)
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


# Coordinates are in a 1080 x 1440 design grid, rendered at 2x.
LAYOUTS = {
    'A': dict(title=[(70, 300, '我的简历评估', 96, 735)], brand=(855, 88),
              compass=(915, 265, 104), person=(530, 490, 495, 590), score=(75, 590, 420, 235)),
    'B': dict(title=[(80, 270, '我的', 130, 600), (80, 420, '简历评估', 130, 600)], brand=(80, 207),
              compass=(885, 270, 125), person=(60, 585, 470, 535), score=(575, 680, 435, 240)),
    'D': dict(title=[(75, 260, '我的', 137, 625), (75, 450, '简历评估', 137, 625)], brand=(845, 85),
              compass=(875, 340, 135), person=(545, 605, 480, 490), score=(75, 685, 430, 230)),
    'E': dict(title=[(720, 300, '我的', 132, 300), (500, 470, '简历评估', 120, 520)], brand=(855, 220),
              compass=(235, 345, 125), person=(40, 590, 475, 520), score=(555, 715, 460, 225)),
    'F': dict(title=[(75, 255, '我的简历评估', 112, 940)], brand=(80, 195),
              compass=(230, 600, 123), person=(425, 455, 590, 660), score=(75, 815, 345, 165)),
}


def choose_share_layout():
    """Choose once at assessment completion, never while drawing/rerunning."""
    return secrets.choice(tuple(LAYOUTS))


def generate_share_card(field_id, field_name, total, layout='A'):
    if layout not in LAYOUTS:
        raise ValueError('Unknown share card layout')
    spec = LAYOUTS[layout]
    mascot, bg, ink = artwork(field_id)
    card = Image.new('RGB', (1080 * SCALE, 1440 * SCALE), bg)
    draw = ImageDraw.Draw(card)
    def coords(values):
        return tuple(round(v * SCALE) for v in values)
    def text(x, y, value, size, max_width=940, bold=False):
        path = BOLD_FONT if bold else FONT
        font = ImageFont.truetype(path, round(size * SCALE))
        while draw.textlength(value, font=font) > max_width * SCALE and size > 12:
            size -= 1
            font = ImageFont.truetype(path, round(size * SCALE))
        draw.text(coords((x, y)), value, font=font, fill=ink, anchor='lt')
        return draw.textlength(value, font=font) / SCALE
    def line(points, width=1):
        draw.line(coords(points), fill=ink, width=max(1, round(width*SCALE)))
    logo = Image.open(LOGO_PATH).convert('RGBA')
    logo = logo.crop(logo.getchannel('A').getbbox())
    logo.thumbnail(coords((370, 85)), Image.Resampling.LANCZOS)
    colored = Image.new('RGBA', logo.size, ink)
    colored.putalpha(logo.getchannel('A'))
    card.paste(colored, coords((80, 80)), colored)
    text(*spec['brand'], '简历罗盘', 32, 170)
    for x, y, label, size, width in spec['title']:
        text(x, y, label, size, width, True)
    # Eight-point compass with a dashed inner circle and cardinal letters.
    cx, cy, radius = spec['compass']
    draw.ellipse(coords((cx-radius,cy-radius,cx+radius,cy+radius)),outline=ink,width=SCALE)
    inner=radius*.76
    for start in range(0,360,9):
        draw.arc(coords((cx-inner,cy-inner,cx+inner,cy+inner)),start,start+4,fill=ink,width=SCALE)
    for i in range(8):
        a=math.pi*i/4-math.pi/2
        length=radius*(.76 if i%2==0 else .48)
        tip=(cx+math.cos(a)*length,cy+math.sin(a)*length)
        left=(cx+math.cos(a-math.pi/2)*11,cy+math.sin(a-math.pi/2)*11)
        right=(cx+math.cos(a+math.pi/2)*11,cy+math.sin(a+math.pi/2)*11)
        draw.polygon([coords(p) for p in [(cx,cy),left,tip]],fill=ink)
        draw.line([coords(p) for p in [(cx,cy),right,tip,(cx,cy)]],fill=ink,width=SCALE)
        line((cx+math.cos(a)*radius*.94,cy+math.sin(a)*radius*.94,
              cx+math.cos(a)*radius*1.1,cy+math.sin(a)*radius*1.1))
    for label,x,y in [('N',cx-9,cy-radius-38),('S',cx-8,cy+radius+14),
                      ('W',cx-radius-36,cy-12),('E',cx+radius+15,cy-12)]:
        text(x,y,label,23,35)
    if mascot:
        x,y,w,h=spec['person']
        # Remove source padding so each profession occupies its designated box.
        bounds = Image.new('L', mascot.size)
        bounds.putdata([255 if a and (max(r,g,b)-min(r,g,b)>18 or max(r,g,b)<150) else 0
                        for r,g,b,a in mascot.getdata()])
        figure=mascot.crop(bounds.getbbox() or mascot.getchannel('A').getbbox())
        ratio=min(w*SCALE/figure.width,h*SCALE/figure.height)
        figure=figure.resize((round(figure.width*ratio),round(figure.height*ratio)),Image.Resampling.LANCZOS)
        card.paste(figure,(round((x+w/2)*SCALE-figure.width/2),round((y+h)*SCALE-figure.height)),figure)
    x,y,w,size=spec['score']
    score=f'{float(total):.1f}'.rstrip('0').rstrip('.') if float(total)%1 else str(int(total))
    score_width=text(x,y,score,size,w-110,True)
    divider=x+score_width+20
    line((divider,y+12,divider,y+size*.62))
    text(divider-10,y+size*.72,'/100',43,105)
    text(x,y+size+22,'简历综合评分',59 if layout!='F' else 49,w,True)
    # Long custom directions wrap rather than becoming illegibly tiny.
    direction='目标方向 · '+field_name
    if len(direction)>22:
        text(x,y+size+110,'目标方向 ·',28,w)
        text(x,y+size+148,field_name,28,w)
    else:
        text(x,y+size+110,direction,31,w)
    def centered_text(cx, y, value, size, max_width=940, bold=False):
        """Same as text(), but horizontally centred on cx instead of left-anchored."""
        path = BOLD_FONT if bold else FONT
        font = ImageFont.truetype(path, round(size * SCALE))
        while draw.textlength(value, font=font) > max_width * SCALE and size > 12:
            size -= 1
            font = ImageFont.truetype(path, round(size * SCALE))
        width = draw.textlength(value, font=font) / SCALE
        return text(cx - width / 2, y, value, size, max_width, bold)
    line((70,1160,1010,1160))
    # AI-disclaimer line removed; the two lines above shift down a bit to fill the freed space evenly.
    text(80,1230,'你的简历，还有哪些可能？',55,710,True)
    text(80,1312,'resume-compass.streamlit.app',28,725)
    # Static QR asset (not colour-matched to the card theme like the old generated one).
    qr_image=Image.open(QR_CODE_PATH).convert('RGB')
    qr_side_design=205  # enlarged from the previous 170-unit generated QR
    side=qr_side_design*SCALE
    qr_image=qr_image.resize((side,side),Image.Resampling.NEAREST)
    qr_x=800
    qr_y=1175
    card.paste(qr_image,coords((qr_x,qr_y)))
    centered_text(qr_x+qr_side_design/2,qr_y+qr_side_design+18,'扫码评估简历',23,qr_side_design)
    output=io.BytesIO();card.save(output,format='PNG')
    return output.getvalue()


_share_component = components.declare_component(
    "resume_share", path=str(Path(__file__).parent / "share_component")
)


def render_share_button(png, color, key):
    """color: 按钮主色（十六进制），跟随当前方向插画的主色调，由调用方传入。"""
    return _share_component(png=base64.b64encode(png).decode('ascii'),
                            color=color, key=key, default=False)


def render_share_preview(png):
    """Serve original PNG bytes; Streamlit st.image(width=...) downsamples them.

    卡片不再套外层的方框/展开器，直接给图片本身加圆角和阴影，浮在页面背景上；
    margin-bottom 留出和下面按钮之间的呼吸空间。
    """
    encoded = base64.b64encode(png).decode('ascii')
    st.markdown(
        '<div style="width:100%;display:flex;justify-content:center;margin-bottom:28px">'
        '<img alt="简历评估分享卡" src="data:image/png;base64,' + encoded + '" '
        'style="display:block;width:100%;max-width:min(720px,65vh);height:auto;'
        'border-radius:14px;box-shadow:0 6px 20px rgba(0,0,0,0.08);'
        'object-fit:contain" /></div>', unsafe_allow_html=True)
