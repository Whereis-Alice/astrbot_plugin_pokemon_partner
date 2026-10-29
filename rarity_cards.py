"""Five independently composed daily Pokémon editions; no network or state access."""

from __future__ import annotations

import io
import math
import random
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

try:
    from .card_renderer import load_font
    from .gacha import DrawItem, rarity_label
    from .rarity_emblems import render_emblem
    from .rarity_scenes import render_scene
    from .tcg_symbols import render_energy_icon
    from .card_matchups import matchup_groups
except ImportError:
    from card_renderer import load_font
    from gacha import DrawItem, rarity_label
    from rarity_emblems import render_emblem
    from rarity_scenes import render_scene
    from tcg_symbols import render_energy_icon
    from card_matchups import matchup_groups

WIDTH = 1080
CARD_HEIGHT = 1800
MARGIN = 48
STATS = ("HP", "攻击", "防御", "特攻", "特防", "速度")
STAT_COLORS = (
    (239, 93, 105),
    (247, 158, 76),
    (239, 197, 62),
    (91, 162, 238),
    (67, 190, 148),
    (177, 119, 235),
)


@dataclass(frozen=True)
class Palette:
    bg: tuple
    panel: tuple
    ink: tuple
    muted: tuple
    accent: tuple
    line: tuple
    track: tuple


PALETTES = {
    "C": Palette(
        (51, 77, 49),
        (191, 214, 179),
        (26, 46, 35),
        (36, 58, 43),
        (166, 180, 119),
        (109, 147, 108),
        (147, 180, 136),
    ),
    "R": Palette(
        (7, 37, 68),
        (10, 44, 70),
        (239, 250, 255),
        (177, 219, 231),
        (116, 225, 255),
        (64, 126, 167),
        (41, 88, 113),
    ),
    "SR": Palette(
        (29, 12, 52),
        (39, 19, 65),
        (250, 241, 255),
        (218, 192, 233),
        (248, 167, 226),
        (142, 85, 157),
        (82, 52, 106),
    ),
    "SSR": Palette(
        (16, 20, 24),
        (27, 23, 18),
        (255, 248, 225),
        (218, 197, 155),
        (246, 205, 114),
        (155, 125, 62),
        (73, 67, 50),
    ),
    "SHINY": Palette(
        (221, 230, 245),
        (224, 230, 247),
        (46, 37, 79),
        (60, 45, 87),
        (186, 141, 245),
        (150, 139, 195),
        (191, 181, 221),
    ),
}

PANEL_ALPHA = {
    PALETTES[kind].panel: alpha
    for kind, alpha in (("C", 230), ("R", 229), ("SR", 231), ("SSR", 236), ("SHINY", 234))
}


def edition(item: DrawItem) -> str:
    return "SHINY" if item.shiny else item.rarity if item.rarity in PALETTES else "C"


@lru_cache(maxsize=48)
def _font(size: int):
    return load_font(size)


def _fit(draw, text, width, size=64, minimum=42):
    for value in range(size, minimum - 1, -2):
        if draw.textlength(str(text), font=_font(value)) <= width:
            return value
    return minimum


def _wrap(draw, text, size, width, maximum=None):
    text = str(text or "")
    font = _font(size)
    lines, current = [], ""
    tokens = re.findall(
        r"[一-龥]{1,6}×\d+(?:\.\d+)?|[♂♀]\d+(?:\.\d+)?%|[A-Za-z0-9.%_+/-]+|[^\n]|\n",
        text,
    )
    for token in tokens:
        if token == "\n":
            lines.append(current)
            current = ""
            continue
        chunks = list(token) if draw.textlength(token, font=font) > width else [token]
        for chunk in chunks:
            if current and draw.textlength(current + chunk, font=font) > width:
                if chunk in "，。；：、！？）】":
                    # Move a complete type multiplier or percentage with punctuation.
                    tail_match = re.search(
                        r"(?:[一-龥]{1,6}×\d+(?:\.\d+)?|[♂♀]\d+(?:\.\d+)?%|[A-Za-z0-9.%_+/-]+|.)$",
                        current,
                    )
                    tail = tail_match.group() if tail_match else current[-1]
                    head = current[: -len(tail)]
                    if head:
                        lines.append(head)
                        current = tail + chunk
                    else:
                        lines.append(current)
                        current = chunk
                else:
                    lines.append(current)
                    current = chunk
            else:
                current += chunk
    if current or not lines:
        lines.append(current)
    if maximum and len(lines) > maximum:
        lines = lines[:maximum]
        tail = lines[-1]
        while tail and draw.textlength(tail + "…", font=font) > width:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    return tuple(lines)


def _text(draw, x, y, text, size, color, *, width=None, maximum=None, center=False):
    lines = _wrap(draw, text, size, width or WIDTH - x - MARGIN, maximum)
    step = size + 12
    for index, line in enumerate(lines):
        tx = (
            x + ((width or 0) - draw.textlength(line, font=_font(size))) / 2
            if center
            else x
        )
        draw.text(
            (round(tx), y + index * step),
            line,
            font=_font(size),
            fill=color,
            anchor="lt",
        )
    return y + len(lines) * step


PANEL_STOPS = {
    "C": ((205, 225, 192), (191, 214, 179), (177, 203, 167)),
    "R": ((14, 61, 82), (10, 44, 70), (8, 31, 54)),
    "SR": ((60, 28, 81), (39, 19, 65), (28, 16, 47)),
    "SSR": ((42, 34, 24), (27, 23, 18), (17, 21, 25)),
    "SHINY": ((237, 234, 251), (219, 233, 246), (233, 219, 247)),
}
PANEL_SEEDS = {"C": 13759, "R": 27103, "SR": 41387, "SSR": 69143, "SHINY": 97787}
PRISM_COLORS = ((128, 228, 232), (156, 145, 249), (238, 132, 212),
                (242, 185, 116), (165, 229, 159), (134, 189, 249))


@lru_cache(maxsize=24)
def _reading_material(kind, width, height):
    """Theme-specific print textures; callers copy before masking or outlining."""
    stops = PANEL_STOPS[kind]
    strip = Image.new("RGB", (1, height))
    colors = []
    for y in range(height):
        position = y / max(1, height - 1)
        first, second = (stops[0], stops[1]) if position <= .5 else (stops[1], stops[2])
        amount = position * 2 if position <= .5 else (position - .5) * 2
        colors.append(tuple(round(a + (b - a) * amount) for a, b in zip(first, second)))
    strip.putdata(colors)
    surface = strip.resize((width, height))
    draw = ImageDraw.Draw(surface, "RGBA")
    rng = random.Random(PANEL_SEEDS[kind] + width * 17 + height * 31)
    if kind == "C":
        # Sage vellum catches the forest light; barely pressed leaf veins
        # stay at the margins rather than competing with the printed text.
        for _ in range(max(50, width * height // 2200)):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.line((x, y, min(width, x + rng.randrange(5, 23)), y + 1),
                      fill=(69, 110, 73, rng.randrange(4, 10)), width=1)
        for index in range(3):
            cx, cy = width * (.84 + .08 * index), height * (.72 + .09 * index)
            radius = min(width, height) * (.16 + .025 * index)
            draw.arc((cx - radius, cy - radius * 1.8, cx + radius, cy + radius * 1.8),
                     120, 258, fill=(77, 129, 83, 16), width=2)
            for branch in range(4):
                yy = cy - radius * 1.15 + branch * radius * .62
                draw.line((cx - radius * .62, yy, cx + radius * .30, yy + radius * .32),
                          fill=(86, 143, 88, 12), width=1)
        for _ in range(max(60, width * height // 2100)):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.point((x, y), fill=(240, 249, 225, 22))
    elif kind == "R":
        # Brushed metal under a sea-blue optical lens: broad reflections and
        # a precise etched grid are distinct from paper and crystal facets.
        for y in range(8, height, 9):
            start = rng.randrange(max(1, width // 3))
            draw.line((start, y, width, y), fill=(118, 208, 222, 7), width=1)
        draw.polygon(((width * .48, 0), (width * .70, 0),
                      (width * .18, height), (0, height)), fill=(147, 226, 243, 11))
        for x in range(38, width, 61):
            draw.line((x, 0, x - height * .10, height), fill=(77, 151, 182, 9), width=1)
        draw.line((5, 5, width - 6, 5), fill=(191, 243, 252, 35), width=2)
    elif kind == "SR":
        # An amethyst cut with long mineral strata and asymmetric planes.
        for index in range(5):
            x = width * (index / 5)
            tilt = width * (.17 if index % 2 == 0 else -.12)
            draw.polygon(((x, 0), (x + width * .26, 0),
                          (x + width * .16 + tilt, height), (x + tilt, height)),
                         fill=(220, 133, 232, 8 + index % 3 * 4))
            draw.line((x, 0, x + tilt, height), fill=(170, 102, 193, 22), width=1)
        draw.polygon(((width, 0), (width * .60, height * .41),
                      (width, height * .83)), fill=(139, 118, 243, 13))
        for y in range(17, height, 37):
            draw.line((0, y, width, y - width * .04), fill=(231, 170, 245, 6), width=1)
    elif kind == "SSR":
        # Obsidian with gold leaf micrograin, orbital engraving and star maps.
        for _ in range(max(30, width * height // 3200)):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.point((x, y), fill=(211, 176, 91, 30))
        center = (width * .79, height * .84)
        for radius in (width * .22, width * .36, width * .53):
            draw.arc((center[0] - radius, center[1] - radius * .7,
                      center[0] + radius, center[1] + radius * .7),
                     182, 340, fill=(200, 155, 71, 18), width=1)
        points = [(width * .10, height * .30), (width * .20, height * .22),
                  (width * .36, height * .32), (width * .48, height * .15)]
        draw.line(points, fill=(185, 145, 64, 13), width=1)
        for x, y in points:
            draw.line((x - 3, y, x + 3, y), fill=(235, 198, 121, 35), width=1)
            draw.line((x, y - 3, x, y + 3), fill=(235, 198, 121, 35), width=1)
    else:
        # Pearl foil carries flowing interference bands and fine curved
        # embossing. Its luminous body belongs to the bright prism scene.
        reflection = Image.new("RGBA", (width, height))
        optical = ImageDraw.Draw(reflection)
        for index, color in enumerate(PRISM_COLORS):
            cx = width * (index / 5 - .12)
            cy = height * (.18 + (index % 3) * .29)
            rx, ry = width * .42, max(35, height * .30)
            optical.ellipse((cx - rx, cy - ry, cx + rx, cy + ry), fill=(*color, 35))
        reflection = reflection.filter(ImageFilter.GaussianBlur(max(12, min(width, height) / 9)))
        surface = Image.alpha_composite(surface.convert("RGBA"), reflection).convert("RGB")
        draw = ImageDraw.Draw(surface, "RGBA")
        cx, cy = width * .76, height * 1.13
        for index in range(12):
            radius = width * (.24 + index * .073)
            color = PRISM_COLORS[index % 6]
            draw.arc((cx - radius, cy - radius * .81, cx + radius, cy + radius * .81),
                     188, 320, fill=(*color, 18), width=1)
            draw.arc((cx - radius - 2, cy - radius * .81 - 2,
                      cx + radius - 2, cy + radius * .81 - 2),
                     188, 320, fill=(255, 255, 255, 46), width=1)
        for _ in range(max(35, width * height // 4200)):
            x, y = rng.randrange(width), rng.randrange(height)
            draw.point((x, y), fill=(*PRISM_COLORS[rng.randrange(6)], 28))
    return surface.convert("RGBA")


def _panel(draw, box, p, radius=22, fill=None):
    # The themed material is alpha-composited into the actual scene, so both
    # the print texture and background remain visible beneath readable glyphs.
    kind = next(name for name, palette in PALETTES.items() if palette is p)
    left, top = math.floor(box[0]), math.floor(box[1])
    width, height = math.ceil(box[2] - left) + 1, math.ceil(box[3] - top) + 1
    layer = _reading_material(kind, width, height).copy()
    if kind in {"C", "SHINY"}:
        # Lift and defocus the local scene before mixing it into the print.
        # The diffuse forest/prism colours retain context while the bounded
        # light surface keeps dark ink legible even above the darkest scene.
        local = draw._image.crop((left, top, left + width, top + height)).convert("RGB")
        local = local.resize((max(1, width // 12), max(1, height // 12)), Image.Resampling.BILINEAR)
        local = local.resize((width, height), Image.Resampling.BICUBIC)
        local = local.filter(ImageFilter.GaussianBlur(12))
        lifted = Image.blend(local, Image.new("RGB", local.size, p.panel), .68)
        layer = Image.blend(layer.convert("RGB"), lifted, .35).convert("RGBA")
    mask = Image.new("L", (width, height))
    boundary = ImageDraw.Draw(mask)
    inset = min(radius, 14)
    if kind == "SR":
        polygon = ((inset, 0), (width - inset - 1, 0), (width - 1, inset),
                   (width - 1, height - inset - 1), (width - inset - 1, height - 1),
                   (inset, height - 1), (0, height - inset - 1), (0, inset))
        boundary.polygon(polygon, fill=255)
    else:
        polygon = None
        boundary.rounded_rectangle((0, 0, width - 1, height - 1),
                                   radius=min(radius, 18), fill=255)
    layer.putalpha(mask.point(lambda value: round(value * PANEL_ALPHA[p.panel] / 255)))
    detail = ImageDraw.Draw(layer)
    if polygon:
        detail.line((*polygon, polygon[0]), fill=(*p.line, 255), width=2)
    else:
        detail.rounded_rectangle((0, 0, width - 1, height - 1),
                                 radius=min(radius, 18), outline=(*p.line, 255), width=2)
    if width > 260 and height > 100:
        if kind == "C":
            detail.line((17, 8, width - 18, 8), fill=(232, 246, 213, 185), width=1)
            detail.line((10, 19, 10, height - 20), fill=(233, 246, 218, 82), width=1)
        elif kind == "R":
            detail.line((11, 34, 11, 12, 48, 12), fill=(133, 218, 239, 200), width=2)
            detail.line((width - 48, height - 12, width - 12, height - 12, width - 12, height - 34),
                        fill=(75, 164, 205, 190), width=2)
        elif kind == "SR":
            detail.line((17, 7, width - 23, 7, width - 7, 23), fill=(219, 145, 235, 145), width=1)
        elif kind == "SSR":
            detail.line((19, 8, width - 20, 8), fill=(231, 190, 100, 170), width=1)
        else:
            for index, color in enumerate(PRISM_COLORS):
                y1, y2 = 19 + index * (height - 38) / 6, 19 + (index + 1) * (height - 38) / 6
                detail.line((5, y1, 5, y2), fill=(*color, 160), width=2)
                detail.line((width - 6, y1, width - 6, y2), fill=(*PRISM_COLORS[-index - 1], 160), width=2)
            detail.rounded_rectangle((7, 7, width - 8, height - 8), radius=12,
                                     outline=(255, 255, 255, 125), width=1)
    if draw._image.mode == "RGBA":
        draw._image.alpha_composite(layer, (left, top))
    else:
        surface = draw._image.crop((left, top, left + width, top + height)).convert("RGBA")
        surface.alpha_composite(layer)
        draw._image.paste(surface.convert(draw._image.mode), (left, top))


def _stats(item):
    match = re.search(
        r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)",
        str(item.row.get("stats") or ""),
    )
    return dict(zip(STATS, map(int, match.groups()))) if match else {}


def _stat_bars(draw, item, x, y, width, p, *, step=62):
    values = _stats(item)
    if not values:
        return _text(draw, x, y, f"种族值总和 {item.bst}", 26, p.ink, width=width)
    draw.text((x, y), "种族值", font=_font(26), fill=p.muted, anchor="lt")
    y += 44
    bar_x, bar_w = x + 78, width - 146
    for index, key in enumerate(STATS):
        yy = y + index * step
        draw.text((x, yy), key, font=_font(28), fill=p.ink, anchor="lt")
        draw.rounded_rectangle(
            (bar_x, yy + 8, bar_x + bar_w, yy + 27), radius=9, fill=p.track
        )
        ratio = min(1, max(0, values[key] / 180))
        if ratio:
            draw.rounded_rectangle(
                (bar_x, yy + 8, bar_x + max(8, bar_w * ratio), yy + 27),
                radius=9,
                fill=STAT_COLORS[index],
            )
        draw.text(
            (bar_x + bar_w + 12, yy),
            str(values[key]),
            font=_font(28),
            fill=p.ink,
            anchor="lt",
        )
    return _text(draw, x, y + 6 * step, f"总和 {item.bst}", 26, p.muted, width=width)


def _art(base, draw, item, loader, box, p):
    x, y, width, height = box
    art_row = dict(item.row, _artwork_shiny=True) if item.shiny else item.row
    image = loader(art_row) if loader else None
    if isinstance(image, bytes):
        with Image.open(io.BytesIO(image)) as opened:
            image = opened.convert("RGBA")
    if image is not None:
        image = image.convert("RGBA")
        bounds = image.getchannel("A").getbbox()
        image = image.crop(bounds) if bounds else None
    if image is not None:
        scale = min(3.0, width / image.width, height / image.height)
        image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                             Image.Resampling.LANCZOS)
        tx, ty = (
            round(x + (width - image.width) / 2),
            round(y + (height - image.height) / 2),
        )
        padding = 38
        mask = Image.new("L", (image.width + padding * 2, image.height + padding * 2))
        mask.paste(image.getchannel("A"), (padding, padding))
        shadow = Image.new("RGBA", mask.size, (7, 10, 25, 0))
        shadow.putalpha(
            mask.filter(ImageFilter.GaussianBlur(12)).point(
                lambda value: round(value * 0.42)
            )
        )
        base.alpha_composite(shadow, (tx - padding + 4, ty - padding + 14))
        if edition(item) != "C":
            glow = Image.new("RGBA", mask.size, (*p.accent, 0))
            glow.putalpha(
                mask.filter(ImageFilter.GaussianBlur(16)).point(
                    lambda value: round(value * 0.4)
                )
            )
            base.alpha_composite(glow, (tx - padding, ty - padding))
        if edition(item) in {"SSR", "SHINY"}:
            rim_color = (255, 227, 145) if edition(item) == "SSR" else (255, 253, 255)
            rim = Image.new("RGBA", mask.size, (*rim_color, 0))
            rim.putalpha(
                mask.filter(ImageFilter.MaxFilter(7)).point(
                    lambda value: round(value * 0.85)
                )
            )
            base.alpha_composite(rim, (tx - padding, ty - padding))
        base.alpha_composite(image, (tx, ty))
    else:
        cx, cy, radius = x + width / 2, y + height / 2 - 12, min(width, height) * 0.25
        draw.ellipse(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            fill=p.panel,
            outline=p.accent,
            width=3,
        )
        draw.line((cx - radius, cy, cx + radius, cy), fill=p.accent, width=3)
        draw.ellipse(
            (cx - 18, cy - 18, cx + 18, cy + 18),
            fill=p.panel,
            outline=p.accent,
            width=3,
        )
        _panel(draw, (x, cy + radius + 20, x + width, cy + radius + 62), p, radius=12)
        _text(
            draw,
            x,
            cy + radius + 26,
            "立绘暂未缓存",
            24,
            p.ink,
            width=width,
            center=True,
        )


# Layout appended after the stable copy-wrapping and artwork helpers.
BADGES = {
    "C": ((49, 82, 54), (255, 255, 245)),
    "R": ((16, 85, 133), (245, 253, 255)),
    "SR": ((106, 49, 144), (255, 248, 255)),
    "SSR": ((242, 202, 111), (44, 34, 13)),
}


def _pill(draw, x, y, label, fill, ink, *, size=24, height=40):
    width = math.ceil(draw.textlength(label, font=_font(size))) + 28
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill)
    draw.text(
        (x + 14, y + (height - size) // 2 - 1),
        label,
        font=_font(size),
        fill=ink,
        anchor="lt",
    )
    return x + width + 10


def _badges(draw, item, x, y, *, center=False, width=940):
    fill, ink = BADGES.get(item.rarity, BADGES["C"])
    entries = [(f"{item.rarity} · {rarity_label(item.rarity)}", fill, ink)]
    if item.shiny:
        entries.append(("闪光", (94, 47, 128), (255, 249, 255)))
    total = (
        sum(
            math.ceil(draw.textlength(label, font=_font(24))) + 38
            for label, _, _ in entries
        )
        - 10
    )
    if center:
        x += (width - total) / 2
    for label, color, text_color in entries:
        x = _pill(draw, x, y, label, color, text_color)


def _type_layout(draw, item, x, y, width, *, center=False):
    rows = [[]]
    used = 0
    for label in filter(None, item.types.split("/")):
        size = math.ceil(draw.textlength(label, font=_font(26))) + 72
        if used and used + size > width:
            rows.append([])
            used = 0
        rows[-1].append((label, size))
        used += size + 10
    positions = []
    for index, row in enumerate(rows):
        length = sum(size + 10 for _, size in row) - 10
        cursor = x + (width - length) / 2 if center else x
        for label, size in row:
            positions.append((label, cursor, y + index * 58))
            cursor += size + 10
    return positions, y + 58 * len(rows) if positions else y


def _types(base, draw, positions, p):
    for label, x, y in positions:
        base.alpha_composite(render_energy_icon(label, 42), (round(x), round(y)))
        draw.text((round(x + 51), round(y + 8)), label,
                  font=_font(26), fill=p.ink, anchor="lt")


def _finish_image(base):
    mask = Image.new("L", base.size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (16, 16, WIDTH - 16, base.height - 16), radius=30, fill=255
    )
    framed = Image.new("RGB", base.size, (238, 237, 231))
    framed.paste(base, (0, 0), mask)
    ImageDraw.Draw(framed).rounded_rectangle(
        (16, 16, WIDTH - 16, base.height - 16),
        radius=30,
        outline=(188, 197, 187),
        width=1,
    )
    return framed


def _encode(image):
    stream = io.BytesIO()
    image.save(stream, "JPEG", quality=95, optimize=True)
    return stream.getvalue()


@dataclass(frozen=True)
class Hero:
    name_x: int
    name_y: int
    name_width: int
    name_size: int
    art: tuple
    emblem: tuple
    left_width: int
    profile_cap: int


HEROES = {
    "C": Hero(566, 148, 430, 66, (56, 100, 482, 660), (40, 134, 520), 482, 13),
    "R": Hero(566, 148, 430, 68, (52, 98, 488, 666), (34, 128, 530), 482, 13),
    "SR": Hero(566, 148, 430, 70, (50, 96, 490, 676), (30, 122, 540), 482, 13),
    "SSR": Hero(566, 148, 430, 72, (44, 94, 496, 686), (24, 116, 550), 500, 13),
    "SHINY": Hero(566, 148, 430, 68, (38, 90, 502, 700), (20, 112, 556), 482, 13),
}


def _compact_info(item):
    row = item.row
    values = []
    basic = " · ".join(str(row.get(key) or "").strip()
                       for key in ("category", "height", "weight") if row.get(key))
    if basic:
        values.append((basic, 2))
    ability, hidden = str(row.get("abilities") or ""), str(row.get("hidden_abilities") or "")
    if ability or hidden:
        values.append((f"特性：{ability or '—'}" + (f"（隐藏：{hidden}）" if hidden else ""), 3))
    if row.get("catch_rate"):
        values.append((f"捕获度：{row['catch_rate']}", 1))
    if row.get("egg_groups"):
        values.append((f"蛋群：{row['egg_groups']}", 2))
    gender = str(row.get("gender_ratio") or "").replace(" ", "")
    gender = {"♂0%♀0%": "无性别", "♂0%♀100%": "仅雌性", "♂100%♀0%": "仅雄性"}.get(gender, gender)
    if gender:
        values.append((f"性别：{gender}", 1))
    if row.get("evolution"):
        values.append((f"进化：{row['evolution']}", 4))
    return values or [("暂无基础资料", 1)]


def _compact_lines(draw, item, width, available, details_out):
    lines = []
    for value, cap in _compact_info(item):
        full = _wrap(draw, value, 28, width)
        count = min(cap, max(0, available - len(lines)))
        if len(full) > count and details_out is not None:
            details_out.append(f"#{item.dex_raw} {item.name}：{value}")
        if count:
            lines.extend(_wrap(draw, value, 28, width, count))
    return tuple(lines)


def _matchup_layout(draw, item, width):
    rows = []
    for key, label in (("weak", "弱点"), ("resist", "抗性"), ("immune", "免疫")):
        entries = matchup_groups(item.row, item.types)[key]
        current, used = [], 0
        for name, multiplier in entries:
            text = f"{name}×{multiplier:g}"
            cell_width = math.ceil(draw.textlength(text, font=_font(26))) + 64
            if current and used + cell_width > width:
                rows.append((label if not any(row[0] == label for row in rows) else "", tuple(current)))
                current, used = [], 0
            current.append((name, text, cell_width))
            used += cell_width
        shown = label if not any(row[0] == label for row in rows) else ""
        rows.append((shown, tuple(current)))
    return tuple(rows)


def _matchup_band(base, rows, top, height, p):
    draw = ImageDraw.Draw(base)
    _panel(draw, (48, top, 1032, top + height), p, radius=18)
    for index, (label, entries) in enumerate(rows):
        y = top + 19 + index * 56
        if label:
            draw.text((73, y + 4), label, font=_font(28), fill=p.ink, anchor="lt")
        x = 165
        if not entries:
            draw.text((x, y + 5), "无", font=_font(24), fill=p.muted, anchor="lt")
        for name, text, cell_width in entries:
            base.alpha_composite(render_energy_icon(name, 38), (round(x), round(y)))
            draw.text((round(x + 48), y + 5), text, font=_font(26), fill=p.ink, anchor="lt")
            x += cell_width
    draw.text((73, top + height - 37), "属性相性 · 不计特性影响", font=_font(24), fill=p.muted, anchor="lt")


def _print_mark(draw, kind, right, y, ink):
    if kind == "C":
        draw.ellipse((right - 17, y + 5, right - 3, y + 19), fill=ink)
        return
    if kind == "R":
        draw.polygon(((right - 13, y), (right - 2, y + 12),
                      (right - 13, y + 24), (right - 24, y + 12)), fill=ink)
        return
    count = 3 if kind == "SSR" else 2 if kind == "SR" else 1
    for index in range(count):
        cx, cy = right - 13 - index * 29, y + 12
        points = []
        for vertex in range(10):
            angle = -math.pi / 2 + vertex * math.pi / 5
            radius = 12 if vertex % 2 == 0 else 5
            points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
        draw.polygon(points, fill=ink)
    if kind == "SHINY":
        draw.line((right - 13, y - 4, right - 13, y + 28), fill=ink, width=2)
        draw.line((right - 29, y + 12, right + 3, y + 12), fill=ink, width=2)


def _single_image(item, context, loader, details_out=None):
    kind, p = edition(item), PALETTES[edition(item)]
    hero = HEROES[kind]
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    name_x, name_y, name_width = hero.name_x, hero.name_y, hero.name_width
    size = _fit(measure, item.name, name_width, hero.name_size, 42)
    name_lines = _wrap(measure, item.name, size, name_width, 3)
    name_end = name_y + len(name_lines) * (size + 12)
    type_positions, type_end = _type_layout(measure, item, name_x, name_end + 19, name_width)
    stat_top = type_end + 26
    hero_end = max(842, stat_top + 44 + 6 * 62 + 44)
    rows = _matchup_layout(measure, item, 840)
    band_height = 72 + len(rows) * 56
    band_top = CARD_HEIGHT - 76 - band_height
    art_x, art_y, art_w, art_h = hero.art
    dex_y = max(800, art_y + art_h + 20)
    info_y = max(hero_end + 24, dex_y + 64)
    info_height = band_top - info_y - 18
    if info_height < 144:
        raise ValueError("宝可梦名称与属性超出实体卡片的标题空间")
    left_width = 490
    profile_x = 560
    available = max(1, (info_height - 102) // 42)
    info = _compact_lines(measure, item, left_width - 52, available, details_out)
    profile = _wrap(measure, item.row.get("profile") or "暂无图鉴摘要", 28, 446, min(hero.profile_cap, available))
    base = render_scene(kind, WIDTH, CARD_HEIGHT)
    if kind == "SR":
        base = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    draw = ImageDraw.Draw(base)
    # The card edge keeps the scene inside the same physical-card proportions.
    draw.rounded_rectangle((17, 17, 1063, CARD_HEIGHT - 17), radius=30, outline=p.accent, width=5)
    draw.rounded_rectangle((26, 26, 1054, CARD_HEIGHT - 26), radius=24, outline=p.line, width=1)
    ex, ey, diameter = hero.emblem
    base.alpha_composite(render_emblem(kind, diameter), (ex, ey))
    draw = ImageDraw.Draw(base)
    _panel(draw, (name_x - 20, 80, name_x + name_width + 20, hero_end - 14), p, radius=24)
    _badges(draw, item, name_x, 100)
    _art(base, ImageDraw.Draw(base), item, loader, (art_x, art_y, art_w, art_h), p)
    draw = ImageDraw.Draw(base)
    _text(draw, name_x, name_y, item.name, size, p.ink, width=name_width, maximum=3)
    _types(base, draw, type_positions, p)
    _stat_bars(draw, item, name_x, stat_top, name_width, p)
    _panel(draw, (66, dex_y - 5, 220, dex_y + 35), p, radius=10)
    _text(draw, 76, dex_y + 3, f"#{item.dex_raw}", 24, p.muted, width=134, maximum=1)
    _panel(draw, (48, info_y, 1032, info_y + info_height), p, radius=20)
    draw.line((545, info_y + 24, 545, info_y + info_height - 24), fill=p.line, width=1)
    for x, title in ((74, "宝可梦资料"), (profile_x + 26, "图鉴摘要")):
        draw.text((x, info_y + 21), title, font=_font(30), fill=p.ink, anchor="lt")
    for index, line in enumerate(info):
        draw.text((74, info_y + 82 + index * 42), line, font=_font(28), fill=p.ink, anchor="lt")
    for index, line in enumerate(profile):
        draw.text((profile_x + 26, info_y + 82 + index * 42), line, font=_font(28), fill=p.ink, anchor="lt")
    _matchup_band(base, rows, band_top, band_height, p)
    draw = ImageDraw.Draw(base)
    _panel(draw, (48, CARD_HEIGHT - 64, 1032, CARD_HEIGHT - 19), p, radius=12)
    draw.line((62, CARD_HEIGHT - 67, 1018, CARD_HEIGHT - 67), fill=p.line, width=1)
    draw.text((73, CARD_HEIGHT - 53), "POKÉMON PARTNER", font=_font(24), fill=p.muted, anchor="lt")
    _print_mark(draw, kind, 1007, CARD_HEIGHT - 52, p.muted)
    return _finish_image(base)


def _single(item, context, loader, details_out=None):
    return _encode(_single_image(item, context, loader, details_out))


def _grid(items, context, loader, details_out=None):
    # Every cell is the same complete physical card as a single draw. Scale
    # only after its real layout has collected any fields that need text.
    columns, margin, gap = 2, 24, 20
    cell_width = (WIDTH - 2 * margin - gap) // columns
    cell_height = round(CARD_HEIGHT * cell_width / WIDTH)
    rows = math.ceil(len(items) / columns)
    page_height = 2 * margin + rows * cell_height + (rows - 1) * gap
    base = Image.new("RGB", (WIDTH, page_height), (238, 237, 231))
    for index, item in enumerate(items):
        row, column = divmod(index, columns)
        count = min(columns, len(items) - row * columns)
        offset = (WIDTH - count * cell_width - (count - 1) * gap) // 2
        card = _single_image(item, context, loader, details_out)
        card = card.resize((cell_width, cell_height), Image.Resampling.LANCZOS)
        base.paste(card, (offset + column * (cell_width + gap),
                          margin + row * (cell_height + gap)))
    return _encode(base)


def render_rarity_draw(
    items: Sequence[DrawItem],
    *,
    nickname: str,
    remaining: int,
    daily_limit: int,
    dust: int,
    pity_ssr: int,
    ssr_pity_after: int,
    image_loader: Callable | None = None,
    details_out: list[str] | None = None,
) -> bytes:
    """Render without changing draw data; material and composition follow rarity."""
    if not items:
        raise ValueError("抽卡卡片至少需要一个结果")
    context = {
        "nickname": nickname,
        "remaining": remaining,
        "daily_limit": daily_limit,
        "dust": dust,
        "pity_ssr": pity_ssr,
        "ssr_pity_after": ssr_pity_after,
    }
    return (
        _grid(items, context, image_loader, details_out)
        if len(items) > 1
        else _single(items[0], context, image_loader, details_out)
    )
