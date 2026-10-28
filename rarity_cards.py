"""Five independently composed daily Pokémon editions; no network or state access."""

from __future__ import annotations

import io
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

try:
    from .card_renderer import load_font
    from .gacha import DrawItem, rarity_label
    from .partner_cards import TYPE_COLORS, _pokeball
    from .rarity_emblems import render_emblem
    from .rarity_scenes import render_scene
except ImportError:
    from card_renderer import load_font
    from gacha import DrawItem, rarity_label
    from partner_cards import TYPE_COLORS, _pokeball
    from rarity_emblems import render_emblem
    from rarity_scenes import render_scene

WIDTH = 1080
MARGIN = 48
BODY_SIZE = 30
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
        (248, 247, 228),
        (35, 52, 39),
        (76, 92, 76),
        (57, 95, 56),
        (144, 171, 120),
        (213, 223, 199),
    ),
    "R": Palette(
        (7, 37, 68),
        (12, 37, 67),
        (239, 250, 255),
        (174, 207, 228),
        (116, 225, 255),
        (64, 126, 167),
        (49, 79, 109),
    ),
    "SR": Palette(
        (29, 12, 52),
        (38, 22, 59),
        (250, 241, 255),
        (207, 185, 224),
        (248, 167, 226),
        (142, 85, 157),
        (82, 52, 106),
    ),
    "SSR": Palette(
        (16, 20, 24),
        (27, 28, 28),
        (255, 248, 225),
        (218, 197, 155),
        (246, 205, 114),
        (155, 125, 62),
        (73, 67, 50),
    ),
    "SHINY": Palette(
        (221, 230, 245),
        (252, 250, 255),
        (41, 36, 64),
        (89, 79, 111),
        (101, 56, 137),
        (162, 150, 196),
        (217, 213, 233),
    ),
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


def _panel(draw, box, p, radius=22, fill=None):
    draw.rounded_rectangle(
        box, radius=radius, fill=fill or p.panel, outline=p.line, width=2
    )


def _stats(item):
    match = re.search(
        r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)",
        str(item.row.get("stats") or ""),
    )
    return dict(zip(STATS, map(int, match.groups()))) if match else {}


def _stat_bars(draw, item, x, y, width, p, *, step=40):
    values = _stats(item)
    if not values:
        return _text(draw, x, y, f"种族值总和 {item.bst}", 26, p.ink, width=width)
    draw.text((x, y), "种族值", font=_font(26), fill=p.muted, anchor="lt")
    y += 44
    bar_x, bar_w = x + 78, width - 146
    for index, key in enumerate(STATS):
        yy = y + index * step
        draw.text((x, yy), key, font=_font(26), fill=p.ink, anchor="lt")
        draw.rounded_rectangle(
            (bar_x, yy + 8, bar_x + bar_w, yy + 25), radius=8, fill=p.track
        )
        ratio = min(1, max(0, values[key] / 180))
        if ratio:
            draw.rounded_rectangle(
                (bar_x, yy + 8, bar_x + max(8, bar_w * ratio), yy + 25),
                radius=8,
                fill=STAT_COLORS[index],
            )
        draw.text(
            (bar_x + bar_w + 12, yy),
            str(values[key]),
            font=_font(26),
            fill=p.ink,
            anchor="lt",
        )
    return _text(draw, x, y + 6 * step, f"总和 {item.bst}", 26, p.muted, width=width)


def _info(item, *, dex=False):
    row = item.row
    basic = " · ".join(
        str(row.get(k) or "").strip()
        for k in ("category", "height", "weight")
        if row.get(k)
    )
    lines = [(f"全国图鉴 #{item.dex_raw}", 1)] if dex else []
    if basic:
        lines.append((basic, 2))
    ability, hidden = (
        str(row.get("abilities") or ""),
        str(row.get("hidden_abilities") or ""),
    )
    if ability or hidden:
        lines.append(
            (f"特性：{ability or '—'}" + (f"（隐藏：{hidden}）" if hidden else ""), 3)
        )
    if row.get("catch_rate"):
        lines.append((f"捕获度：{row['catch_rate']}", 2))
    for key, label in (
        ("weak_to", "弱点"),
        ("resist_to", "抗性"),
        ("immune_to", "免疫"),
    ):
        if row.get(key):
            lines.append((f"{label}：{row[key]}", 3))
    extra = []
    if row.get("egg_groups"):
        extra.append(f"蛋群：{row['egg_groups']}")
    gender = str(row.get("gender_ratio") or "")
    gender = {"♂0%♀0%": "无性别", "♂0%♀100%": "仅雌性", "♂100%♀0%": "仅雄性"}.get(
        gender.replace(" ", ""), gender
    )
    if gender:
        extra.append(f"性别：{gender}")
    if extra:
        lines.append((" · ".join(extra), 2))
    if row.get("evolution"):
        lines.append((f"进化：{row['evolution']}", 4))
    return lines or [("暂无基础资料", 1)]


def _block_lines(draw, texts, width):
    out = []
    for text, cap in texts:
        out.extend(_wrap(draw, text, BODY_SIZE, width, cap))
    return tuple(out)


def _block_height(lines):
    return 86 + len(lines) * (BODY_SIZE + 12)


def _block(draw, x, y, width, title, lines, p, *, height=None, plain=False):
    height = height or _block_height(lines)
    if not plain:
        _panel(draw, (x, y, x + width, y + height), p)
    draw.text((x + 26, y + 19), title, font=_font(30), fill=p.ink, anchor="lt")
    for index, line in enumerate(lines):
        draw.text(
            (x + 26, y + 72 + index * (BODY_SIZE + 12)),
            line,
            font=_font(BODY_SIZE),
            fill=p.ink,
            anchor="lt",
        )
    return y + height


def _art(base, draw, item, loader, box, p):
    x, y, width, height = box
    art_row = dict(item.row, _artwork_shiny=True) if item.shiny else item.row
    image = loader(art_row) if loader else None
    if isinstance(image, bytes):
        image = Image.open(io.BytesIO(image))
    if image is not None:
        image = image.convert("RGBA")
        bounds = image.getchannel("A").getbbox()
        if bounds:
            image = image.crop(bounds)
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
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
    if item.is_new:
        entries.append(("NEW", (17, 105, 76), (255, 255, 255)))
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
        size = math.ceil(draw.textlength(label, font=_font(24))) + 28
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
            positions.append((label, cursor, y + index * 48))
            cursor += size + 10
    return positions, y + 48 * len(rows) if positions else y


def _types(draw, positions):
    for label, x, y in positions:
        color = TYPE_COLORS.get(label, (143, 151, 151))
        rgb = [value / 255 for value in color]
        luminance = sum(
            coef
            * (
                channel / 12.92
                if channel <= 0.04045
                else ((channel + 0.055) / 1.055) ** 2.4
            )
            for coef, channel in zip((0.2126, 0.7152, 0.0722), rgb)
        )
        ink = (26, 30, 34) if luminance > 0.18 else (255, 255, 255)
        _pill(draw, x, y, label, color, ink)


def _header(draw, context, p, kind):
    _panel(draw, (40, 40, 1040, 200), p, radius=24)
    # The red rule and Poké Ball are the original card's shared identity.
    draw.rounded_rectangle((58, 38, 1022, 46), radius=4, fill=(199, 53, 53))
    _pokeball(draw, 82, 89, 24)
    draw.text((120, 63), "今日宝可梦", font=_font(46), fill=p.ink, anchor="lt")
    trainer = _wrap(draw, f"训练家 · {context['nickname']}", 24, 396, 1)[0]
    draw.text((120, 123), trainer, font=_font(24), fill=p.muted, anchor="lt")
    for x, label, value in (
        (566, "今日剩余次数", f"{context['remaining']} / {context['daily_limit']}"),
        (808, "星尘余额", str(context["dust"])),
    ):
        draw.rounded_rectangle((x, 61, x + 212, 144), radius=14, fill=p.track)
        draw.text((x + 16, 70), label, font=_font(24), fill=p.ink, anchor="lt")
        size = _fit(draw, value, 180, 34, 24)
        draw.text((x + 16, 103), value, font=_font(size), fill=p.ink, anchor="lt")
    after = context["ssr_pity_after"]
    if after:
        progress = max(0, min(1, context["pity_ssr"] / after))
        draw.rounded_rectangle((566, 167, 734, 175), radius=4, fill=p.track)
        if progress:
            draw.rounded_rectangle(
                (566, 167, 566 + max(8, 168 * progress), 175), radius=4, fill=p.accent
            )
        label = f"SSR 保底 {context['pity_ssr']}/{after}"
    else:
        label = "SSR 保底关闭"
    draw.text((754, 156), label, font=_font(24), fill=p.muted, anchor="lt")


def _footer(draw, item, y, p):
    note = (
        "新宝可梦已加入图鉴" if item.is_new else f"重复获得 · 转化为 {item.dust} 星尘"
    )
    fill = (224, 242, 230) if item.is_new else (251, 239, 207)
    ink = (15, 87, 61) if item.is_new else (110, 71, 14)
    draw.rounded_rectangle((48, y, 1032, y + 58), radius=14, fill=fill)
    _text(draw, 68, y + 15, note, 28, ink, width=940)
    _panel(draw, (40, y + 76, 1040, y + 124), p, radius=12)
    _text(
        draw,
        58,
        y + 88,
        "重复宝可梦自动折算星尘 · 兑换宝可梦 <名称> 可定向获得",
        24,
        p.muted,
        width=964,
        maximum=1,
    )


def _finish(base):
    mask = Image.new("L", base.size)
    ImageDraw.Draw(mask).rounded_rectangle(
        (16, 16, WIDTH - 16, base.height - 16), radius=30, fill=255
    )
    framed = Image.new("RGB", base.size, (238, 237, 231))
    framed.paste(base.convert("RGB"), (0, 0), mask)
    ImageDraw.Draw(framed).rounded_rectangle(
        (16, 16, WIDTH - 16, base.height - 16),
        radius=30,
        outline=(188, 197, 187),
        width=1,
    )
    stream = io.BytesIO()
    framed.save(stream, "JPEG", quality=95, optimize=True)
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
    "C": Hero(566, 300, 430, 62, (77, 272, 454, 444), (58, 254, 480), 482, 12),
    "R": Hero(574, 298, 418, 64, (79, 265, 448, 458), (52, 247, 496), 482, 12),
    "SR": Hero(566, 300, 430, 66, (74, 264, 462, 464), (52, 250, 500), 482, 12),
    "SSR": Hero(574, 302, 418, 68, (62, 251, 476, 480), (44, 239, 516), 500, 12),
    "SHINY": Hero(584, 300, 408, 64, (50, 254, 496, 480), (40, 236, 524), 482, 12),
}


def _single(item, context, loader):
    kind = edition(item)
    p, hero = PALETTES[kind], HEROES[kind]
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    size = _fit(measure, item.name, hero.name_width, hero.name_size, 42)
    name_lines = _wrap(measure, item.name, size, hero.name_width, 3)
    name_end = hero.name_y + len(name_lines) * (size + 12)
    type_positions, type_end = _type_layout(
        measure, item, hero.name_x, name_end + 18, hero.name_width
    )
    stat_top = type_end + 22
    hero_end = max(806, stat_top + 44 + 240 + 48)
    fields = _info(item)
    matchups = [
        field for field in fields if field[0].startswith(("弱点：", "抗性：", "免疫："))
    ]
    fields = [field for field in fields if field not in matchups]
    info_x, left_width = 48, hero.left_width
    profile_x = info_x + left_width + 18
    profile_width = 1032 - profile_x
    info = _block_lines(measure, fields, left_width - 52)
    profile = _wrap(
        measure,
        item.row.get("profile") or "暂无图鉴摘要",
        BODY_SIZE,
        profile_width - 52,
        hero.profile_cap,
    )
    info_y = hero_end + 30
    shared_height = max(_block_height(info), _block_height(profile))
    content_end = info_y + shared_height
    matchup_lines = _block_lines(measure, matchups, 932) if matchups else ()
    band_height = 24 + len(matchup_lines) * (BODY_SIZE + 12) if matchup_lines else 0
    footer_y = content_end + 24 + (band_height + 18 if matchup_lines else 0)
    base = render_scene(kind, WIDTH, footer_y + 148)
    if kind == "SR":
        # Move the stage under the portrait while preserving the original left/right reading order.
        base = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    draw = ImageDraw.Draw(base)
    _header(draw, context, p, kind)
    draw.line((48, 214, 1032, 214), fill=p.line, width=1)
    ex, ey, diameter = hero.emblem
    base.alpha_composite(render_emblem(kind, diameter), (ex, ey))
    draw = ImageDraw.Draw(base)
    _panel(
        draw,
        (hero.name_x - 20, 230, hero.name_x + hero.name_width + 20, hero_end - 16),
        p,
        radius=24,
    )
    _badges(draw, item, hero.name_x, 248)
    _art(base, ImageDraw.Draw(base), item, loader, hero.art, p)
    draw = ImageDraw.Draw(base)
    _text(
        draw,
        hero.name_x,
        hero.name_y,
        item.name,
        size,
        p.ink,
        width=hero.name_width,
        maximum=3,
    )
    _types(draw, type_positions)
    _stat_bars(draw, item, hero.name_x, stat_top, hero.name_width, p)
    dex_y = max(760, hero.art[1] + hero.art[3] + 18)
    _panel(draw, (66, dex_y - 5, 220, dex_y + 35), p, radius=10)
    _text(draw, 76, dex_y, f"#{item.dex_raw}", 24, p.muted, width=134, maximum=1)
    # A continuous reading surface and fine rules retain the original lower two-column rhythm.
    _panel(draw, (48, info_y, 1032, content_end), p, radius=20)
    draw.line((74, info_y + 1, 1006, info_y + 1), fill=p.line, width=1)
    _block(
        draw,
        info_x,
        info_y,
        left_width,
        "宝可梦资料",
        info,
        p,
        height=shared_height,
        plain=True,
    )
    _block(
        draw,
        profile_x,
        info_y,
        profile_width,
        "图鉴摘要",
        profile,
        p,
        height=shared_height,
        plain=True,
    )
    if matchup_lines:
        yy = content_end + 18
        _panel(draw, (48, yy, 1032, yy + band_height), p, radius=18)
        for index, line in enumerate(matchup_lines):
            _text(
                draw,
                74,
                yy + 12 + index * (BODY_SIZE + 12),
                line,
                BODY_SIZE,
                p.ink,
                width=932,
            )
    _footer(draw, item, footer_y, p)
    return _finish(base)


def _tile(base, item, box, loader):
    x, y, width, height = box
    kind, p = edition(item), PALETTES[edition(item)]
    tile = render_scene(kind, width, height)
    draw = ImageDraw.Draw(tile)
    _panel(draw, (12, 14, width - 12, 64), p, radius=12)
    label = f"{item.rarity} · {rarity_label(item.rarity)}" + (
        " · 闪光" if item.shiny else ""
    )
    _text(draw, 26, 27, label, 24, p.accent, width=width - 52, maximum=1)
    art = (26, 83, width - 52, width - 52)
    _art(tile, draw, item, loader, art, p)
    draw = ImageDraw.Draw(tile)
    name_y = width + 44
    _panel(draw, (12, name_y - 12, width - 12, height - 12), p, radius=14)
    name_size = 30 if width > 400 else 28
    end = _text(
        draw, 26, name_y, item.name, name_size, p.ink, width=width - 52, maximum=2
    )
    end = _text(
        draw,
        26,
        end + 12,
        f"{item.types} · 总和 {item.bst}",
        24,
        p.muted,
        width=width - 52,
        maximum=1,
    )
    fill, ink = (
        ((224, 242, 230), (15, 87, 61))
        if item.is_new
        else ((251, 239, 207), (110, 71, 14))
    )
    draw.rounded_rectangle((23, end + 6, width - 23, end + 44), radius=10, fill=fill)
    _text(
        draw,
        32,
        end + 13,
        "NEW · 新伙伴" if item.is_new else f"重复 · +{item.dust}星尘",
        24,
        ink,
        width=width - 64,
        maximum=1,
    )
    base.alpha_composite(tile, (x, y))


def _grid(items, context, loader):
    columns = 2 if len(items) <= 4 else 3
    gap = 18
    width = (WIDTH - 96 - gap * (columns - 1)) // columns
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    size = 30 if width > 400 else 28
    name_h = max(
        len(_wrap(measure, item.name, size, width - 52, 2)) * (size + 12)
        for item in items
    )
    height = width + 44 + name_h + 12 + 36 + 6 + 44 + 24
    rows = math.ceil(len(items) / columns)
    page_height = 224 + rows * (height + gap) + 88
    base = Image.new("RGBA", (WIDTH, page_height), (233, 238, 231, 255))
    draw = ImageDraw.Draw(base)
    _header(draw, context, PALETTES["C"], "C")
    for index, item in enumerate(items):
        row, column = divmod(index, columns)
        count = min(columns, len(items) - row * columns)
        offset = (WIDTH - (count * width + (count - 1) * gap)) // 2
        _tile(
            base,
            item,
            (
                offset + column * (width + gap),
                224 + row * (height + gap),
                width,
                height,
            ),
            loader,
        )
    draw = ImageDraw.Draw(base)
    p = PALETTES["C"]
    _panel(draw, (40, page_height - 68, 1040, page_height - 20), p, radius=12)
    _text(
        draw,
        58,
        page_height - 56,
        "重复宝可梦自动折算星尘 · 兑换宝可梦 <名称> 可定向获得",
        24,
        p.muted,
        width=964,
        maximum=1,
    )
    return _finish(base)


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
        _grid(items, context, image_loader)
        if len(items) > 1
        else _single(items[0], context, image_loader)
    )
