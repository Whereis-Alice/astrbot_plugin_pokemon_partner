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
    from .yugioh_materials import render_stock, render_art_scene, render_reading_stock, apply_print_finish
    from .yugioh_fonts import load_card_font
    from .yugioh_foil import render_foil_name
    from .yugioh_frames import paint_frame
except ImportError:
    from card_renderer import _split_top_level
    from card_matchups import matchup_groups, TYPE_ORDER
    from gacha import DrawItem
    from yugioh_symbols import render_attribute_icon, render_level_star
    from yugioh_materials import render_stock, render_art_scene, render_reading_stock, apply_print_finish
    from yugioh_fonts import load_card_font
    from yugioh_foil import render_foil_name
    from yugioh_frames import paint_frame


CARD_W, CARD_H = 1080, 1680
# The visible paper, rather than the frame's shadow, defines each text rail.
READING_BOX = (60, 1123, 1020, 1600)
READING_PAPER = (76, 1140, 1004, 1583)
STATS_RULE_Y = 1533
FOOTER_BOTTOM = 1644
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


@lru_cache(maxsize=48)
def _name_font(size: int, text: str = ""):
    return load_card_font(size, "name", text=text)


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
    return text


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
    paint_frame(base, 'outer', (0, 0, CARD_W, CARD_H), kind)


def _rarity_star(base: Image.Image, x: int, y: int, kind: str):
    base.alpha_composite(render_level_star(64), (x, y))


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
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(2)).point(lambda a: round(a * .12)))
    base.alpha_composite(shadow, (tx - padding + 1, ty - padding + 2))
    base.alpha_composite(art, (tx, ty))


def _name_layout(draw, name: str):
    for size in range(66, 35, -2):
        if draw.textlength(name, font=_name_font(size, name)) <= 748:
            return size, (name,)
    for size in range(38, 27, -2):
        # Long names keep the same title face at every size and on both lines.
        lines, rest = _wrap(draw, name, size, 748, 2, font=_name_font(size, name))
        if not rest:
            return size, lines
    raise ValueError("完整宝可梦名称超出卡片的标题空间，请使用文本。")


def _matchup_layout(draw, item: DrawItem):
    layout = []
    types = _types(item)
    groups = matchup_groups(item.row, types) if types != ("未知",) else None
    for key, label in (("resist", "抗性"), ("weak", "弱点")):
        if groups is None:
            layout.append((label, (None,)))
            continue
        entries = groups[key]
        rows, current, used = [], [], 0
        for name, multiplier in entries:
            text = f"×{multiplier:g}"
            width = 41 + round(draw.textlength(text, font=_font(26))) + 18
            available = 635 if key == "weak" else 806
            if current and used + width > available:
                rows.append(tuple(current))
                current, used = [], 0
            current.append((name, text, width))
            used += width
        rows.append(tuple(current))
        layout.append((label, tuple(rows)))
    return tuple(layout)


def _matchups(base, layout, top, kind, total):
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
    total_text = f"总和 {total}"
    # Align the bottom-right number with the end of the ATK/DEF rule.
    _text(draw, 988 - draw.textlength(total_text, font=_font(25)), STATS_RULE_Y - 32,
          total_text, 25, ink)


def _stats_band(base, item, kind):
    draw = ImageDraw.Draw(base)
    ink, muted, rule = INKS[kind]
    stats = _stats(item)
    center_y = (STATS_RULE_Y + 2 + READING_PAPER[3]) / 2
    for index, name in enumerate(("HP", "特攻", "特防", "速度")):
        x = 93 + index * 119
        text = f"{name} {stats.get(name, '—')}"
        height = _font(24).getbbox(text, anchor="lt")[3]
        _text(draw, x, center_y - height / 2, text, 24, muted)
    draw.line((91, STATS_RULE_Y, 989, STATS_RULE_Y), fill=rule, width=2)
    combat = f"ATK / {stats.get('攻击', '—')}   DEF / {stats.get('防御', '—')}"
    height = _title_font(34).getbbox(combat, anchor="lt")[3]
    _text(draw, 988 - draw.textlength(combat, font=_title_font(34)), center_y - height / 2,
          combat, 34, ink, font=_title_font(34))


def _foil_name(base, name, x, y, size, kind, *, font=None):
    render_foil_name(base, name, x, y, size, kind, font=font or _name_font(size, name))


def _reading_frame(base, kind):
    paint_frame(base, 'description', READING_BOX, kind)
    left, top, right, bottom = READING_PAPER
    base.alpha_composite(render_reading_stock(kind, right-left, bottom-top), (left, top))


def _descriptor(base, item, types, kind, missing):
    draw = ImageDraw.Draw(base)
    ink = INKS[kind][0]
    category = str(item.row.get('category') or '宝可梦')
    gender = str(item.row.get('gender_ratio') or '性别未知').strip()
    if re.fullmatch(r'♂\s*0(?:\.0+)?%\s*♀\s*0(?:\.0+)?%', gender):
        gender = '无性别'
    groups = matchup_groups(item.row, types) if types != ('未知',) else None
    immunities = groups['immune'] if groups is not None else ()
    label = '免疫未知' if groups is None else '免疫 ' if immunities else '无免疫'
    prefix = f'【{category} / {gender} / {label}'
    size = 28
    while size > 24 and draw.textlength(prefix + '】', font=_title_font(size)) + len(immunities)*34 > 898:
        size -= 1
    available = 858-len(immunities)*34
    suffix = f' / {gender} / {label}'
    while category and draw.textlength('【'+category+suffix, font=_title_font(size)) > available:
        category = category[:-2]+'…' if len(category) > 2 else ''
    prefix = '【'+category+suffix
    x = 89
    prefix = _text(draw, x, 1150, prefix, size, ink, font=_title_font(size), width=available)
    x += round(draw.textlength(prefix, font=_title_font(size)))
    for type_name, _multiplier in immunities:
        base.alpha_composite(render_attribute_icon(type_name, 29), (x+2, 1148))
        x += 34
    _text(draw, min(x, 965), 1150, '】', size, ink, font=_title_font(size))
    return max(1150 + _title_font(size).getbbox(prefix+'】', anchor='lt')[3],
               1177 if immunities else 0)


def _quiet_rail(base, top, bottom, kind):
    # Broad, weak illumination across the paper has no rectangular strip edge.
    if kind not in {'C', 'R', 'SR'}:
        return
    if kind == 'C':
        color, opacity = ((247, 220, 188), 100) if top > 1550 else ((237, 202, 165), 130)
    else:
        color, opacity = (27, 9, 33), 103 if kind == 'SR' else 40
    margin = 12
    height = bottom-top+margin*2
    strip = Image.new('RGBA', (1, height))
    strip.putdata([(*color, round(opacity*math.exp(-((y-height/2)/(height*.45))**4)))
                   for y in range(height)])
    strip = strip.resize((CARD_W-88, height))
    strip = strip.crop((0, 0, strip.width, min(height, CARD_H-44-(top-margin))))
    base.alpha_composite(strip, (44,top-margin))


def _data_rail(base, item, kind, missing):
    _quiet_rail(base, 1082, 1120, kind)
    draw = ImageDraw.Draw(base)
    ink = (253, 242, 233) if kind in {"R", "SR", "SSR"} else (17, 8, 5)
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
    center = (1080 + 1123) / 2
    text_y = center - _font(size).getbbox(text, anchor='lt')[3] / 2
    meta_y = center - _font(size).getbbox(metadata, anchor='lt')[3] / 2
    _text(draw, 91, text_y, text, size, ink, width=left_width)
    _text(draw, 988 - right_width, meta_y, metadata, size, ink)


def _footer(base, item, kind, missing):
    _quiet_rail(base, 1608, 1641, kind)
    draw = ImageDraw.Draw(base)
    ink = (253, 242, 233) if kind in {"R", "SR", "SSR"} else (17, 8, 5)
    catch = str(item.row.get("catch_rate") or "未知")
    eggs = str(item.row.get("egg_groups") or "未知")
    left = f"捕获度 {catch}    蛋群 {eggs}"
    serial = f"#{item.dex_raw}"
    size = 26
    right_width = draw.textlength(serial, font=_font(size))
    available = 931 - right_width - 24
    if draw.textlength(left, font=_font(size)) > available:
        missing.append(f"捕获度：{catch}；蛋群：{eggs}")
    center = (READING_BOX[3] + FOOTER_BOTTOM) / 2
    # Chinese text, parentheses and the short numeric serial have different
    # ink heights. Centre each in the exposed stock beneath the description.
    left_y = center - _font(size).getbbox(left, anchor='lt')[3] / 2
    serial_y = center - _font(size).getbbox(serial, anchor='lt')[3] / 2
    _text(draw, 62, left_y, left, size, ink, width=available)
    _text(draw, 1009 - right_width, serial_y, serial, size, ink)


def _card(item: DrawItem, loader: Callable | None):
    kind, types = _kind(item), _types(item)
    ink, muted, _rule = INKS[kind]
    base = render_stock(kind, CARD_W, CARD_H)
    _outer_frame(base, kind)
    title = base.crop((67, 66, 1012, 147))
    paint_frame(base, 'title', (54, 54, 1026, 159), kind)
    if kind in {'C', 'R'}:
        title.alpha_composite(Image.new('RGBA', title.size, (39, 21, 27, 98 if kind == 'C' else 35)))
    elif kind == 'SR':
        title.alpha_composite(Image.new('RGBA', title.size, (235, 213, 247, 112)))
    base.alpha_composite(title, (67, 66))
    draw = ImageDraw.Draw(base)
    size, lines = _name_layout(draw, item.name)
    name_face = _name_font(size, item.name)
    heights = [name_face.getbbox(line, anchor='lt')[3] for line in lines]
    title_center = 97 if kind == 'SHINY' else (66+147)/2
    text_top = title_center - (sum(heights) + (len(lines)-1)*4) / 2
    title_ink = (252, 236, 216) if kind == "C" else TITLE_INKS[kind]
    for index, line in enumerate(lines):
        y = text_top + sum(heights[:index]) + index * 4
        if kind in {"SSR", "SHINY"}:
            _foil_name(base, line, 78, y, size, kind, font=name_face)
        else:
            _text(draw, 78, y, line, size, title_ink, font=name_face)
    for index, type_name in enumerate(types):
        x = 925 - (len(types) - index - 1) * 83
        base.alpha_composite(render_attribute_icon(type_name, 78), (x, round(title_center-39)))
    stars = {"C": 2, "R": 4, "SR": 6, "SSR": 8, "SHINY": 12}[kind]
    for index in range(stars):
        _rarity_star(base, 930 - index * 68, 172, kind)

    # The supplied picture mould and light field share an actual print texture.
    paint_frame(base, 'picture', (75, 253, 1005, 1080), kind)
    art_box = (96, 274, 888, 785)
    base.alpha_composite(render_art_scene(kind, types[0], 888, 785), (96, 274))
    _portrait(base, item, loader, (114, 292, 852, 749))
    apply_print_finish(base, art_box, kind)

    missing = []
    _data_rail(base, item, kind, missing)
    _reading_frame(base, kind)
    descriptor_bottom = _descriptor(base, item, types, kind, missing)
    draw = ImageDraw.Draw(base)
    layout = _matchup_layout(draw, item)
    matchup_height = sum(len(rows) * 40 + 4 for _label, rows in layout)
    matchup_top = STATS_RULE_Y - 15 - matchup_height
    y = descriptor_bottom + 12
    evolution = "；".join(dict.fromkeys(part.strip() for part in
        _split_top_level(str(item.row.get("evolution") or ""), "；") if part.strip()))
    has_evolution = evolution and evolution not in {item.name, str(item.row.get("name_zh") or "")}
    # A short Pokédex passage leads the original-style description box.
    profile = str(item.row.get("profile") or "暂无图鉴摘要")
    reserve = 49 if has_evolution else 15
    cap = min(7, max(1, (matchup_top - y - reserve) // 37))
    profile_lines, _ = _wrap(draw, profile, 28, 894, cap)
    profile_height = len(profile_lines)*37
    evolution_cap = min(2, max(0, (matchup_top-y-profile_height-12)//35)) if has_evolution else 0
    evolution_lines, _ = _wrap(draw, '进化：'+evolution, 26, 894, evolution_cap) if evolution_cap else ((), '')
    block_height = ((len(profile_lines)-1)*37 + _font(28).getbbox(profile_lines[-1], anchor='lt')[3])
    if evolution_lines:
        block_height = profile_height + 7 + (len(evolution_lines)-1)*35 + _font(26).getbbox(evolution_lines[-1], anchor='lt')[3]
    # Keep the paragraph and its evolution together, with balanced top/bottom
    # breathing room even for species with only a brief description.
    y = descriptor_bottom + max(12, (matchup_top-13-descriptor_bottom-block_height)/2)
    y, _ = _paragraph(draw, profile, y, size=28, cap=cap, ink=ink)
    if has_evolution:
        cap = evolution_cap
        y, overflow = _paragraph(draw, "进化：" + evolution, y + 7,
                                 size=26, cap=cap, ink=muted)
        if overflow:
            missing.append("进化：" + evolution)
    _matchups(base, layout, matchup_top, kind, item.bst)
    _stats_band(base, item, kind)
    _footer(base, item, kind, missing)
    return base, ()


def _encode(base: Image.Image) -> bytes:
    buf = io.BytesIO()
    # Full chroma keeps small coloured type and jewels sharp. Streaming JPEG
    # avoids libjpeg's optimized-buffer failures with high-quality 4:4:4 data.
    base.convert("RGB").save(buf, "JPEG", quality=96, subsampling=0, optimize=False)
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
        if sheet is None:
            return _encode(card)
        row, column = divmod(index, 2)
        sheet.alpha_composite(card.resize((tile_width, tile_height), Image.Resampling.LANCZOS),
                              (12 + column * (tile_width + gap), gap + row * (tile_height + gap)))
    return _encode(sheet)
