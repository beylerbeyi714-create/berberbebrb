"""Seri bölüm kartları (giriş / kapanış). Yerel üretim, ücretsiz, sistem sahibine ait (own_work).

Her seri, adından türetilen sabit bir renk temasına sahiptir; izleyici bölümlerin aynı
seriye ait olduğunu ilk karede tanır.
"""
from __future__ import annotations

import colorsys
import hashlib
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def series_palette(series_title: str) -> tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    hue = int(hashlib.sha256(series_title.encode()).hexdigest()[:4], 16) / 0xFFFF
    def rgb(h, l, s):
        return tuple(int(c * 255) for c in colorsys.hls_to_rgb(h % 1, l, s))
    return rgb(hue, 0.30, 0.65), rgb(hue + 0.08, 0.12, 0.55), rgb(hue + 0.5, 0.62, 0.85)  # üst, alt, vurgu


def _gradient(top, bottom) -> Image.Image:
    img = Image.new("RGB", (W, H))
    col = Image.new("RGB", (1, H))
    for y in range(H):
        t = y / (H - 1)
        col.putpixel((0, y), tuple(int(top[i] * (1 - t) + bottom[i] * t) for i in range(3)))
    return col.resize((W, H))


def _center(d: ImageDraw.ImageDraw, y: int, text: str, font, fill) -> int:
    w = d.textlength(text, font=font)
    d.text(((W - w) / 2, y), text, font=font, fill=fill)
    return y + int(font.size * 1.25)


def render_card(series_title: str, episode: int, kind: str, line: str, out_path: Path) -> Path:
    """kind: 'intro' (bölüm başı) | 'outro' (devamı) | 'finale' (seri sonu)."""
    top, bottom, accent = series_palette(series_title)
    if kind != "intro":           # kapanış kartı farklı görünsün (tekrar eden sahne sayılmasın)
        top, bottom = bottom, top
    img = _gradient(top, bottom)
    d = ImageDraw.Draw(img)
    f_series = ImageFont.truetype(BOLD, 64)
    f_ep = ImageFont.truetype(BOLD, 150)
    f_line = ImageFont.truetype(REGULAR, 56)

    y = 520
    for ln in textwrap.wrap(series_title.upper(), 20)[:3]:
        y = _center(d, y, ln, f_series, (255, 255, 255))
    y += 60
    if kind == "intro":
        badge = f"BÖLÜM {episode}"
    elif kind == "outro":
        badge = f"BÖLÜM {episode + 1} ▶"
    else:
        badge = "SON"
    bw = d.textlength(badge, font=f_ep)
    d.rounded_rectangle(((W - bw) / 2 - 50, y - 20, (W + bw) / 2 + 50, y + 190), radius=40, fill=accent)
    d.text(((W - bw) / 2, y), badge, font=f_ep, fill=(20, 20, 30))
    y += 300
    for ln in textwrap.wrap(line, 28)[:4]:
        y = _center(d, y, ln, f_line, (240, 240, 245))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
