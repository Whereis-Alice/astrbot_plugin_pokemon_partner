"""Printed arena lighting for battle posters and long-form match reports.

Background strips are cached and tiled to keep hundred-turn reports bounded.
All decoration stays behind the existing text and battle snapshot.
"""
from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter


def _mix(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


@lru_cache(maxsize=2)
def _stock(width: int):
    rng = random.Random(3041)
    image = Image.new("RGB", (width, 256))
    values = []
    for y in range(256):
        for x in range(width):
            grain = rng.choice((-1, 0, 0, 0, 1))
            edge = abs(x / max(1, width - 1) - .5) * 2
            base = _mix((15, 28, 39), (10, 20, 31), edge * .65)
            weave = 1 if (x + y * 2) % 13 == 0 else 0
            values.append(tuple(c + grain + weave for c in base))
    image.putdata(values)
    return image


def _wash(width, height, colors):
    sw, sh = 180, max(1, round(180 * height / width))
    im = Image.new("RGB", (sw, sh))
    values = []
    for y in range(sh):
        v = y / max(1, sh - 1)
        for x in range(sw):
            u = x / max(1, sw - 1)
            color = (10, 23, 35)
            for cx, cy, tint, strength in colors:
                power = math.exp(-((u-cx) ** 2 / .20 + (v-cy) ** 2 / .55))
                color = _mix(color, tint, power * strength)
            values.append(color)
    im.putdata(values)
    return im.resize((width, height), Image.Resampling.BICUBIC)


def _paste_wash(image, wash, top, *, fade_in=0, fade_out=100):
    """Blend lighting into the paper so scene edges never form a hard panel."""
    mask = Image.new("L", (1, wash.height))
    mask.putdata([
        round(255 * min(1, (y+1)/max(1, fade_in))
              * min(1, (wash.height-y)/max(1, fade_out)))
        for y in range(wash.height)
    ])
    image.paste(wash, (0, top), mask.resize(wash.size))


def report_stock(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    tile = _stock(width)
    for y in range(0, height, tile.height):
        image.paste(tile, (0, y))
    wash = _wash(width, 234, ((.03, .1, (38, 92, 102), .42),
                              (.96, -.3, (40, 50, 103), .40)))
    _paste_wash(image, wash, 0, fade_out=108)
    draw = ImageDraw.Draw(image)
    draw.line((26, 34, 26, height-28), fill=(37, 56, 68), width=1)
    draw.line((width-27, 34, width-27, height-28), fill=(37, 56, 68), width=1)
    for y, sign in ((25, 1), (height-26, -1)):
        draw.line((27, y+sign*34, 27, y, 62, y), fill=(93, 133, 139), width=2)
        draw.line((width-28, y+sign*34, width-28, y, width-63, y), fill=(93, 133, 139), width=2)
    return image


def arena_stock(width: int, height: int, accents) -> Image.Image:
    image = report_stock(width, height)
    left, right = accents[0][2], accents[1][2]
    arena = _wash(width, 492, ((.10, .36, left, .33), (.88, .4, right, .30),
                                (.53, .85, (119, 165, 180), .22)))
    _paste_wash(image, arena, 211, fade_in=56, fade_out=180)
    overlay = Image.new("RGBA", image.size)
    draw = ImageDraw.Draw(overlay)
    # Diffuse stadium lights stay above the trainer information.
    draw.polygon(((85, 225), (184, 225), (702, 631), (448, 631)), fill=(*left, 13))
    draw.polygon(((1002, 225), (1110, 225), (760, 631), (482, 631)), fill=(*right, 14))
    overlay = overlay.filter(ImageFilter.GaussianBlur(20))
    draw = ImageDraw.Draw(overlay)
    for inset in (0, 24, 50):
        box = (-100-inset*2, 342-inset, width+100+inset*2, 667+inset)
        draw.arc(box, 180, 358, fill=(151, 186, 205, 36-inset//3), width=2)
    # Distant seating lights and a perspective Poké Ball court.
    rng = random.Random(8721)
    for _ in range(170):
        x = rng.randint(35, width-35)
        y = rng.randint(262, 386)
        radius = rng.choice((1, 1, 2))
        draw.ellipse((x-radius, y-radius, x+radius, y+radius),
                     fill=(180, 211, 225, rng.randint(16, 65)))
    for box, alpha in (((78, 528, width-78, 685), 55), ((139, 541, width-139, 673), 31)):
        draw.ellipse(box, outline=(177, 209, 219, alpha), width=2)
    draw.line((84, 607, width-84, 607), fill=(177, 209, 219, 38), width=2)
    draw.ellipse((477, 588, 723, 626), fill=(11, 24, 36, 85), outline=(177, 209, 219, 45), width=2)
    image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
    return image


def section_rule(draw, x, y, width, color, *, weight=2):
    draw.line((x, y, x+width, y), fill=_mix((42, 61, 75), color, .15), width=1)
    draw.line((x, y, x+min(94, width*.22), y), fill=color, width=weight)
    draw.polygon(((x+width-8, y-3), (x+width-2, y), (x+width-8, y+3)), fill=_mix(color,(55,72,88),.35))


def hp_track(draw, box, ratio, color):
    left, top, right, bottom = box
    draw.polygon(((left, top), (right, top), (right-5, bottom), (left-5, bottom)), fill=(36, 50, 61))
    filled = max(0, min(1, ratio)) * (right-left)
    if filled:
        end = left + filled
        draw.polygon(((left, top), (end, top), (end-5, bottom), (left-5, bottom)), fill=color)
        draw.line((left+1, top+1, end-1, top+1), fill=_mix(color, (244,252,245), .47), width=2)
    for index in range(1, 12):
        x = left+(right-left)*index/12
        draw.line((x, top, x-5, bottom), fill=(15,28,39), width=2)
