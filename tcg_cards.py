"""TCG-inspired printed cards, using the plugin's actual Pokédex/draw data.

The cards keep the familiar portrait proportion, name/HP rail, artwork window,
energy medallions, fine print and rarity marks. They do not use scanned cards,
TCG move data, or change the collection/game rules.
"""

from __future__ import annotations

import io
import math
import re
from collections.abc import Callable, Sequence
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

try:
    from .card_renderer import load_font
    from .gacha import DrawItem, rarity_label
    from .tcg_materials import render_art_backdrop, render_print_surface
    from .tcg_symbols import render_energy_icon
    from .card_matchups import matchup_groups
except ImportError:
    from card_renderer import load_font
    from gacha import DrawItem, rarity_label
    from tcg_materials import render_art_backdrop, render_print_surface
    from tcg_symbols import render_energy_icon
    from card_matchups import matchup_groups


WIDTH = 1080
CARD_W, CARD_H = 900, 1500
INK = (36, 33, 27)
MUTED = (100, 92, 73)
PAPER = (255, 252, 232)
STAT_NAMES = ("HP", "攻击", "防御", "特攻", "特防", "速度")
STAT_INKS = ((239, 145, 48), (199, 164, 48), (76, 142, 191),
             (60, 159, 124), (150, 105, 173), (121, 104, 67))

# Ink belongs to the stock: dark over paper/silver, warm light over deep foil.
PRINT_INKS = {
    "C": ((42, 34, 23), (71, 57, 39), (163, 125, 64), (170, 47, 40)),
    "R": ((18, 27, 34), (28, 39, 48), (95, 130, 152), (40, 64, 82)),
    "SR": ((252, 247, 230), (231, 222, 244), (132, 123, 171), (205, 161, 230)),
    "SSR": ((254, 243, 209), (235, 211, 160), (167, 129, 67), (225, 178, 78)),
    "SHINY": ((37, 31, 61), (53, 43, 78), (145, 124, 168), (102, 63, 143)),
}


@lru_cache(maxsize=48)
def _font(size):
    return load_font(size)


def _kind(item):
    return "SHINY" if item.shiny else item.rarity if item.rarity in {"C", "R", "SR", "SSR"} else "C"


def _types(item):
    return tuple(part.strip() for part in str(item.types or "一般").split("/") if part.strip()) or ("一般",)


def _stats(item):
    match = re.search(r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)",
                      str(item.row.get("stats") or ""))
    return dict(zip(STAT_NAMES, map(int, match.groups()))) if match else {}


def _wrap(draw, text, size, width):
    """Measure full text; callers decide how much belongs on the physical card."""
    lines, current = [], ""
    for character in str(text or ""):
        if character == "\n":
            lines.append(current)
            current = ""
        elif current and draw.textlength(current + character, font=_font(size)) > width:
            if character in "，。；：、！？）)]":
                # Keep a closing mark with the preceding characters.
                tail, current = current[-1:], current[:-1]
                lines.append(current)
                current = tail + character
            else:
                lines.append(current)
                current = character
        else:
            current += character
    if current or not lines:
        lines.append(current)
    return tuple(lines)


def _line(draw, x, y, text, size=26, ink=INK, width=None, *, bold=False, center=False):
    value = str(text)
    if width is not None:
        while value and draw.textlength(value, font=_font(size)) > width:
            value = value[:-2] + "…" if len(value) > 2 else ""
    if center and width is not None:
        x += (width - draw.textlength(value, font=_font(size))) / 2
    draw.text((round(x), round(y)), value, font=_font(size), fill=ink, anchor="lt",
              stroke_width=1 if bold else 0, stroke_fill=ink)


def _take_lines(draw, text, size, width, count):
    """Measure only the visible prefix, preserving unmeasured continuation text."""
    text, lines, current = str(text or ""), [], ""
    for index, character in enumerate(text):
        if character == "\n":
            lines.append(current)
            current = ""
            if len(lines) == count:
                return tuple(lines), text[index + 1:]
        elif current and draw.textlength(current + character, font=_font(size)) > width:
            if character in "，。；：、！？）)]":
                tail, current = current[-1:], current[:-1]
                lines.append(current)
                if len(lines) == count:
                    return tuple(lines), tail + text[index:]
                current = tail + character
            else:
                lines.append(current)
                if len(lines) == count:
                    return tuple(lines), text[index:]
                current = character
        else:
            current += character
    if current or not lines:
        lines.append(current)
    return tuple(lines), ""


def _paragraph(draw, x, y, lines, size=26, ink=INK, step=None):
    step = step or size + 9
    for index, line in enumerate(lines):
        _line(draw, x, y + index * step, line, size, ink)
    return y + len(lines) * step


def _limited(draw, lines, count, size, width):
    if len(lines) <= count:
        return tuple(lines)
    values = list(lines[:count])
    tail = values[-1]
    while tail and draw.textlength(tail + "…", font=_font(size)) > width:
        tail = tail[:-1]
    values[-1] = tail + "…"
    return tuple(values)


def _gradient(width, height, stops):
    strip = Image.new("RGBA", (1, height))
    pixels, cursor = [], 0
    for y in range(height):
        position = y / max(1, height - 1)
        while cursor + 1 < len(stops) - 1 and position > stops[cursor + 1][0]:
            cursor += 1
        a, b = stops[cursor], stops[cursor + 1]
        amount = (position - a[0]) / max(.001, b[0] - a[0])
        pixels.append(tuple(round(left + (right - left) * amount) for left, right in zip(a[1], b[1])))
    strip.putdata(pixels)
    return strip.resize((width, height))


def _frame(base, kind):
    gold = kind in {"C", "SSR"}
    if kind == "C":
        stops = ((0, (255, 226, 75, 255)), (.25, (232, 178, 32, 255)),
                 (.5, (255, 222, 57, 255)), (.82, (223, 162, 19, 255)), (1, (255, 232, 113, 255)))
    elif kind == "SSR":
        stops = ((0, (255, 235, 172, 255)), (.12, (143, 96, 29, 255)),
                 (.29, (251, 212, 119, 255)), (.52, (193, 137, 49, 255)),
                 (.77, (255, 241, 171, 255)), (1, (156, 105, 30, 255)))
    elif kind == "SHINY":
        stops = ((0, (253, 249, 255, 255)), (.12, (153, 159, 188, 255)),
                 (.26, (206, 245, 244, 255)), (.43, (227, 198, 238, 255)),
                 (.58, (255, 244, 204, 255)), (.74, (162, 205, 218, 255)),
                 (.88, (221, 189, 221, 255)), (1, (253, 252, 250, 255)))
    else:
        stops = ((0, (247, 249, 246, 255)), (.11, (120, 127, 129, 255)),
                 (.26, (225, 227, 226, 255)), (.48, (158, 166, 168, 255)),
                 (.68, (254, 252, 248, 255)), (.84, (133, 139, 141, 255)), (1, (242, 245, 241, 255)))
    edge = _gradient(CARD_W, CARD_H, stops)
    mask = Image.new("L", base.size)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=42, fill=255)
    draw.rounded_rectangle((30, 30, CARD_W - 31, CARD_H - 31), radius=21, fill=0)
    base.paste(edge, (0, 0), mask)
    draw = ImageDraw.Draw(base)
    dark = (128, 88, 26) if gold else (100, 111, 119)
    light = (255, 245, 185) if gold else (255, 255, 253)
    for inset, color, line_width in ((3, dark, 2), (7, light, 2), (23, dark, 2), (28, light, 2), (32, dark, 1)):
        draw.rounded_rectangle((inset, inset, CARD_W - inset - 1, CARD_H - inset - 1),
                               radius=max(12, 42 - inset), outline=color, width=line_width)
    # The three corner reflections emphasize the bevel without a flat glow halo.
    for offset in (10, 13):
        draw.arc((offset, offset, 100, 100), 184, 267, fill=light, width=2)
        draw.arc((CARD_W - 101, CARD_H - 101, CARD_W - offset, CARD_H - offset), 3, 88, fill=light, width=2)


def _paste_art(base, item, loader, box, kind):
    x, y, width, height = map(round, box)
    row = dict(item.row, _artwork_shiny=True) if item.shiny else item.row
    image = loader(row) if loader else None
    if isinstance(image, bytes):
        with Image.open(io.BytesIO(image)) as opened:
            image = opened.convert("RGBA")
    if image is not None:
        image = image.convert("RGBA")
        bounds = image.getchannel("A").getbbox()
        image = image.crop(bounds) if bounds else None
    if image is None:
        draw = ImageDraw.Draw(base)
        icon = render_energy_icon(_types(item)[0], 118)
        base.alpha_composite(icon, (x + (width - 118) // 2, y + (height - 118) // 2 - 28))
        caption_y = round(y + height / 2 + 58)
        caption = Image.new("RGBA", base.size)
        cx = x + (width - 230) // 2
        ImageDraw.Draw(caption).rounded_rectangle((cx, caption_y - 9, cx + 230, caption_y + 35),
                                                  radius=11, fill=(255, 250, 232, 230))
        base.alpha_composite(caption)
        _line(ImageDraw.Draw(base), x, caption_y, "立绘暂未缓存", 27, INK, width, center=True)
        return
    # Fill the portrait box after trimming alpha padding, including official
    # artwork smaller than the box. Limit enlargement of tiny fallback sprites.
    scale = min(3.0, width / image.width, height / image.height)
    image = image.resize((max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                         Image.Resampling.LANCZOS)
    tx, ty = x + (width - image.width) // 2, y + (height - image.height) // 2
    padding = 30
    alpha = Image.new("L", (image.width + padding * 2, image.height + padding * 2))
    alpha.paste(image.getchannel("A"), (padding, padding))
    shadow = Image.new("RGBA", alpha.size, (22, 18, 14, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(9)).point(lambda value: round(value * .35)))
    base.alpha_composite(shadow, (tx - padding + 3, ty - padding + 9))
    if kind in {"SSR", "SHINY"}:
        rim = Image.new("RGBA", alpha.size, (255, 237, 181, 0) if kind == "SSR" else (255, 255, 253, 0))
        rim.putalpha(alpha.filter(ImageFilter.MaxFilter(5)).point(lambda value: round(value * .85)))
        base.alpha_composite(rim, (tx - padding, ty - padding))
    base.alpha_composite(image, (tx, ty))


def _rarity_mark(draw, x, y, kind, color=INK):
    if kind == "C":
        draw.ellipse((x, y + 5, x + 15, y + 20), fill=color)
    elif kind == "R":
        draw.polygon(((x + 9, y), (x + 18, y + 12), (x + 9, y + 25), (x, y + 12)), fill=color)
    else:
        count = 3 if kind == "SSR" else 2 if kind == "SR" else 1
        for index in range(count):
            cx, cy = x + 11 + index * 24, y + 12
            points = []
            for vertex in range(10):
                angle = -math.pi / 2 + vertex * math.pi / 5
                radius = 11 if vertex % 2 == 0 else 4.8
                points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
            draw.polygon(points, fill=color)
        if kind == "SHINY":
            draw.line((x + 11, y - 4, x + 11, y + 28), fill=color, width=2)
            draw.line((x - 5, y + 12, x + 27, y + 12), fill=color, width=2)


def _name_layout(draw, name):
    # A normal form name should shrink before taking a second artwork row.
    for size in range(62, 35, -2):
        if draw.textlength(name, font=_font(size)) <= 530:
            return size, (name,)
    for size in range(48, 31, -2):
        lines = _wrap(draw, name, size, 530)
        if len(lines) <= 2:
            return size, lines
    return 32, _wrap(draw, name, 32, 530)


def _matchup_layout(draw, groups):
    """Measure icon/name/multiplier tokens; no type is lost to fixed row caps."""
    output = []
    for key, label in (("weak", "弱点"), ("resist", "抗性"), ("immune", "免疫")):
        entries = groups.get(key, ())
        if key == "immune" and not entries:
            continue
        rows, current, used = [], [], 0
        for name, multiplier in entries:
            text = f"{name} ×{multiplier:g}"
            width = 39 + round(draw.textlength(text, font=_font(24))) + 16
            if current and used + width > 685:
                rows.append(tuple(current))
                current, used = [], 0
            current.append((name, text, width))
            used += width
        rows.append(tuple(current))
        output.append((key, label, tuple(rows)))
    return tuple(output)


def _draw_matchups(base, layout, top, kind):
    draw = ImageDraw.Draw(base)
    ink, muted, rule, _accent = PRINT_INKS[kind]
    y = top
    for key, label, rows in layout:
        height = len(rows) * 40 + 12
        # Etched rules and small energy stamps stay on the continuous print stock.
        draw.line((61, y, 839, y), fill=rule, width=1)
        _line(draw, 65, y + 14, label, 24, ink, bold=True)
        for index, row in enumerate(rows):
            yy, x = y + 9 + index * 40, 149
            if not row:
                _line(draw, x + 2, yy + 5, "无", 24, muted)
            for name, text, width in row:
                base.alpha_composite(render_energy_icon(name, 32), (x, yy + 1))
                _line(draw, x + 39, yy + 5, text, 24, ink)
                x += width
        y += height + 8


def _gender(value):
    value = str(value or "").strip()
    return {"♂0%♀0%": "无性别", "♂0%♀100%": "仅雌性", "♂100%♀0%": "仅雄性"}.get(value.replace(" ", ""), value)


def _title_finish(base, kind, end):
    if kind not in {"SR", "SSR"}:
        return
    height = max(170, end + 24)
    shade = (18, 18, 36) if kind == "SR" else (18, 20, 23)
    veil = Image.new("RGBA", (1, height))
    veil.putdata([shade + (round(155 * min(1, max(0, (height - y) / 44))),)
                  for y in range(height)])
    base.alpha_composite(veil.resize((CARD_W, height)), (0, 0))


def _card(item, loader):
    kind, types = _kind(item), _types(item)
    full = kind in {"SR", "SSR", "SHINY"}
    ink, muted, rule, accent = PRINT_INKS[kind]
    base = render_print_surface(kind, types[0], CARD_W, CARD_H)
    draw = ImageDraw.Draw(base)
    name_size, name_lines = _name_layout(draw, item.name)
    name_y = 61
    title_end = name_y + len(name_lines) * (name_size + 8)
    art_top = max(169, title_end + 18)
    art_bottom = 766 if full else 744
    if art_top >= art_bottom - 390:
        raise ValueError("宝可梦名字超出实体卡片的标题空间")

    if not full:
        window = (57, art_top, 843, art_bottom)
        backdrop = render_art_backdrop(kind, types[0], window[2] - window[0], window[3] - window[1])
        base.alpha_composite(backdrop, window[:2])
        draw = ImageDraw.Draw(base)
        if kind != "SHINY":
            bevel = (151, 109, 37) if kind == "C" else (126, 135, 139)
            draw.rectangle((54, art_top - 3, 846, art_bottom + 3), outline=bevel, width=3)
            draw.rectangle((58, art_top + 1, 842, art_bottom - 1), outline=(255, 249, 219), width=2)
        _paste_art(base, item, loader, (87, art_top + 15, 726, art_bottom - art_top - 29), kind)
    else:
        backdrop = render_art_backdrop(kind, types[0], 830, art_bottom - 95)
        mask = Image.new("L", backdrop.size)
        ImageDraw.Draw(mask).rectangle((0, 30, mask.width, mask.height - 57), fill=232)
        backdrop.putalpha(mask.filter(ImageFilter.GaussianBlur(36)))
        base.alpha_composite(backdrop, (35, 95))
        _paste_art(base, item, loader, (52, art_top + 3, 796, art_bottom - art_top - 25), kind)

    _title_finish(base, kind, title_end)
    # The lower surface is printed directly on the same paper or foil stock.
    # Soft material transitions are generated by tcg_materials; no white box
    # is pasted over the illustration and no backdrop changes at text blocks.
    _frame(base, kind)
    draw = ImageDraw.Draw(base)
    title_ink = ink
    title_edge = (23, 21, 33) if full else ink
    for index, name_line in enumerate(name_lines):
        draw.text((54, name_y + index * (name_size + 7)), name_line, font=_font(name_size),
                  fill=title_ink, anchor="lt", stroke_width=2 if full else 1, stroke_fill=title_edge)
    stats = _stats(item)
    _line(draw, 605, 47, "种族HP", 22, title_ink)
    hp = str(stats.get("HP", "—"))
    hp_x = 602 + (110 - draw.textlength(hp, font=_font(57))) / 2
    draw.text((round(hp_x), 81), hp, font=_font(57), fill=title_ink, anchor="lt",
              stroke_width=2 if full else 1, stroke_fill=title_edge)
    icon_size = 76 if len(types) == 1 else 61
    for index, type_name in enumerate(types[:2]):
        icon_x = 775 if len(types) == 1 else 713 + index * 68
        base.alpha_composite(render_energy_icon(type_name, icon_size), (icon_x, 55 if len(types) == 1 else 61))

    missing = []
    metadata = " · ".join(str(item.row.get(key) or "").strip()
                          for key in ("category", "height", "weight") if item.row.get(key))
    _line(draw, 61, art_bottom + 23, metadata or "宝可梦资料", 24, muted, 778, center=True)
    if metadata and draw.textlength(metadata, font=_font(24)) > 778:
        missing.append(metadata)
    draw.line((61, art_bottom + 58, 839, art_bottom + 58), fill=rule, width=1)
    ability_y = art_bottom + 74
    abilities = str(item.row.get("abilities") or "—")
    hidden = str(item.row.get("hidden_abilities") or "")
    if hidden:
        abilities += f"（隐藏：{hidden}）"
    ability_lines = _wrap(draw, abilities, 26, 650)
    ability_cap = min(3, len(ability_lines))
    shown = _limited(draw, ability_lines, ability_cap, 26, 650)
    badge_fill = accent if kind in {"C", "R", "SHINY"} else (92, 54, 122) if kind == "SR" else (213, 165, 72)
    badge_ink = (255, 250, 231) if kind != "SSR" else (29, 25, 17)
    draw.rounded_rectangle((61, ability_y, 157, ability_y + 35), radius=17, fill=badge_fill)
    _line(draw, 80, ability_y + 6, "特性", 24, badge_ink, bold=True)
    _paragraph(draw, 176, ability_y + 2, shown, 26, ink, 34)
    if len(ability_lines) > ability_cap:
        missing.append(f"特性：{abilities}")
    stat_y = ability_y + ability_cap * 34 + 23
    draw.line((61, stat_y - 10, 839, stat_y - 10), fill=rule, width=1)
    entries = [(name, stats.get(name, "—")) for name in STAT_NAMES[1:]] + [("总和", item.bst)]
    for index, (name, value) in enumerate(entries):
        x = 62 + index * 132
        if index:
            draw.line((x - 9, stat_y + 3, x - 9, stat_y + 72), fill=rule, width=1)
        _line(draw, x, stat_y + 2, name, 22, muted, 110, center=True)
        _line(draw, x, stat_y + 32, value, 36, ink, 110, center=True, bold=True)
        draw.line((x + 9, stat_y + 79, x + 100, stat_y + 79), fill=rule, width=2)
        maximum = 750 if name == "总和" else 180
        ratio = min(1, max(0, float(value) / maximum)) if isinstance(value, (int, float)) else 0
        if ratio:
            draw.line((x + 9, stat_y + 79, x + 9 + max(6, 91 * ratio), stat_y + 79), fill=STAT_INKS[index], width=4)

    layout = _matchup_layout(draw, matchup_groups(item.row, item.types))
    matchup_height = sum(len(rows) * 40 + 20 for _key, _label, rows in layout) - 8
    footer_line = CARD_H - 84
    matchup_top = footer_line - 14 - matchup_height
    # Practical species data takes priority over the intentionally short flavour text.
    basic = " · ".join(part for part in (
        f"捕获度：{item.row['catch_rate']}" if item.row.get("catch_rate") else "",
        f"蛋群：{item.row['egg_groups']}" if item.row.get("egg_groups") else "",
        f"性别：{_gender(item.row['gender_ratio'])}" if item.row.get("gender_ratio") else "",
    ) if part)
    basic_y = stat_y + 103
    basic_lines = _wrap(draw, basic, 24, 775) if basic else ()
    basic_cap = max(0, min(4, (matchup_top - basic_y - 17) // 33))
    if basic_lines and basic_cap:
        _paragraph(draw, 63, basic_y, _limited(draw, basic_lines, basic_cap, 24, 775), 24, ink, 33)
    if len(basic_lines) > basic_cap:
        missing.append(basic)
    cursor = basic_y + min(len(basic_lines), basic_cap) * 33 + 15
    evolution = str(item.row.get("evolution") or "").strip()
    if evolution and evolution not in {item.name, str(item.row.get("name_zh") or "")}:
        evolution_lines = _wrap(draw, f"进化：{evolution}", 24, 775)
        if len(evolution_lines) <= 3 and cursor + len(evolution_lines) * 33 + 46 <= matchup_top:
            cursor = _paragraph(draw, 63, cursor, evolution_lines, 24, ink, 33) + 15
        else:
            missing.append(f"进化：{evolution}")
    profile_capacity = max(0, (matchup_top - cursor - 17) // 33)
    if profile_capacity:
        profile = str(item.row.get("profile") or "").strip() or "暂无图鉴摘要"
        profile_lines, rest = _take_lines(draw, profile, 24, 773, min(5, profile_capacity))
        if rest:
            profile_lines = _limited(draw, (*profile_lines, ""), len(profile_lines), 24, 773)
        _paragraph(draw, 63, cursor, profile_lines, 24, muted, 33)
    _draw_matchups(base, layout, matchup_top, kind)

    draw = ImageDraw.Draw(base)
    draw.line((58, footer_line, 842, footer_line), fill=rule, width=1)
    _line(draw, 58, CARD_H - 66, f"#{item.dex_raw}", 25, ink, bold=True)
    label = f"{item.rarity} · {rarity_label(item.rarity)}"
    if item.shiny:
        label = f"闪光 · {label}"
    label_width = round(draw.textlength(label, font=_font(23)))
    mark_width = 72 if kind == "SSR" else 48 if kind == "SR" else 28
    mark_x = 843 - mark_width
    _line(draw, mark_x - label_width - 12, CARD_H - 65, label, 23, ink)
    _rarity_mark(draw, mark_x, CARD_H - 67, kind, ink)
    mask = Image.new("L", base.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=42, fill=255)
    base.putalpha(mask)
    return base, tuple(missing)


def _finish(base):
    output = io.BytesIO()
    base.convert("RGB").save(output, "JPEG", quality=95, optimize=True)
    return output.getvalue()


def _collect_details(item, missing, details_out):
    if details_out is not None and missing:
        details_out.append(f"#{item.dex_raw} {item.name}：" + "；".join(missing))


def _single(item, loader, details_out):
    card, missing = _card(item, loader)
    _collect_details(item, missing, details_out)
    return _finish(card)


def _grid(items, loader, details_out):
    tile_width, gap = 506, 22
    tile_height = round(CARD_H * tile_width / CARD_W)
    rows = math.ceil(len(items) / 2)
    base = Image.new("RGBA", (WIDTH, rows * (tile_height + gap) + gap), (237, 236, 229, 255))
    for index, item in enumerate(items):
        row, column = divmod(index, 2)
        count = min(2, len(items) - row * 2)
        left = (WIDTH - count * tile_width - (count - 1) * gap) // 2
        x, y = left + column * (tile_width + gap), gap + row * (tile_height + gap)
        card, missing = _card(item, loader)
        _collect_details(item, missing, details_out)
        base.alpha_composite(card.resize((tile_width, tile_height), Image.Resampling.LANCZOS), (x, y))
    return _finish(base)


def render_tcg_draw(
    items: Sequence[DrawItem], *, nickname: str, remaining: int, daily_limit: int,
    dust: int, pity_ssr: int, ssr_pity_after: int, image_loader: Callable | None = None,
    details_out: list[str] | None = None,
) -> bytes:
    """Return a physical card or card spread; transient draw notices belong to chat."""
    if not items:
        raise ValueError("抽卡卡片至少需要一个结果")
    return _single(items[0], image_loader, details_out) if len(items) == 1 else _grid(items, image_loader, details_out)
