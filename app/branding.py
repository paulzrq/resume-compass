"""Original academy logo, displayed within its visible bounds without changing the asset."""
import base64
from functools import lru_cache
from pathlib import Path
from PIL import Image

LOGO_PATH = Path(__file__).resolve().parent / "assets" / "grace_harbor_logo.png"

@lru_cache(maxsize=1)
def logo_geometry():
    with Image.open(LOGO_PATH) as image:
        return image.size, image.getchannel("A").getbbox()

@lru_cache(maxsize=1)
def logo_svg_data_uri():
    (width, height), (left, top, right, bottom) = logo_geometry()
    png = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{left} {top} {right-left} {bottom-top}">'
           f'<image width="{width}" height="{height}" href="data:image/png;base64,{png}"/></svg>')
    return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode("ascii")

def draw_report_logo(canvas, x, y, width=112):
    """Draw in existing top margin; y is the bottom of the visible logo."""
    (source_w, source_h), (left, top, right, bottom) = logo_geometry()
    scale = width / (right-left)
    height = (bottom-top) * scale
    canvas.saveState()
    clip = canvas.beginPath()
    clip.rect(x, y, width, height)
    canvas.clipPath(clip, stroke=0)
    canvas.drawImage(str(LOGO_PATH), x-left*scale, y-(source_h-bottom)*scale,
                     width=source_w*scale, height=source_h*scale, mask="auto")
    canvas.restoreState()
