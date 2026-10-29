"""Original card stocks inspired by Yu-Gi-Oh's print and foil vocabulary.

Stock, picture window and reading paper are independent compositions. Materials
are generated deterministically at a small sampling resolution; engraved lines
are drawn at output resolution. Public callers receive copies of cached images.
No scanned card, network request, Pokémon portrait, text or state is used here.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw

try:
    from .tcg_symbols import TYPE_COLORS
except ImportError:
    from tcg_symbols import TYPE_COLORS


KINDS = ("C", "R", "SR", "SSR", "SHINY")
READING_INKS = {
    "C": (48, 35, 24), "R": (52, 32, 26), "SR": (23, 41, 63),
    "SSR": (246, 227, 181), "SHINY": (45, 40, 66),
}
READING_MUTED = {
    "C": (77, 56, 36), "R": (84, 50, 37), "SR": (41, 59, 77),
    "SSR": (224, 200, 150), "SHINY": (71, 57, 86),
}
TITLE_INKS = {
    "C": (40, 28, 19), "R": (239, 235, 228), "SR": (235, 242, 246),
    "SSR": (255, 227, 151), "SHINY": (43, 37, 62),
}
_SEEDS = {"C": 17003, "R": 24029, "SR": 39113, "SSR": 57119, "SHINY": 79133}
_ALIASES = dict(zip(
    ("grass", "fire", "water", "electric", "psychic", "fighting", "dark",
     "steel", "dragon", "normal", "ice", "ground", "flying", "poison",
     "rock", "bug", "ghost", "fairy"),
    ("草", "火", "水", "电", "超能力", "格斗", "恶", "钢", "龙", "一般",
     "冰", "地面", "飞行", "毒", "岩石", "虫", "幽灵", "妖精"),
))


def _mix(a, b, amount):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _rgb(values):
    return tuple(max(0, min(255, round(value))) for value in values)


def _spectrum(phase):
    return tuple(round(163 + 90 * math.cos(math.tau * (phase - shift)))
                 for shift in (0, 1 / 3, 2 / 3))


def _normalise(kind, width, height, type_name="一般"):
    kind = str(kind).upper()
    if kind not in KINDS:
        kind = "C"
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise ValueError("Yu-Gi-Oh material dimensions must be positive")
    type_name = _ALIASES.get(str(type_name).lower(), str(type_name))
    if type_name not in TYPE_COLORS:
        type_name = "一般"
    return kind, width, height, type_name


def _seed(kind, layer, type_name):
    return _SEEDS[kind] + sum((i + 7) * ord(c) for i, c in enumerate(layer + type_name))


def _field(kind, layer, width, height, type_name):
    # At most 33k pixels are sampled, including full 1080x1800 stocks.
    scale = min(1.0, 174 / width, 224 / height)
    sw, sh = max(1, round(width * scale)), max(1, round(height * scale))
    rng = random.Random(_seed(kind, layer, type_name))
    tc = TYPE_COLORS[type_name]
    values = []
    for y in range(sh):
        v = y / max(1, sh - 1)
        for x in range(sw):
            u = x / max(1, sw - 1)
            noise = rng.uniform(-1, 1)
            centre = math.exp(-((u - .46) ** 2 / .14 + (v - .38) ** 2 / .22))
            edge = min(1, ((u - .5) ** 2 + (v - .5) ** 2) * 2)
            wave = .5 + .5 * math.sin(u * 9 + v * 6 + math.sin(v * 11) * .6)
            if layer == "reading":
                if kind == "C":
                    colour = _mix((224, 203, 156), (204, 177, 122), edge * .45)
                    colour = _mix(colour, (242, 226, 191), centre * .20)
                    grain = noise * 1.7 + math.sin(x * .4 + y * .2) * .4
                elif kind == "R":
                    colour = _mix((226, 201, 168), (195, 155, 113), edge * .23)
                    colour = _mix(colour, (244, 217, 189), centre * .13)
                    grain = noise * 1.3 + math.sin(y * 2.4) * .5
                elif kind == "SR":
                    colour = _mix((190, 211, 218), (167, 191, 207), edge * .32)
                    colour = _mix(colour, (217, 231, 228), wave * .12)
                    grain = noise * .9
                elif kind == "SSR":
                    colour = _mix((29, 28, 26), (45, 37, 26), wave * .36)
                    colour = _mix(colour, (61, 49, 31), centre * .13)
                    grain = noise * .8
                else:
                    colour = _mix((217, 219, 226), (224, 216, 229), wave * .35)
                    colour = _mix(colour, _spectrum(u * .73 - v * .41), .045)
                    colour = _mix(colour, (232, 240, 233), centre * .17)
                    grain = noise * .7
            elif layer == "stock":
                if kind == "C":
                    colour = _mix((189, 143, 74), (224, 190, 127), centre * .70)
                    colour = _mix(colour, (122, 79, 33), edge * .28)
                    colour = _mix(colour, (226, 197, 143), wave * .07)
                    grain = noise * 3.5
                elif kind == "R":
                    colour = _mix((130, 66, 39), (205, 134, 77), centre * .80)
                    band = math.exp(-((u - .27 + v * .12) / .10) ** 2)
                    colour = _mix(colour, (247, 193, 135), band * .36)
                    colour = _mix(colour, (76, 35, 27), edge * .31)
                    grain = noise * 2.3 + math.sin(y * 5.1) * 1.4
                elif kind == "SR":
                    colour = _mix((28, 52, 94), (74, 122, 166), centre * .72)
                    colour = _mix(colour, (105, 157, 182), wave * .12)
                    colour = _mix(colour, (18, 35, 64), edge * .28)
                    grain = noise * 1.5
                elif kind == "SSR":
                    colour = _mix((16, 18, 24), (50, 38, 23), wave * .25 + centre * .15)
                    beam = math.exp(-((u - .19 + v * .12) / .04) ** 2)
                    colour = _mix(colour, (205, 156, 60), beam * .26)
                    grain = noise * 1.8
                else:
                    colour = _mix((188, 190, 207), (227, 231, 233), centre * .82)
                    band = math.exp(-((u - .78 + v * .29) / .08) ** 2)
                    colour = _mix(colour, _spectrum(u * 2.2 - v * 1.4), .09 + band * .29)
                    colour = _mix(colour, (170, 166, 191), edge * .13)
                    grain = noise * 1.2
            else:
                if kind == "C":
                    colour = _mix((213, 186, 139), (100, 119, 92), v * .78)
                    colour = _mix(colour, tc, .05 + centre * .08)
                    colour = _mix(colour, (242, 218, 158), centre * .29)
                    grain = noise * 2.4
                elif kind == "R":
                    colour = _mix((59, 36, 35), (67, 106, 109), centre * .79)
                    colour = _mix(colour, (160, 94, 48), (1 - u) ** 3 * .46)
                    colour = _mix(colour, tc, centre * .07)
                    grain = noise * 1.7
                elif kind == "SR":
                    colour = _mix((12, 29, 67), (77, 131, 174), centre * .84)
                    colour = _mix(colour, tc, centre * .07)
                    ridge = math.exp(-((u - .78 + v * .24) / .04) ** 2)
                    colour = _mix(colour, (168, 217, 231), ridge * .32)
                    grain = noise * 1.3
                elif kind == "SSR":
                    colour = _mix((9, 14, 24), (57, 49, 36), centre * .71)
                    colour = _mix(colour, tc, centre * .045)
                    beam = math.exp(-((u - .76 + v * .35) / .045) ** 2)
                    colour = _mix(colour, (196, 153, 71), beam * .26)
                    grain = noise * 1.7
                else:
                    colour = _mix((175, 186, 208), (221, 232, 232), centre * .60)
                    phase = u * 1.25 - v * .83 + .10 * math.sin(v * 8)
                    band = math.exp(-((u - .85 + v * .37) / .14) ** 2)
                    colour = _mix(colour, _spectrum(phase), .10 + band * .26)
                    colour = _mix(colour, (252, 243, 222), centre * .18)
                    grain = noise * .8
            values.append((*_rgb(c + grain for c in colour), 255))
    im = Image.new("RGBA", (sw, sh))
    im.putdata(values)
    return im.resize((width, height), Image.Resampling.BICUBIC)


def _poly(draw, points, width, height, fill):
    draw.polygon([(round(x * width), round(y * height)) for x, y in points], fill=fill)


def _spark(draw, x, y, radius, fill, *, diamond=False):
    if diamond:
        draw.polygon(((x, y - radius), (x + radius * .56, y),
                      (x, y + radius), (x - radius * .56, y)), fill=fill)
    else:
        draw.line((x - radius, y, x + radius, y), fill=fill, width=1)
        draw.line((x, y - radius, x, y + radius), fill=fill, width=1)
        draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=fill)


def _engraving(base, kind, layer, type_name):
    width, height = base.size
    s = width / 900
    rng = random.Random(_seed(kind, layer, type_name) + 919)
    overlay = Image.new("RGBA", base.size)
    d = ImageDraw.Draw(overlay)
    reading = layer == "reading"
    if kind == "C":
        # Irregular short fibres never resemble a uniform dot/noise filter.
        for _ in range(190 if reading else 450):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            length = rng.uniform(3, 21) * max(.5, s)
            d.line((x, y, x + length, y + rng.uniform(-1.5, 1.5) * s),
                   fill=(101, 69, 27, 12 if reading else 18), width=1)
        if layer == "art":
            # Layered old-illustration hills leave the centre clear for artwork.
            for i, colour in enumerate(((91, 107, 78, 74), (66, 88, 68, 87), (53, 71, 57, 106))):
                points = [(0, 1), (0, .73 + i * .08)]
                points += [(j / 30, .77 + i * .07 + .055 * math.sin(j / 30 * 8 + i)) for j in range(31)]
                points.append((1, 1))
                _poly(d, points, width, height, colour)
            for _ in range(23):
                x = rng.choice((rng.uniform(.02, .19), rng.uniform(.81, .98))) * width
                y = rng.uniform(.72, .98) * height
                h = rng.uniform(10, 38) * s
                d.line((x, y, x - h * .25, y - h), fill=(202, 193, 130, 82), width=max(1, round(s)))
    elif kind == "R":
        # Straight rolled-metal strands, with a warm satin highlight.
        for y in range(0, height, max(3, round(7 * s))):
            alpha = rng.randint(5, 11) if reading else rng.randint(9, 25)
            d.line((0, y, width, y + rng.uniform(-1, 1) * s), fill=(252, 213, 171, alpha), width=1)
        if layer == "art":
            for i in range(5):
                inset = (55 + i * 21) * s
                # Partial cold-silver circles retain an unfoiled dark picture area.
                d.arc((inset, height * .12 + i * 12 * s, width - inset, height * .92 - i * 12 * s),
                      211, 339, fill=(190, 207, 205, 37 - i * 4), width=max(1, round(s)))
            for _ in range(42):
                x, y = rng.uniform(0, width), rng.uniform(0, height)
                if .22 * width < x < .78 * width:
                    continue
                d.ellipse((x, y, x + 1.5 * s, y + 1.5 * s), fill=(214, 155, 90, 80))
    elif kind == "SR":
        # Frosted ceremonial circles are structural, not floating spell text.
        cx, cy = width * .50, height * (.49 if layer == "art" else .34)
        for radius in ((.20, .25, .38, .41) if layer == "art" else (.55, .61)):
            r = width * radius
            d.ellipse((cx - r, cy - r, cx + r, cy + r),
                      outline=(169, 219, 232, 8 if reading else 37), width=max(1, round(s)))
        if not reading:
            for i in range(48):
                a = i * math.tau / 48
                r1, r2 = width * .392, width * (.405 if i % 4 else .425)
                d.line((cx + math.cos(a) * r1, cy + math.sin(a) * r1,
                        cx + math.cos(a) * r2, cy + math.sin(a) * r2),
                       fill=(180, 220, 233, 66), width=max(1, round(s)))
            for side in (0, 1):
                _poly(d, ((side, .05), (.14 if side == 0 else .86, .28),
                          (side, .83)), width, height, (156, 203, 235, 14))
                _poly(d, ((side, .90), (.10 if side == 0 else .90, .54),
                          (side, .23)), width, height, (215, 234, 241, 12))
    elif kind == "SSR":
        # Celestial engraving and random gold flecks occupy the open margins.
        for _ in range(44 if reading else 240):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            r = rng.uniform(.3, 1.3) * max(.6, s)
            d.ellipse((x, y, x + r, y + r), fill=(227, 184, 95, 9 if reading else rng.randint(30, 97)))
        if not reading:
            cx, cy = width * .48, height * .45
            for r in (width * .34, width * .43, width * .46):
                d.ellipse((cx - r, cy - r * .70, cx + r, cy + r * .70),
                          outline=(203, 153, 56, 43), width=max(1, round(s)))
            for i in range(18):
                a = i * math.tau / 18 - .12
                r = width * .435
                x, y = cx + math.cos(a) * r, cy + math.sin(a) * r * .70
                _spark(d, x, y, (4 if i % 3 else 7) * s, (233, 192, 98, 142), diamond=i % 3 == 0)
            points = [(width * x, height * y) for x, y in ((.08, .19), (.19, .11), (.31, .18), (.17, .28))]
            d.line(points, fill=(220, 174, 79, 66), width=max(1, round(s)))
            for x, y in points:
                _spark(d, x, y, 4 * s, (248, 219, 151, 174))
    else:
        # Orthogonal prismatic micro-grid differs from diagonal rainbow washes.
        pitch = max(8, round((21 if layer == "art" else 17) * s))
        for x in range(0, width, pitch):
            alpha = 5 if reading else (23 if layer == "art" else 16)
            d.line((x, 0, x, height), fill=(252, 251, 250, alpha), width=1)
            d.line((x + 2, 0, x + 2, height), fill=(77, 81, 131, alpha // 2), width=1)
        for y in range(0, height, pitch):
            alpha = 5 if reading else (22 if layer == "art" else 15)
            d.line((0, y, width, y), fill=(246, 252, 253, alpha), width=1)
            d.line((0, y + 2, width, y + 2), fill=(126, 89, 151, alpha // 2), width=1)
        if not reading:
            for _ in range(48 if layer == "art" else 76):
                x, y = rng.uniform(0, width), rng.uniform(0, height)
                # Sparkles accumulate near edges; the portrait silhouette remains clear.
                if layer == "art" and .27 * width < x < .73 * width and .20 * height < y < .83 * height:
                    continue
                _spark(d, x, y, rng.uniform(2, 6) * s, (252, 251, 255, rng.randint(95, 188)), diamond=True)
            if layer == "art":
                _poly(d, ((.00, .42), (.22, .08), (.53, .00), (.26, .40)), width, height, (255, 251, 249, 26))
                _poly(d, ((1, .29), (.79, .76), (.45, 1), (.90, .93)), width, height, (253, 231, 255, 25))
                _poly(d, ((0, .83), (.18, .53), (.37, .94), (.16, 1)), width, height, (202, 243, 247, 24))
    return Image.alpha_composite(base, overlay)


@lru_cache(maxsize=15)
def _material(kind, layer, width, height, type_name):
    return _engraving(_field(kind, layer, width, height, type_name), kind, layer, type_name)


def render_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return the card's tinted paper/metal stock as an opaque RGBA copy."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "stock", width, height, type_name).copy()


def render_art_scene(kind: str, type_name: str, width: int, height: int) -> Image.Image:
    """Return a separate picture-window scene with its own rarity composition."""
    kind, width, height, type_name = _normalise(kind, width, height, type_name)
    return _material(kind, "art", width, height, type_name).copy()


def render_reading_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return low-amplitude, stock-matched reading paper; no pure-white panel."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "reading", width, height, type_name).copy()
