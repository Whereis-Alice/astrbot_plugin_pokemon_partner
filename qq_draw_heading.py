"""Compact QQ draw titles with readable, small secondary annotations.

QQ Markdown cannot assign an inline font size or color. This separate image
styles only the result heading; the collectible card bytes stay untouched.
"""
from __future__ import annotations

import io
import re

from PIL import Image, ImageDraw

try:
    from .card_renderer import load_font
except ImportError:
    from card_renderer import load_font


def render_draw_heading(text: str) -> bytes | None:
    rows = []
    for line in text.splitlines():
        match = re.match(r'^(?:.+?的今日宝可梦是\s+|(\d+\. ))(.+?)｜(SSR|SR|R|C)·(.+)$', line)
        if match:
            prefix, name, rarity, description = match.groups()
            _, bracket, tags = description.partition('（')
            rows.append((prefix or '', name, rarity, bracket+tags if bracket else ''))
    if not rows:
        return None
    width, row_height = 1280, 98
    image = Image.new('RGBA', (width, row_height*len(rows)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for index, (prefix, name, rarity, suffix) in enumerate(rows):
        top = index*row_height
        draw.rounded_rectangle((0, top+3, width-1, top+row_height-4), radius=18,
                               fill=(246, 247, 248), outline=(218, 223, 229), width=2)
        main = prefix+name+'｜'+rarity
        size = 52
        while size >= 28:
            primary, secondary = load_font(size), load_font(round(size*.60))
            main_width = draw.textlength(main, font=primary)
            secondary_width = draw.textlength(suffix, font=secondary)
            if main_width+secondary_width+106 <= width:
                break
            size -= 2
        if main_width+secondary_width+106 > width:
            while name and draw.textlength(prefix+name+'…｜'+rarity, font=primary)+secondary_width+106 > width:
                name = name[:-1]
            main = prefix+name+'…｜'+rarity
            main_width = draw.textlength(main, font=primary)
        # A small diamond keeps the original heading accent without relying
        # on a font containing emoji glyphs.
        accent = (191, 139, 66) if '闪光' in suffix else (110, 124, 140)
        center = top+row_height//2
        draw.polygon(((28, center-10), (38, center), (28, center+10), (18, center)), fill=accent)
        x = 55
        bbox = draw.textbbox((0, 0), main, font=primary, stroke_width=1)
        y = center-(bbox[3]-bbox[1])/2-bbox[1]
        draw.text((x, y), main, font=primary, fill=(35, 43, 54), stroke_width=1)
        bbox = draw.textbbox((0, 0), suffix, font=secondary)
        y = center-(bbox[3]-bbox[1])/2-bbox[1]
        draw.text((x+main_width+8, y), suffix, font=secondary, fill=(103, 113, 126))
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
