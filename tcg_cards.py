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
except ImportError:
    from card_renderer import load_font
    from gacha import DrawItem, rarity_label
    from tcg_materials import render_art_backdrop, render_print_surface
    from tcg_symbols import render_energy_icon


WIDTH = 1080
CARD_W, CARD_H = 900, 1257
INK = (36, 33, 27)
MUTED = (100, 92, 73)
PAPER = (255, 252, 232)
STAT_NAMES = ("HP", "攻击", "防御", "特攻", "特防", "速度")
STAT_INKS = ((239, 145, 48), (199, 164, 48), (76, 142, 191),
             (60, 159, 124), (150, 105, 173), (121, 104, 67))


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
        _line(draw, x, y + height / 2 + 58, "立绘暂未缓存", 27, MUTED, width, center=True)
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
    for size in range(62, 31, -2):
        lines = _wrap(draw, name, size, 572)
        if len(lines) <= 2:
            return size, lines
    return 32, _wrap(draw, name, 32, 572)


def _card(item, loader):
    kind, types = _kind(item), _types(item)
    full = kind in {"SR", "SSR"}
    base = render_print_surface(kind, types[0], CARD_W, CARD_H)
    draw = ImageDraw.Draw(base)
    name_size, name_lines = _name_layout(draw, item.name)
    title_end = 89 + len(name_lines) * (name_size + 7)
    art_top = max(184, title_end + 17)
    art_bottom = 698 if full else 650
    if art_top >= art_bottom - 160:
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
        backdrop = render_art_backdrop(kind, types[0], 830, art_bottom - 125)
        mask = Image.new("L", backdrop.size)
        m = ImageDraw.Draw(mask)
        m.rectangle((0, 40, mask.width, mask.height - 30), fill=240)
        mask = mask.filter(ImageFilter.GaussianBlur(30))
        backdrop.putalpha(mask)
        base.alpha_composite(backdrop, (35, 125))
        _paste_art(base, item, loader, (52, art_top - 4, 796, art_bottom - art_top + 7), kind)

    data_y = 766 if full else 714
    # A translucent print layer keeps the foil visible but isolates small text.
    print_layer = Image.new("RGBA", base.size)
    layer_draw = ImageDraw.Draw(print_layer)
    fill = (255, 248, 203, 229) if kind == "SSR" else (255, 254, 248, 231) if kind == "SHINY" else (255, 253, 230, 235)
    if kind == "C":
        fill = (255, 251, 209, 217)
    if kind == "R":
        fill = (245, 251, 249, 218)
    layer_draw.rounded_rectangle((46, data_y - 59, 854, 1183), radius=15, fill=fill)
    base.alpha_composite(print_layer)
    draw = ImageDraw.Draw(base)
    _frame(base, kind)

    # Name, species HP and energy remain above the illustration, including long forms.
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((54, 43, 186, 74), radius=13, fill=(240, 238, 225), outline=(134, 127, 109), width=1)
    _line(draw, 63, 47, "全国图鉴", 23, INK)
    if item.is_new:
        draw.rounded_rectangle((201, 43, 275, 74), radius=13, fill=(29, 125, 82))
        _line(draw, 214, 47, "NEW", 22, (255, 255, 241), bold=True)
    if item.shiny:
        draw.rounded_rectangle((287, 43, 372, 74), radius=13, fill=(245, 235, 252), outline=(154, 112, 179), width=1)
        _line(draw, 301, 47, "闪光", 22, (103, 57, 132))
    title_ink = (255, 251, 226) if full else INK
    title_edge = (47, 36, 21) if full else INK
    for index, name_line in enumerate(name_lines):
        draw.text((54, 89 + index * (name_size + 7)), name_line, font=_font(name_size),
                  fill=title_ink, anchor="lt", stroke_width=3 if full else 1, stroke_fill=title_edge)
    stats = _stats(item)
    _line(draw, 646, 48, "种族HP", 23, title_ink)
    hp = str(stats.get("HP", "—"))
    hp_x = 642 + (124 - draw.textlength(hp, font=_font(57))) / 2
    draw.text((round(hp_x), 81), hp, font=_font(57), fill=title_ink, anchor="lt",
              stroke_width=2 if full else 1, stroke_fill=title_edge)
    base.alpha_composite(render_energy_icon(types[0], 76), (775, 53))

    metadata = " · ".join(str(item.row.get(key) or "").strip()
                          for key in ("category", "height", "weight") if item.row.get(key))
    _line(draw, 61, data_y - 43, metadata or "宝可梦资料", 24, MUTED, 778, center=True)
    draw.line((61, data_y - 4, 839, data_y - 4), fill=(161, 151, 120), width=1)
    abilities = str(item.row.get("abilities") or "—")
    hidden = str(item.row.get("hidden_abilities") or "")
    if hidden:
        abilities += f"（隐藏：{hidden}）"
    ability_lines = _wrap(draw, abilities, 26, 648)
    ability_cap = min(3, len(ability_lines))
    shown_ability = ability_lines[:ability_cap]
    draw.rounded_rectangle((61, data_y + 2, 160, data_y + 35), radius=16, fill=(176, 44, 42))
    _line(draw, 82, data_y + 7, "特性", 25, (255, 250, 231), bold=True)
    _paragraph(draw, 178, data_y + 3, shown_ability, 26, INK, 34)
    stat_y = data_y + ability_cap * 34 + 29
    draw.line((61, stat_y - 13, 839, stat_y - 13), fill=(142, 128, 98), width=1)
    entries = [(name, stats.get(name, "—")) for name in STAT_NAMES[1:]] + [("总和", item.bst)]
    for index, (name, value) in enumerate(entries):
        row, column = divmod(index, 3)
        x, y = 63 + column * 264, stat_y + row * 77
        _line(draw, x, y + 4, name, 25, INK)
        _line(draw, x + 123, y, value, 36, INK, 105, bold=True, center=True)
        draw.line((x, y + 49, x + 231, y + 49), fill=(201, 195, 169), width=3)
        maximum = 750 if name == "总和" else 180
        amount = min(1, max(0, float(value) / maximum)) if isinstance(value, (int, float)) else 0
        if amount:
            draw.line((x, y + 49, x + max(8, 231 * amount), y + 49), fill=STAT_INKS[index], width=4)

    profile_y = stat_y + 167
    _line(draw, 63, profile_y, "图鉴摘要", 23, MUTED)
    profile_y += 33
    profile = str(item.row.get("profile") or "暂无图鉴摘要")
    capacity = max(1, (1166 - profile_y) // 33)
    profile_lines, overflow = _take_lines(draw, profile, 25, 773, capacity)
    _paragraph(draw, 63, profile_y, profile_lines, 25, INK, 33)
    if len(ability_lines) > ability_cap:
        overflow = f"特性：{abilities}\n" + overflow
    draw.line((58, 1180, 842, 1180), fill=(119, 105, 76), width=1)
    _line(draw, 58, 1196, f"#{item.dex_raw}", 25, INK, bold=True)
    tx = 213
    for name in types:
        icon = render_energy_icon(name, 28)
        base.alpha_composite(icon, (tx, 1194))
        _line(draw, tx + 34, 1197, name, 23, INK)
        tx += 48 + round(draw.textlength(name, font=_font(23)))
    label = f"{item.rarity} · {rarity_label(item.rarity)}"
    label_width = round(draw.textlength(label, font=_font(23)))
    mark_width = 72 if kind == "SSR" else 48 if kind == "SR" else 28
    mark_x = 843 - mark_width
    _line(draw, mark_x - label_width - 12, 1197, label, 23, INK)
    _rarity_mark(draw, mark_x, 1197, kind)
    mask = Image.new("L", base.size)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, CARD_W - 1, CARD_H - 1), radius=42, fill=255)
    base.putalpha(mask)
    return base, overflow


def _details(item):
    row, entries = item.row, []
    for key, label in (("catch_rate", "捕获度"), ("weak_to", "弱点"),
                       ("resist_to", "抗性"), ("immune_to", "免疫")):
        if row.get(key):
            entries.append((f"{label}：{row[key]}", 3))
    gender = str(row.get("gender_ratio") or "")
    gender = {"♂0%♀0%": "无性别", "♂0%♀100%": "仅雌性", "♂100%♀0%": "仅雄性"}.get(gender.replace(" ", ""), gender)
    extra = " · ".join(value for value in (
        f"蛋群：{row['egg_groups']}" if row.get("egg_groups") else "",
        f"性别：{gender}" if gender else "") if value)
    if extra:
        entries.append((extra, 3))
    if row.get("evolution"):
        entries.append((f"进化：{row['evolution']}", 5))
    return entries


def _page(height):
    base = _gradient(WIDTH, height, ((0, (20, 32, 45, 255)), (.55, (32, 46, 58, 255)),
                                     (1, (12, 22, 33, 255))))
    draw = ImageDraw.Draw(base)
    for y in range(0, height, 7):
        draw.line((0, y, WIDTH, y + 60), fill=(45, 59, 72, 32), width=1)
    return base


def _header(base, context):
    draw = ImageDraw.Draw(base)
    draw.rounded_rectangle((40, 28, 1040, 179), radius=22, fill=(18, 30, 42), outline=(116, 135, 147), width=1)
    draw.rounded_rectangle((59, 28, 1021, 34), radius=3, fill=(208, 58, 51))
    _line(draw, 64, 53, "今日宝可梦", 46, (248, 246, 232), bold=True)
    _line(draw, 66, 116, f"训练家 · {context['nickname']}", 24, (187, 206, 217), 497)
    for x, label, value in ((601, "今日剩余次数", f"{context['remaining']} / {context['daily_limit']}"),
                            (826, "星尘余额", str(context['dust']))):
        _line(draw, x, 53, label, 24, (208, 218, 215))
        size = 34
        while size > 22 and draw.textlength(value, font=_font(size)) > 167:
            size -= 2
        _line(draw, x, 88, value, size, (255, 242, 203), 167, bold=True)
    after = context['ssr_pity_after']
    label = f"SSR 保底 {context['pity_ssr']}/{after}" if after else "SSR 保底关闭"
    _line(draw, 602, 135, label, 23, (180, 200, 208), 392)


def _place_card(base, card, x, y):
    padding = 25
    mask = Image.new("L", (card.width + padding * 2, card.height + padding * 2))
    mask.paste(card.getchannel("A"), (padding, padding))
    shadow = Image.new("RGBA", mask.size, (0, 0, 0, 0))
    shadow.putalpha(mask.filter(ImageFilter.GaussianBlur(14)).point(lambda value: round(value * .7)))
    base.alpha_composite(shadow, (x - padding + 3, y - padding + 13))
    base.alpha_composite(card, (x, y))


def _result(base, item, x, y, width, *, small=False):
    draw = ImageDraw.Draw(base)
    note = "新宝可梦已加入图鉴" if item.is_new else f"重复获得 · 转化为 {item.dust} 星尘"
    fill, ink = ((224, 242, 230), (15, 87, 61)) if item.is_new else ((251, 239, 207), (110, 71, 14))
    height, size = (44, 22) if small else (56, 28)
    draw.rounded_rectangle((x, y, x + width, y + height), radius=9, fill=fill)
    _line(draw, x + 16, y + 12, note, size, ink, width - 32, center=small)


def _finish(base):
    output = io.BytesIO()
    base.convert("RGB").save(output, "JPEG", quality=95, optimize=True)
    return output.getvalue()


def _single(item, context, loader):
    card, overflow = _card(item, loader)
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    detail_width = 922 if not overflow else 445
    details = []
    for entry, cap in _details(item):
        details.extend(_limited(measure, _wrap(measure, entry, 28, detail_width), cap, 28, detail_width))
    continuation, rest = _take_lines(measure, overflow, 28, 435, 12) if overflow else ((), "")
    if rest:
        continuation = _limited(measure, (*continuation, ""), 12, 28, 435)
    has_details = bool(details or continuation)
    info_y = 208 + CARD_H + 87
    info_height = 90 + max(len(details), len(continuation)) * 39 if has_details else 0
    footer_y = info_y + info_height + (30 if has_details else 0)
    base = _page(footer_y + 62)
    _header(base, context)
    _place_card(base, card, 90, 208)
    _result(base, item, 90, 208 + CARD_H + 19, 900)
    draw = ImageDraw.Draw(base)
    if has_details:
        draw.rounded_rectangle((58, info_y, 1022, info_y + info_height), radius=15,
                               fill=(247, 245, 231), outline=(150, 143, 119), width=1)
        _line(draw, 79, info_y + 20, "宝可梦资料", 28, INK, bold=True)
        _paragraph(draw, 80, info_y + 64, details, 28, INK, 39)
        if continuation:
            draw.line((562, info_y + 22, 562, info_y + info_height - 22), fill=(185, 174, 148), width=1)
            _line(draw, 584, info_y + 20, "图鉴摘要（续）", 28, INK, bold=True)
            _paragraph(draw, 584, info_y + 64, continuation, 28, INK, 39)
    _line(draw, 58, footer_y, "重复宝可梦自动折算星尘 · 兑换宝可梦 <名称> 可定向获得", 24,
          (187, 202, 208), 964, center=True)
    return _finish(base)


def _grid(items, context, loader):
    tile_width, gap, left = 464, 34, 59
    tile_height = round(CARD_H * tile_width / CARD_W)
    step = tile_height + 82
    rows = math.ceil(len(items) / 2)
    footer_y = 214 + rows * step + 7
    base = _page(footer_y + 62)
    _header(base, context)
    for index, item in enumerate(items):
        row, column = divmod(index, 2)
        x, y = left + column * (tile_width + gap), 212 + row * step
        card, _ = _card(item, loader)
        card = card.resize((tile_width, tile_height), Image.Resampling.LANCZOS)
        _place_card(base, card, x, y)
        _result(base, item, x, y + tile_height + 12, tile_width, small=True)
    draw = ImageDraw.Draw(base)
    _line(draw, 58, footer_y, "重复宝可梦自动折算星尘 · 兑换宝可梦 <名称> 可定向获得", 24,
          (187, 202, 208), 964, center=True)
    return _finish(base)


def render_tcg_draw(
    items: Sequence[DrawItem], *, nickname: str, remaining: int, daily_limit: int,
    dust: int, pity_ssr: int, ssr_pity_after: int, image_loader: Callable | None = None,
) -> bytes:
    """Draw one physical card or a two-column spread without accessing live state."""
    if not items:
        raise ValueError("抽卡卡片至少需要一个结果")
    context = dict(nickname=nickname, remaining=remaining, daily_limit=daily_limit,
                   dust=dust, pity_ssr=pity_ssr, ssr_pity_after=ssr_pity_after)
    return _single(items[0], context, image_loader) if len(items) == 1 else _grid(items, context, image_loader)
