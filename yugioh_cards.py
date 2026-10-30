"""Reference-led monster cards with printed stock and restrained foil finishes.

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
    from .gacha import DrawItem
    from .yugioh_symbols import render_attribute_icon, render_level_star
    from .yugioh_materials import render_stock, render_art_scene, render_reading_stock
    from .yugioh_fonts import load_card_font
except ImportError:
    from card_renderer import _split_top_level
    from card_matchups import matchup_groups, TYPE_ORDER
    from gacha import DrawItem
    from yugioh_symbols import render_attribute_icon, render_level_star
    from yugioh_materials import render_stock, render_art_scene, render_reading_stock
    from yugioh_fonts import load_card_font


CARD_W, CARD_H = 1080, 1680
STAT_NAMES = ("HP", "攻击", "防御", "特攻", "特防", "速度")
INKS = {
    "C": ((34, 24, 19), (63, 47, 37), (127, 100, 79)),
    "R": ((36, 23, 32), (68, 43, 60), (140, 104, 124)),
    "SR": ((31, 25, 39), (57, 45, 69), (126, 109, 142)),
    "SSR": ((29, 25, 23), (58, 48, 37), (131, 114, 81)),
    "SHINY": ((28, 27, 34), (53, 50, 65), (132, 127, 143)),
}
TITLE_INKS = {
    "C": (39, 23, 14), "R": (251, 245, 250), "SR": (30, 19, 44),
    "SSR": (255, 233, 153), "SHINY": (45, 36, 77),
}
METALS = {
    "C": ((94, 52, 33), (191, 126, 89), (244, 202, 162)),
    "R": ((78, 25, 65), (174, 76, 142), (239, 183, 221)),
    "SR": ((53, 34, 68), (121, 92, 145), (193, 168, 210)),
    "SSR": ((11, 14, 18), (80, 80, 77), (176, 171, 155)),
    "SHINY": ((91, 93, 96), (178, 179, 181), (250, 248, 244)),
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


def _paragraph(draw, text, y, *, size, cap, ink, width=894):
    if cap < 1:
        return y, bool(text)
    lines, rest = _wrap(draw, text, size, width, cap)
    if rest:
        line = lines[-1]
        while line and draw.textlength(line + "…", font=_font(size)) > width:
            line = line[:-1]
        lines = (*lines[:-1], line + "…")
    for index, line in enumerate(lines):
        _text(draw, 92, y + index * (size + 9), line, size, ink)
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
    # A narrow charcoal edge and cut-paper bevel match the supplied cards.
    draw.rectangle((0, 0, CARD_W - 1, CARD_H - 1), outline=(43, 43, 47), width=18)
    dark, _, light = METALS[kind]
    draw.line((19, CARD_H - 20, 19, 19, CARD_W - 20, 19), fill=light, width=2)
    draw.line((CARD_W - 20, 19, CARD_W - 20, CARD_H - 20, 19, CARD_H - 20), fill=dark, width=3)


def _rarity_star(base: Image.Image, x: int, y: int, kind: str):
    base.alpha_composite(render_level_star(46), (x, y))


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
            base.alpha_composite(render_attribute_icon(_types(item)[0], 130),
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
        if draw.textlength(name, font=_title_font(size)) <= 748:
            return size, (name,)
    for size in range(38, 27, -2):
        # Long names keep the same title face at every size and on both lines.
        lines, rest = _wrap(draw, name, size, 748, 2, font=_title_font(size))
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
            text = f"×{multiplier:g}"
            width = 41 + round(draw.textlength(text, font=_font(26))) + 18
            if current and used + width > 806:
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
    draw.line((91, y - 13, 989, y - 13), fill=rule, width=1)
    for label, rows in layout:
        _text(draw, 92, y + 5, label, 26, ink)
        for index, row in enumerate(rows):
            yy, x = y + index * 40, 182
            if row is None:
                _text(draw, x, yy + 5, "未知", 26, muted)
                continue
            if not row:
                _text(draw, x, yy + 5, "无", 26, muted)
            for name, text, width in row:
                base.alpha_composite(render_attribute_icon(name, 36), (x, yy))
                _text(draw, x + 41, yy + 5, text, 26, ink)
                x += width
        y += len(rows) * 40 + 4


def _stats_band(base, item, kind):
    draw = ImageDraw.Draw(base)
    ink, muted, rule = INKS[kind]
    stats = _stats(item)
    draw.line((91, 1509, 989, 1509), fill=rule, width=1)
    for index, name in enumerate(("HP", "特攻", "特防", "速度")):
        x = 93 + index * 175
        _text(draw, x, 1524, f"{name} {stats.get(name, '—')}", 26, ink)
    _text(draw, 795, 1524, f"总和 {item.bst}", 26, ink)
    draw.line((91, 1563, 989, 1563), fill=rule, width=2)
    combat = f"ATK / {stats.get('攻击', '—')}   DEF / {stats.get('防御', '—')}"
    _text(draw, 988 - draw.textlength(combat, font=_title_font(37)), 1576,
          combat, 37, ink, font=_title_font(37))


def _seal(base, kind):
    """Original micro-foil diamond; no publisher logo or copied authenticity mark."""
    draw = ImageDraw.Draw(base)
    x, y, size = 1018, 1640, 19
    dark, metal, light = METALS[kind]
    draw.rectangle((x, y, x + size, y + size), fill=dark, outline=light, width=1)
    for offset in range(3, size - 2, 4):
        color = ((173 + offset * 3, 168 + offset * 2, 204), metal)[kind != "SHINY"]
        draw.line((x + offset, y + 2, x + 2, y + offset), fill=color, width=1)
        draw.line((x + size - 2, y + offset, x + offset, y + size - 2), fill=light, width=1)
    draw.polygon(((x + 9, y + 4), (x + 15, y + 9), (x + 9, y + 15), (x + 4, y + 9)),
                 outline=light)


def _foil_name(base, name, x, y, size, kind):
    """Colour the complete title glyphs with gold or horizontal spectral ink."""
    face = _title_font(size)
    box = face.getbbox(name, anchor="lt")
    width, height = max(1, round(face.getlength(name)) + 8), box[3] + 8
    mask = Image.new("L", (width, height))
    ImageDraw.Draw(mask).text((3, 2), name, font=face, anchor="lt", fill=255)
    edge = Image.new("RGBA", mask.size, (55, 35, 12, 0) if kind == "SSR" else (57, 49, 67, 0))
    edge.putalpha(mask.filter(ImageFilter.MaxFilter(3)))
    base.alpha_composite(edge, (x - 3, y - 2))
    if kind == "SSR":
        stops = ((174, 112, 31), (255, 233, 139), (242, 205, 90),
                 (179, 123, 31), (253, 225, 125))
        strip = Image.new("RGBA", (1, height))
        values = []
        for yy in range(height):
            phase = yy / max(1, height - 1) * (len(stops) - 1)
            pos = min(len(stops) - 2, int(phase))
            values.append(tuple(round(a + (b - a) * (phase - pos))
                                for a, b in zip(stops[pos], stops[pos + 1])) + (255,))
        strip.putdata(values)
    else:
        stops = ((153, 91, 24), (39, 116, 58), (13, 116, 126),
                 (52, 77, 172), (136, 53, 154), (170, 48, 96), (159, 97, 23))
        if size < 48:
            stops = tuple(tuple(round(channel * .87) for channel in color) for color in stops)
        strip = Image.new("RGBA", (width, 1))
        values = []
        for xx in range(width):
            phase = xx / max(1, width - 1) * (len(stops) - 1)
            pos = min(len(stops) - 2, int(phase))
            values.append(tuple(round(a + (b - a) * (phase - pos))
                                for a, b in zip(stops[pos], stops[pos + 1])) + (255,))
        strip.putdata(values)
    foil = strip.resize(mask.size)
    foil.putalpha(mask)
    base.alpha_composite(foil, (x - 3, y - 2))


def _reading_frame(base, kind):
    """Thin copper keyline and four tiny corner blocks of the reference frame."""
    draw = ImageDraw.Draw(base)
    box = (61, 1124, 1019, 1623)
    draw.rectangle(box, fill=(113, 69, 39))
    draw.rectangle((64, 1127, 1016, 1620), outline=(226, 175, 94), width=2)
    base.alpha_composite(render_reading_stock(kind, 942, 483), (69, 1132))
    draw = ImageDraw.Draw(base)
    draw.rectangle((68, 1131, 1011, 1616), outline=(90, 79, 66), width=1)
    for x, y in ((58, 1121), (1010, 1121), (58, 1614), (1010, 1614)):
        draw.rectangle((x, y, x + 14, y + 14), fill=(167, 93, 44), outline=(103, 62, 38), width=1)
        draw.rectangle((x + 3, y + 3, x + 11, y + 11), outline=(221, 155, 79), width=1)


def _descriptor(base, item, types, kind, missing):
    draw = ImageDraw.Draw(base)
    ink = INKS[kind][0]
    category = str(item.row.get("category") or "宝可梦")
    gender = str(item.row.get("gender_ratio") or "性别未知").strip()
    if re.fullmatch(r"♂\s*0(?:\.0+)?%\s*♀\s*0(?:\.0+)?%", gender):
        gender = "无性别"
    suffix = f" / {category} / {gender}】"
    size = 31
    x = 89
    icon_size = 36
    text_width = 898 - 31 - len(types) * (icon_size + 5)
    while size > 24 and draw.textlength(suffix, font=_title_font(size)) > text_width:
        size -= 1
    if draw.textlength(suffix, font=_title_font(size)) > text_width:
        missing.append(f"分类：{category}；性别：{gender}")
    _text(draw, x, 1150, "【", size, ink, font=_title_font(size))
    x += 31
    for type_name in types:
        base.alpha_composite(render_attribute_icon(type_name, icon_size), (x, 1146))
        x += icon_size + 5
    _text(draw, x, 1150, suffix, size, ink, font=_title_font(size), width=989 - x)


def _quiet_rail(base, top, bottom, kind):
    """Transparent, feathered ink glaze keeps small lettering on its stock."""
    if kind not in {"C", "R", "SR"}:
        return
    color, opacity = ((243, 204, 159), 100) if kind == "C" else ((42, 16, 38), 65)
    strip = Image.new("RGBA", (1, bottom - top))
    strip.putdata([(*color, round(opacity * min(1, y / 4, (bottom - top - 1 - y) / 4)))
                   for y in range(bottom - top)])
    base.alpha_composite(strip.resize((CARD_W - 42, bottom - top)), (21, top))


def _data_rail(base, item, kind, missing):
    _quiet_rail(base, 1070, 1117, kind)
    draw = ImageDraw.Draw(base)
    ink = (249, 237, 231) if kind in {"R", "SR", "SSR"} else (29, 21, 30)
    size = 26
    metadata = " · ".join(str(item.row.get(key) or "") for key in ("height", "weight")
                          if item.row.get(key))
    right_width = draw.textlength(metadata, font=_font(size))
    ability = str(item.row.get("abilities") or "—")
    if item.row.get("hidden_abilities"):
        ability += f"（隐藏：{item.row['hidden_abilities']}）"
    text = "特性 " + ability
    left_width = 894 - right_width - (24 if metadata else 0)
    if draw.textlength(text, font=_font(size)) > left_width:
        missing.append("特性：" + ability)
    _text(draw, 91, 1082, text, size, ink, width=left_width)
    _text(draw, 988 - right_width, 1082, metadata, size, ink)


def _footer(base, item, kind, missing):
    _quiet_rail(base, 1628, 1661, kind)
    draw = ImageDraw.Draw(base)
    ink = (249, 237, 231) if kind in {"R", "SR", "SSR"} else (29, 21, 30)
    catch = str(item.row.get("catch_rate") or "未知")
    eggs = str(item.row.get("egg_groups") or "未知")
    left = f"捕获度 {catch}    蛋群 {eggs}"
    serial = f"#{item.dex_raw}"
    size = 26
    right_width = draw.textlength(serial, font=_font(size))
    available = 931 - right_width - 24
    if draw.textlength(left, font=_font(size)) > available:
        missing.append(f"捕获度：{catch}；蛋群：{eggs}")
    _text(draw, 55, 1631, left, size, ink, width=available)
    _text(draw, 995 - right_width, 1631, serial, size, ink)
    _seal(base, kind)


def _card(item: DrawItem, loader: Callable | None):
    kind, types = _kind(item), _types(item)
    ink, muted, _rule = INKS[kind]
    base = render_stock(kind, CARD_W, CARD_H)
    _outer_frame(base, kind)
    title = base.crop((61, 61, 1019, 152))
    draw = ImageDraw.Draw(base)
    _bevel(draw, (54, 54, 1026, 159), kind, 5, recessed=True)
    if kind in {"C", "R"}:
        title.alpha_composite(Image.new("RGBA", title.size, (43, 20, 30, 74)))
    elif kind == "SR":
        title.alpha_composite(Image.new("RGBA", title.size, (228, 208, 239, 110)))
    base.alpha_composite(title, (61, 61))
    draw = ImageDraw.Draw(base)
    size, lines = _name_layout(draw, item.name)
    text_top = 63 + (87 - len(lines) * (size + 4)) // 2 + 4
    title_ink = (252, 236, 216) if kind == "C" else TITLE_INKS[kind]
    for index, line in enumerate(lines):
        y = text_top + index * (size + 4)
        if kind in {"SSR", "SHINY"}:
            _foil_name(base, line, 78, y, size, kind)
        else:
            _text(draw, 78, y, line, size, title_ink, font=_title_font(size))
    for index, type_name in enumerate(types):
        x = 925 - (len(types) - index - 1) * 83
        base.alpha_composite(render_attribute_icon(type_name, 78), (x, 70))
    stars = {"C": 2, "R": 4, "SR": 6, "SSR": 8, "SHINY": 12}[kind]
    for index in range(stars):
        _rarity_star(base, 948 - index * 51, 172, kind)

    # The near-square illustration stays within the metallic picture mould.
    draw = ImageDraw.Draw(base)
    picture = (78, 225, 1002, 1062)
    draw.rectangle((74, 221, 1006, 1066), fill=(56, 52, 53))
    for inset in range(11):
        t = inset / 10
        hi = tuple(round(a + (b - a) * t) for a, b in zip((192, 194, 187), (103, 112, 115)))
        lo = tuple(round(a + (b - a) * t) for a, b in zip((37, 44, 51), (91, 99, 105)))
        l, t0, r, b = picture
        draw.line((l + inset, b - inset, l + inset, t0 + inset, r - inset, t0 + inset), fill=hi)
        draw.line((r - inset, t0 + inset, r - inset, b - inset, l + inset, b - inset), fill=lo)
    base.alpha_composite(render_art_scene(kind, types[0], 898, 811), (91, 238))
    _portrait(base, item, loader, (111, 258, 858, 771))

    missing = []
    _data_rail(base, item, kind, missing)
    _reading_frame(base, kind)
    _descriptor(base, item, types, kind, missing)
    draw = ImageDraw.Draw(base)
    layout = _matchup_layout(draw, item)
    matchup_height = sum(len(rows) * 40 + 4 for _label, rows in layout)
    matchup_top = 1500 - matchup_height
    y = 1202
    evolution = "；".join(dict.fromkeys(part.strip() for part in
        _split_top_level(str(item.row.get("evolution") or ""), "；") if part.strip()))
    has_evolution = evolution and evolution not in {item.name, str(item.row.get("name_zh") or "")}
    # A short Pokédex passage leads the original-style description box.
    profile = str(item.row.get("profile") or "暂无图鉴摘要")
    reserve = 53 if has_evolution else 15
    cap = min(4, max(1, (matchup_top - y - reserve) // 38))
    y, _ = _paragraph(draw, profile, y, size=29, cap=cap, ink=ink)
    if has_evolution:
        cap = min(2, max(0, (matchup_top - y - 12) // 35))
        y, overflow = _paragraph(draw, "进化：" + evolution, y + 7,
                                 size=26, cap=cap, ink=muted)
        if overflow:
            missing.append("进化：" + evolution)
    _matchups(base, layout, matchup_top, kind)
    _stats_band(base, item, kind)
    _footer(base, item, kind, missing)
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
