"""Monster-card composition with locally drawn paper, bevels and foil finishes.

The frame references Yu-Gi-Oh! print design. Stars describe collection rarity;
all types, portraits, abilities and numbers remain actual Pokémon species data.
Transient draw results stay in chat, outside the collectible card.
"""
from __future__ import annotations

import io
import math
import re
from collections.abc import Callable, Sequence
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

try:
    from .card_renderer import _split_top_level
    from .card_matchups import matchup_groups, TYPE_ORDER
    from .gacha import DrawItem, rarity_label
    from .tcg_symbols import render_energy_icon
    from .yugioh_materials import render_stock, render_art_scene, render_reading_stock
    from .yugioh_fonts import load_card_font
except ImportError:
    from card_renderer import _split_top_level
    from card_matchups import matchup_groups, TYPE_ORDER
    from gacha import DrawItem, rarity_label
    from tcg_symbols import render_energy_icon
    from yugioh_materials import render_stock, render_art_scene, render_reading_stock
    from yugioh_fonts import load_card_font


CARD_W, CARD_H = 1080, 1800
STAT_NAMES = ("HP", "攻击", "防御", "特攻", "特防", "速度")
INKS = {
    "C": ((49, 33, 17), (70, 47, 24), (136, 102, 53)),
    "R": ((53, 29, 24), (77, 44, 34), (157, 103, 78)),
    "SR": ((20, 37, 61), (37, 58, 81), (108, 140, 162)),
    "SSR": ((252, 237, 196), (230, 210, 166), (148, 121, 67)),
    "SHINY": ((37, 31, 62), (58, 48, 79), (152, 136, 168)),
}
TITLE_INKS = {
    "C": (44, 29, 13), "R": (252, 246, 231), "SR": (249, 245, 219),
    "SSR": (255, 233, 153), "SHINY": (45, 36, 77),
}
METALS = {
    "C": ((89, 61, 34), (220, 182, 105), (255, 231, 174)),
    "R": ((83, 48, 37), (185, 118, 80), (247, 223, 188)),
    "SR": ((33, 55, 85), (128, 164, 196), (232, 244, 247)),
    "SSR": ((94, 72, 28), (200, 154, 61), (255, 235, 164)),
    "SHINY": ((96, 94, 125), (176, 176, 203), (246, 248, 255)),
}


@lru_cache(maxsize=32)
def _font(size: int):
    return load_card_font(size, "body")


@lru_cache(maxsize=24)
def _title_font(size: int):
    return load_card_font(size, "title")


def _kind(item: DrawItem) -> str:
    return "SHINY" if item.shiny else item.rarity if item.rarity in INKS else "C"


def _types(item: DrawItem) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(value.strip() for value in re.split(
        r"[/／、,，]", str(item.types or "")) if value.strip()))
    return values if 1 <= len(values) <= 2 and all(value in TYPE_ORDER for value in values) else ("未知",)


def _stats(item: DrawItem) -> dict[str, int]:
    found = re.search(r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)",
                      str(item.row.get("stats") or ""))
    return dict(zip(STAT_NAMES, map(int, found.groups()))) if found else {}


def _text(draw, x, y, value, size=26, fill=(49, 33, 17), *, bold=False, width=None, font=None):
    text = str(value)
    face = font if font is not None else _font(size)
    if width is not None:
        while text and draw.textlength(text, font=face) > width:
            text = text[:-2] + "…" if len(text) > 2 else ""
    draw.text((round(x), round(y)), text, font=face, anchor="lt", fill=fill,
              stroke_width=1 if bold else 0, stroke_fill=fill)


def _wrap(draw, value: str, size: int, width: int, limit: int | None = None, *, font=None):
    """Measure only the visible prefix; return continuation without losing it."""
    text, lines, current = str(value or ""), [], ""
    face = font if font is not None else _font(size)
    for index, char in enumerate(text):
        if char == "\n":
            lines.append(current)
            current = ""
            if limit is not None and len(lines) == limit:
                return tuple(lines), text[index + 1:]
        elif current and draw.textlength(current + char, font=face) > width:
            tail = ""
            if char in "，。；：、！？）)]":
                tail, current = current[-1:], current[:-1]
            lines.append(current)
            if limit is not None and len(lines) == limit:
                return tuple(lines), tail + text[index:]
            current = tail + char
        else:
            current += char
    if current or not lines:
        lines.append(current)
    return tuple(lines), ""


def _paragraph(draw, text, y, *, size, cap, ink, width=884):
    if cap < 1:
        return y, bool(text)
    lines, rest = _wrap(draw, text, size, width, cap)
    if rest:
        line = lines[-1]
        while line and draw.textlength(line + "…", font=_font(size)) > width:
            line = line[:-1]
        lines = (*lines[:-1], line + "…")
    for index, line in enumerate(lines):
        _text(draw, 97, y + index * (size + 9), line, size, ink)
    return y + len(lines) * (size + 9), bool(rest)


def _bevel(draw, box, kind: str, thickness=8, *, recessed=False):
    """Four directional highlights give a real rectangular press mould."""
    left, top, right, bottom = map(round, box)
    dark, metal, light = METALS[kind]
    draw.rectangle((left - 2, top - 2, right + 2, bottom + 2), fill=(25, 24, 28))
    for inset in range(thickness):
        amount = inset / max(1, thickness - 1)
        hi = tuple(round(a * (1 - amount) + b * amount) for a, b in zip(light, metal))
        lo = tuple(round(a * (1 - amount) + b * amount) for a, b in zip(dark, metal))
        if recessed:
            hi, lo = lo, hi
        draw.line((left + inset, top + inset, right - inset, top + inset), fill=hi)
        draw.line((left + inset, top + inset, left + inset, bottom - inset), fill=hi)
        draw.line((left + inset, bottom - inset, right - inset, bottom - inset), fill=lo)
        draw.line((right - inset, top + inset, right - inset, bottom - inset), fill=lo)
    draw.rectangle((left + thickness, top + thickness, right - thickness, bottom - thickness),
                   outline=metal, width=1)


def _outer_frame(base: Image.Image, kind: str):
    draw = ImageDraw.Draw(base)
    # Small corner radius and narrow charcoal edge belong to monster-card stock.
    draw.rounded_rectangle((4, 4, CARD_W - 5, CARD_H - 5), radius=24,
                           outline=(35, 33, 36), width=18)
    dark, metal, light = METALS[kind]
    for inset, color in ((23, dark), (26, metal), (29, light), (33, dark)):
        draw.rounded_rectangle((inset, inset, CARD_W - inset - 1, CARD_H - inset - 1),
                               radius=max(3, 25 - inset // 2), outline=color, width=2)
    if kind in {"SSR", "SHINY"}:
        for x, y, sign in ((40, 40, 1), (CARD_W - 40, CARD_H - 40, -1)):
            draw.line((x, y + sign * 55, x, y, x + sign * 55, y), fill=light, width=2)


def _rarity_star(base: Image.Image, x: int, y: int, kind: str):
    icon = Image.new("RGBA", (46, 46))
    draw = ImageDraw.Draw(icon)
    for inset in range(21):
        amount = inset / 20
        color = tuple(round(a + (b - a) * amount) for a, b in
                      zip((99, 28, 14), (246, 105, 31)))
        draw.ellipse((inset, inset, 45 - inset, 45 - inset), fill=(*color, 255))
    draw.ellipse((1, 1, 44, 44), outline=METALS[kind][2], width=2)
    points = []
    for index in range(10):
        angle = -math.pi / 2 + index * math.pi / 5
        radius = 16 if index % 2 == 0 else 6.6
        points.append((23 + math.cos(angle) * radius, 23 + math.sin(angle) * radius))
    draw.polygon(points, fill=(255, 225, 62), outline=(145, 74, 19))
    draw.arc((5, 4, 39, 38), 202, 293, fill=(255, 239, 151), width=2)
    base.alpha_composite(icon, (x, y))


def _portrait(base: Image.Image, item: DrawItem, loader: Callable | None, box):
    x, y, width, height = map(round, box)
    row = dict(item.row)
    if item.shiny:
        row["_artwork_shiny"] = True
    art = loader(row) if loader else None
    if isinstance(art, bytes):
        with Image.open(io.BytesIO(art)) as opened:
            art = opened.convert("RGBA")
    if art is not None:
        art = art.convert("RGBA")
        bounds = art.getchannel("A").getbbox()
        art = art.crop(bounds) if bounds else None
    if art is None:
        if _types(item)[0] != "未知":
            base.alpha_composite(render_energy_icon(_types(item)[0], 130),
                                 (x + (width - 130) // 2, y + (height - 130) // 2 - 30))
        # A real missing image has already been explained by ArtworkCache logs.
        label = "立绘暂未缓存"
        draw = ImageDraw.Draw(base)
        ink = (252, 241, 209)
        draw.text((x + width / 2, y + height / 2 + 90), label, font=_font(28),
                  anchor="mt", fill=ink, stroke_width=2, stroke_fill=(33, 31, 40))
        return
    scale = min(3.0, width / art.width, height / art.height)
    art = art.resize((max(1, round(art.width * scale)), max(1, round(art.height * scale))),
                     Image.Resampling.LANCZOS)
    tx, ty = x + (width - art.width) // 2, y + (height - art.height) // 2
    padding = 22
    alpha = Image.new("L", (art.width + padding * 2, art.height + padding * 2))
    alpha.paste(art.getchannel("A"), (padding, padding))
    shadow = Image.new("RGBA", alpha.size, (12, 15, 22, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(8)).point(lambda a: round(a * .44)))
    base.alpha_composite(shadow, (tx - padding + 5, ty - padding + 10))
    kind = _kind(item)
    if kind in {"SR", "SSR", "SHINY"}:
        color = (188, 222, 241, 0) if kind == "SR" else (238, 206, 133, 0) if kind == "SSR" else (237, 244, 255, 0)
        strength = .32 if kind == "SR" else .28 if kind == "SSR" else .74
        rim = Image.new("RGBA", alpha.size, color)
        rim.putalpha(alpha.filter(ImageFilter.MaxFilter(5)).point(lambda a: round(a * strength)))
        base.alpha_composite(rim, (tx - padding, ty - padding))
    base.alpha_composite(art, (tx, ty))


def _name_layout(draw, name: str):
    for size in range(66, 35, -2):
        if draw.textlength(name, font=_title_font(size)) <= 776:
            return size, (name,)
    for size in range(38, 27, -2):
        # Long names keep the same title face at every size and on both lines.
        lines, rest = _wrap(draw, name, size, 776, 2, font=_title_font(size))
        if not rest:
            return size, lines
    raise ValueError("完整宝可梦名称超出卡片的标题空间，请使用文本。")


def _matchup_layout(draw, item: DrawItem):
    layout = []
    types = _types(item)
    groups = matchup_groups(item.row, types) if types != ("未知",) else None
    for key, label in (("weak", "弱点"), ("resist", "抗性"), ("immune", "免疫")):
        if groups is None:
            layout.append((label, (None,)))
            continue
        entries = groups[key]
        rows, current, used = [], [], 0
        for name, multiplier in entries:
            text = f"{name} ×{multiplier:g}"
            width = 36 + round(draw.textlength(text, font=_font(24))) + 18
            if current and used + width > 790:
                rows.append(tuple(current))
                current, used = [], 0
            current.append((name, text, width))
            used += width
        rows.append(tuple(current))
        layout.append((label, tuple(rows)))
    return tuple(layout)


def _matchups(base, layout, top, kind):
    draw = ImageDraw.Draw(base)
    ink, muted, rule = INKS[kind]
    y = top
    draw.line((94, y - 12, 986, y - 12), fill=rule, width=1)
    for label, rows in layout:
        _text(draw, 97, y + 5, label, 24, ink, bold=True)
        for index, row in enumerate(rows):
            yy, x = y + index * 37, 179
            if row is None:
                _text(draw, x, yy + 5, "未知", 24, muted)
                continue
            if not row:
                _text(draw, x, yy + 5, "无", 24, muted)
            for name, text, width in row:
                base.alpha_composite(render_energy_icon(name, 29), (x, yy + 2))
                _text(draw, x + 36, yy + 5, text, 24, ink)
                x += width
        y += len(rows) * 37 + 7


def _stats_band(base, item, kind):
    draw = ImageDraw.Draw(base)
    ink, muted, rule = INKS[kind]
    stats = _stats(item)
    draw.line((91, 1620, 989, 1620), fill=rule, width=2)
    _text(draw, 95, 1632, "种族值", 24, ink, bold=True)
    _text(draw, 744, 1632, f"总和 {item.bst}", 24, ink, width=245)
    for index, name in enumerate(("HP", "特攻", "特防", "速度")):
        x = 98 + index * 227
        _text(draw, x, 1671, name, 24, muted)
        _text(draw, x + 79, 1668, stats.get(name, "—"), 31, ink, bold=True)
    draw.line((91, 1704, 989, 1704), fill=rule, width=1)
    _text(draw, 100, 1711, f"攻击 ATK / {stats.get('攻击', '—')}", 31, ink, font=_title_font(31))
    defense = f"防御 DEF / {stats.get('防御', '—')}"
    _text(draw, 986 - draw.textlength(defense, font=_title_font(31)), 1711, defense, 31, ink,
          font=_title_font(31))


def _seal(base, kind):
    """Original micro-foil diamond; no publisher logo or copied authenticity mark."""
    draw = ImageDraw.Draw(base)
    x, y, size = 989, 1769, 22
    dark, metal, light = METALS[kind]
    draw.rectangle((x, y, x + size, y + size), fill=dark, outline=light, width=1)
    for offset in range(3, size - 2, 4):
        color = ((173 + offset * 3, 168 + offset * 2, 204), metal)[kind != "SHINY"]
        draw.line((x + offset, y + 2, x + 2, y + offset), fill=color, width=1)
        draw.line((x + size - 2, y + offset, x + offset, y + size - 2), fill=light, width=1)
    draw.polygon(((x + 11, y + 5), (x + 17, y + 11), (x + 11, y + 17), (x + 5, y + 11)),
                 outline=light)


def _card(item: DrawItem, loader: Callable | None):
    kind, types = _kind(item), _types(item)
    ink, muted, rule = INKS[kind]
    base = render_stock(kind, CARD_W, CARD_H)
    draw = ImageDraw.Draw(base)
    _outer_frame(base, kind)
    _bevel(draw, (58, 57, 1022, 158), kind, 7, recessed=True)
    # Title inherits the stock and its grain instead of a plain white textbox.
    title = render_stock(kind, CARD_W, CARD_H).crop((66, 65, 1014, 150))
    if kind in {"R", "SR", "SSR"}:
        glaze = Image.new("RGBA", title.size, (25, 19, 28, 180 if kind == "R" else 108))
        title.alpha_composite(glaze)
    base.alpha_composite(title, (66, 65))
    if kind in {"C", "R", "SR", "SSR"}:
        # A stock-coloured ink glaze quiets local relief behind small lettering;
        # the material is still visible underneath rather than a flat textbox.
        glaze_color = {
            "C": (242, 221, 171, 120), "R": (37, 22, 18, 155),
            "SR": (18, 33, 63, 150), "SSR": (19, 20, 23, 120),
        }[kind]
        for y, height in ((165, 58), (975, 39)):
            base.alpha_composite(Image.new("RGBA", (950, height), glaze_color), (65, y))
    draw = ImageDraw.Draw(base)
    size, lines = _name_layout(draw, item.name)
    text_top = 65 + (85 - len(lines) * (size + 4)) // 2 + 4
    for index, line in enumerate(lines):
        _text(draw, 83, text_top + index * (size + 4), line, size, TITLE_INKS[kind],
              font=_title_font(size))
    # The attribute jewel uses the species' real Pokémon energy symbol.
    dark, metal, light = METALS[kind]
    draw.ellipse((917, 66, 1007, 156), fill=dark, outline=light, width=2)
    if types[0] != "未知":
        base.alpha_composite(render_energy_icon(types[0], 78), (923, 72))
    else:
        draw.text((962, 82), "?", font=_font(52), anchor="mt", fill=light)
    label = f"{item.rarity} · {rarity_label(item.rarity)}"
    if item.shiny:
        label = "闪光 · " + label
    _text(ImageDraw.Draw(base), 77, 178, label, 27, TITLE_INKS[kind], bold=True)
    stars = {"C": 2, "R": 4, "SR": 6, "SSR": 8, "SHINY": 10}[kind]
    for index in range(stars):
        _rarity_star(base, 952 - index * 51, 171, kind)
    draw = ImageDraw.Draw(base)
    _bevel(draw, (78, 231, 1002, 968), kind, 11)
    scene = render_art_scene(kind, types[0], 898, 711)
    base.alpha_composite(scene, (91, 244))
    _portrait(base, item, loader, (111, 264, 858, 671))
    draw = ImageDraw.Draw(base)
    _text(draw, 89, 980, f"#{item.dex_raw}", 24, TITLE_INKS[kind])
    serial = "POKÉMON PARTNER"
    _text(draw, 992 - draw.textlength(serial, font=_font(24)), 980, serial, 24, TITLE_INKS[kind])

    _bevel(draw, (63, 1021, 1017, 1757), kind, 5, recessed=True)
    reading = render_reading_stock(kind, 940, 722)
    base.alpha_composite(reading, (70, 1028))
    draw = ImageDraw.Draw(base)
    # The descriptor rail resembles a monster type line, but contains actual data.
    x = 97
    for type_name in types:
        if type_name == "未知":
            _text(draw, x, 1045, "属性未知", 27, ink, bold=True)
            x += 130
            continue
        base.alpha_composite(render_energy_icon(type_name, 31), (x, 1042))
        _text(draw, x + 39, 1045, type_name, 27, ink, bold=True)
        x += 39 + round(draw.textlength(type_name, font=_font(27))) + 19
    category = str(item.row.get("category") or "宝可梦")
    _text(draw, x + 8, 1045, f"／{category}", 27, ink, width=986 - x - 8)
    missing = []
    if draw.textlength(f"／{category}", font=_font(27)) > 986 - x - 8:
        missing.append(f"分类：{category}")
    y = 1089
    layout = _matchup_layout(draw, item)
    matchup_height = sum(len(rows) * 37 + 7 for _label, rows in layout)
    matchup_top = 1599 - matchup_height
    metadata = " · ".join(str(item.row.get(k) or "") for k in ("height", "weight") if item.row.get(k))
    if metadata:
        y, overflow = _paragraph(draw, metadata, y, size=26, cap=1, ink=muted)
        if overflow:
            missing.append(metadata)
    ability = str(item.row.get("abilities") or "—")
    if item.row.get("hidden_abilities"):
        ability += f"（隐藏：{item.row['hidden_abilities']}）"
    y, overflow = _paragraph(draw, "特性：" + ability, y + 8, size=26, cap=2, ink=ink)
    if overflow:
        missing.append("特性：" + ability)
    basic = " · ".join(value for value in (
        f"捕获度：{item.row['catch_rate']}" if item.row.get("catch_rate") else "",
        f"蛋群：{item.row['egg_groups']}" if item.row.get("egg_groups") else "",
        f"性别：{item.row['gender_ratio']}" if item.row.get("gender_ratio") else "",
    ) if value)
    if basic:
        cap = min(2, max(0, (matchup_top - y - 18) // 35))
        y, overflow = _paragraph(draw, basic, y + 8, size=26, cap=cap, ink=ink)
        if overflow:
            missing.append(basic)
    evolution = "；".join(dict.fromkeys(part.strip() for part in
        _split_top_level(str(item.row.get("evolution") or ""), "；") if part.strip()))
    if evolution and evolution not in {item.name, str(item.row.get("name_zh") or "")}:
        cap = min(2, max(0, (matchup_top - y - 18) // 33))
        y, overflow = _paragraph(draw, "进化：" + evolution, y + 8, size=24, cap=cap, ink=ink)
        if overflow:
            missing.append("进化：" + evolution)
    # A brief Pokédex excerpt fills available space; practical data takes priority.
    cap = min(5, max(0, (matchup_top - y - 26) // 35))
    if cap:
        _paragraph(draw, str(item.row.get("profile") or "暂无图鉴摘要"), y + 9,
                   size=26, cap=cap, ink=muted)
    _matchups(base, layout, matchup_top, kind)
    _stats_band(base, item, kind)
    draw = ImageDraw.Draw(base)
    _seal(base, kind)
    return base, tuple(missing)


def _encode(base: Image.Image) -> bytes:
    buf = io.BytesIO()
    base.convert("RGB").save(buf, "JPEG", quality=94, optimize=True)
    return buf.getvalue()


def render_yugioh_draw(
    items: Sequence[DrawItem], *, nickname: str, remaining: int, daily_limit: int,
    dust: int, pity_ssr: int, ssr_pity_after: int, image_loader: Callable | None = None,
    details_out: list[str] | None = None,
) -> bytes:
    """Render collectible cards; counts, NEW and dust remain in the chat notice."""
    if not items:
        raise ValueError("抽卡卡片至少需要一个结果")
    tile_width, gap = 518, 20
    tile_height = round(CARD_H * tile_width / CARD_W)
    rows = math.ceil(len(items) / 2)
    sheet = None if len(items) == 1 else Image.new(
        "RGBA", (CARD_W, rows * (tile_height + gap) + gap), (35, 33, 40, 255))
    for index, item in enumerate(items):
        card, missing = _card(item, image_loader)
        if details_out is not None and missing:
            details_out.append(f"#{item.dex_raw} {item.name}：" + "；".join(missing))
        if sheet is None:
            return _encode(card)
        row, column = divmod(index, 2)
        sheet.alpha_composite(card.resize((tile_width, tile_height), Image.Resampling.LANCZOS),
                              (12 + column * (tile_width + gap), gap + row * (tile_height + gap)))
    return _encode(sheet)
