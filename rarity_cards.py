"""Five independently composed daily Pokémon editions; no network or state access."""
from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFilter

try:
    from .card_renderer import load_font
    from .gacha import DrawItem, rarity_label
except ImportError:
    from card_renderer import load_font
    from gacha import DrawItem, rarity_label

WIDTH = 1080
MARGIN = 60
BODY_SIZE = 26
STATS = ("HP", "攻击", "防御", "特攻", "特防", "速度")
STAT_COLORS = ((225, 76, 87), (229, 133, 61), (205, 167, 45), (67, 132, 216), (46, 159, 130), (150, 94, 205))


@dataclass(frozen=True)
class Palette:
    bg: tuple
    panel: tuple
    ink: tuple
    muted: tuple
    accent: tuple
    line: tuple


PALETTES = {
    "C": Palette((247, 245, 239), (255, 255, 252), (30, 40, 47), (73, 85, 91), (82, 112, 67), (201, 210, 186)),
    "R": Palette((247, 245, 239), (249, 253, 255), (30, 40, 47), (73, 85, 91), (38, 115, 160), (178, 206, 220)),
    "SR": Palette((247, 245, 239), (253, 251, 255), (30, 40, 47), (73, 85, 91), (126, 89, 161), (213, 199, 224)),
    "SSR": Palette((247, 245, 239), (255, 253, 246), (30, 40, 47), (73, 85, 91), (158, 112, 24), (210, 186, 119)),
    "SHINY": Palette((247, 245, 239), (253, 254, 255), (30, 40, 47), (73, 85, 91), (130, 87, 165), (194, 201, 220)),
}
SPECTRUM = ((218, 125, 154), (212, 180, 104), (131, 183, 137), (116, 181, 208), (146, 139, 205), (193, 136, 201))


def edition(item: DrawItem) -> str:
    return "SHINY" if item.shiny else item.rarity if item.rarity in PALETTES else "C"


@lru_cache(maxsize=48)
def _font(size: int):
    return load_font(size)


def _fit(draw, text, width, size=56, minimum=36):
    for value in range(size, minimum - 1, -2):
        if draw.textlength(str(text), font=_font(value)) <= width:
            return value
    return minimum


def _wrap(draw, text, size, width, maximum=None):
    text = str(text or "")
    font = _font(size)
    lines, current = [], ""
    tokens = re.findall(r"[一-龥]{1,6}×\d+(?:\.\d+)?|[♂♀]\d+(?:\.\d+)?%|[A-Za-z0-9.%_+/-]+|[^\n]|\n", text)
    for token in tokens:
        if token == "\n":
            lines.append(current); current = ""; continue
        chunks = list(token) if draw.textlength(token, font=font) > width else [token]
        for chunk in chunks:
            if current and draw.textlength(current + chunk, font=font) > width:
                if chunk in "，。；：、！？）】":
                    # Move a complete type multiplier or percentage with punctuation.
                    tail_match = re.search(r"(?:[一-龥]{1,6}×\d+(?:\.\d+)?|[♂♀]\d+(?:\.\d+)?%|[A-Za-z0-9.%_+/-]+|.)$", current)
                    tail = tail_match.group() if tail_match else current[-1]
                    head = current[:-len(tail)]
                    if head:
                        lines.append(head); current = tail + chunk
                    else:
                        lines.append(current); current = chunk
                else:
                    lines.append(current); current = chunk
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
        tx = x + ((width or 0) - draw.textlength(line, font=_font(size))) / 2 if center else x
        draw.text((round(tx), y + index * step), line, font=_font(size), fill=color, anchor="lt")
    return y + len(lines) * step


def _panel(draw, box, p, radius=22, fill=None):
    draw.rounded_rectangle(box, radius=radius, fill=fill or p.panel, outline=p.line, width=2)


def _name(draw, item, x, y, width, p, size=56, center=False):
    size = _fit(draw, item.name, width, size, 38)
    return _text(draw, x, y, item.name, size, p.ink, width=width, maximum=3, center=center)






def _stats(item):
    match = re.search(r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)", str(item.row.get("stats") or ""))
    return dict(zip(STATS, map(int, match.groups()))) if match else {}


def _stat_bars(draw, item, x, y, width, p, *, step=42):
    values = _stats(item)
    if not values:
        return _text(draw, x, y, f"种族值总和 {item.bst}", 26, p.ink, width=width)
    draw.text((x, y), "种族值", font=_font(25), fill=p.muted, anchor="lt")
    y += 42
    bar_x, bar_w = x+75, width-132
    for index, key in enumerate(STATS):
        yy = y+index*step
        draw.text((x, yy), key, font=_font(24), fill=p.ink, anchor="lt")
        draw.rounded_rectangle((bar_x, yy+7, bar_x+bar_w, yy+23), radius=8, fill=(226, 231, 227))
        ratio = min(1, max(0, values[key]/180))
        if ratio:
            draw.rounded_rectangle((bar_x, yy+7, bar_x+max(8, bar_w*ratio), yy+23), radius=8, fill=STAT_COLORS[index])
        draw.text((bar_x+bar_w+12, yy), str(values[key]), font=_font(24), fill=p.ink, anchor="lt")
    return _text(draw, x, y+6*step, f"总和 {item.bst}", 24, p.muted, width=width)




def _info(item, *, dex=False):
    row = item.row
    basic = " · ".join(str(row.get(k) or "").strip() for k in ("category", "height", "weight") if row.get(k))
    lines = [(f"全国图鉴 #{item.dex_raw}", 1)] if dex else []
    if basic: lines.append((basic, 2))
    ability, hidden = str(row.get("abilities") or ""), str(row.get("hidden_abilities") or "")
    if ability or hidden:
        lines.append((f"特性：{ability or '—'}" + (f"（隐藏：{hidden}）" if hidden else ""), 3))
    if row.get("catch_rate"): lines.append((f"捕获度：{row['catch_rate']}", 2))
    for key, label in (("weak_to", "弱点"), ("resist_to", "抗性"), ("immune_to", "免疫")):
        if row.get(key): lines.append((f"{label}：{row[key]}", 3))
    extra = []
    if row.get("egg_groups"): extra.append(f"蛋群：{row['egg_groups']}")
    gender = str(row.get("gender_ratio") or "")
    gender = {"♂0%♀0%": "无性别", "♂0%♀100%": "仅雌性", "♂100%♀0%": "仅雄性"}.get(gender.replace(" ", ""), gender)
    if gender: extra.append(f"性别：{gender}")
    if extra: lines.append((" · ".join(extra), 2))
    if row.get("evolution"): lines.append((f"进化：{row['evolution']}", 4))
    return lines or [("暂无基础资料", 1)]


def _block_lines(draw, texts, width):
    out = []
    for text, cap in texts:
        out.extend(_wrap(draw, text, BODY_SIZE, width, cap))
    return tuple(out)


def _block_height(lines):
    return 74 + len(lines)*38


def _block(draw, x, y, width, title, lines, p, *, plain=False):
    height = _block_height(lines)
    if not plain: _panel(draw, (x, y, x+width, y+height), p)
    draw.text((x+24, y+20), title, font=_font(28), fill=p.accent, anchor="lt")
    for index, line in enumerate(lines):
        draw.text((x+24, y+64+index*38), line, font=_font(BODY_SIZE), fill=p.ink, anchor="lt")
    return y+height


def _art(base, draw, item, loader, box, p):
    x, y, width, height = box
    image = loader(item.row) if loader else None
    if isinstance(image, bytes): image = Image.open(io.BytesIO(image))
    if image is not None:
        image = image.convert("RGBA")
        bounds = image.getchannel("A").getbbox()
        if bounds: image = image.crop(bounds)
        image.thumbnail((width, height), Image.Resampling.LANCZOS)
        base.alpha_composite(image, (round(x+(width-image.width)/2), round(y+(height-image.height)/2)))
    else:
        cx, cy, radius = x+width/2, y+height/2-12, min(width,height)*.25
        draw.ellipse((cx-radius,cy-radius,cx+radius,cy+radius), outline=p.line, width=3)
        draw.line((cx-radius,cy,cx+radius,cy), fill=p.line, width=3)
        draw.ellipse((cx-18,cy-18,cx+18,cy+18), fill=p.panel, outline=p.line,width=3)
        _text(draw, x, cy+radius+26, "立绘暂未缓存", 24, p.muted, width=width, center=True)




try:
    from .partner_cards import (
        _frame, _draw_draw_header, _type_pill, _rarity_badge, _pill, _pokeball,
        NEW_GREEN, GOLD,
    )
except ImportError:
    from partner_cards import (
        _frame, _draw_draw_header, _type_pill, _rarity_badge, _pill, _pokeball,
        NEW_GREEN, GOLD,
    )


def _base(height, p):
    return Image.new("RGBA", (WIDTH, height), (*p.bg, 255))


def _finish(base):
    stream = io.BytesIO()
    base.convert("RGB").save(stream, "JPEG", quality=94, optimize=True)
    return stream.getvalue()


def _paper_header(draw, context):
    _draw_draw_header(draw, **context)
    if not context["ssr_pity_after"]:
        _text(draw, 562, 147, "SSR 保底关闭", 22, (73, 85, 91), width=430)


def _edition_frame(draw, height, kind):
    if kind == "SSR":
        for inset, color in ((22, (208, 188, 139)), (27, (235, 224, 195))):
            draw.rounded_rectangle((inset, inset, WIDTH-inset, height-inset), radius=24,
                                   outline=color, width=1)
        for x, y in ((29, 53), (1051, 53), (29, height-53), (1051, height-53)):
            draw.polygon(((x, y-4), (x+3, y), (x, y+4), (x-3, y)), fill=(185, 150, 74))
    elif kind == "SHINY":
        draw.rounded_rectangle((23, 23, WIDTH-23, height-23), radius=25,
                               outline=(214, 211, 229), width=1)
        for index, color in enumerate(SPECTRUM):
            pearl = tuple(round(channel*.55+255*.45) for channel in color)
            x1, x2 = 48+index*164, 48+(index+1)*164
            draw.line((x1, 24, x2, 24), fill=pearl, width=1)
            draw.line((x1, height-24, x2, height-24), fill=pearl, width=1)
        for x, y in ((31, 55), (1049, height-55)):
            draw.polygon(((x, y-5), (x+2, y-2), (x+5, y), (x+2, y+2),
                          (x, y+5), (x-2, y+2), (x-5, y), (x-2, y-2)), fill=(167, 137, 191))


def _paper_footer(draw, item, y):
    fresh = item.is_new
    color = NEW_GREEN if fresh else GOLD
    note = "新宝可梦已加入图鉴" if fresh else f"重复获得 · 转化为 {item.dust} 星尘"
    draw.rounded_rectangle((76, y, 1004, y+56), radius=12,
                           fill=(230, 244, 235) if fresh else (249, 243, 224))
    _text(draw, 94, y+15, note, 26, color, width=884)
    _text(draw, 48, y+76, "重复宝可梦自动折算星尘 · 兑换宝可梦 <名称> 可定向获得", 22,
          (73, 85, 91), width=984)


def _type_layout(draw, item, x, y, width):
    positions, cursor, current_y = [], x, y
    for label in filter(None, item.types.split("/")):
        size = round(draw.textlength(label, font=_font(24)))+26
        if cursor > x and cursor+size > x+width:
            cursor, current_y = x, current_y+46
        positions.append((label, cursor, current_y))
        cursor += size+8
    return positions, current_y+46 if positions else y


def _observation_ring(base, draw, box, p):
    cx, cy = 294, 470
    for yy in range(256, 714, 30):
        draw.line((66, yy, 516, yy), fill=(238, 236, 224), width=1)
    draw.ellipse((cx-219, cy-219, cx+219, cy+219), fill=(246, 246, 235))
    _pokeball(draw, cx, cy, 180, muted=True)
    draw.arc((cx-214, cy-214, cx+214, cy+214), 212, 309, fill=p.accent, width=5)
    draw.arc((cx-214, cy-214, cx+214, cy+214), 42, 102, fill=p.accent, width=5)
    for xx, yy in ((98, 314), (482, 637)):
        draw.ellipse((xx-8, yy-17, xx+8, yy+17), fill=(144, 163, 111))
        draw.line((xx, yy-11, xx, yy+11), fill=(245, 248, 235), width=2)


def _crystal_stage(base, draw, box, p):
    cx, cy = 294, 473
    draw.polygon(((cx, 236), (520, cy), (cx, 707), (68, cy)),
                 fill=(230, 243, 249), outline=(126, 177, 205))
    draw.polygon(((cx, 264), (492, cy), (cx, 679), (96, cy)),
                 fill=(248, 253, 255), outline=(173, 208, 225))
    draw.polygon(((cx, 236), (520, cy), (492, cy), (cx, 264)), fill=(203, 230, 242))
    draw.polygon(((68, cy), (cx, 707), (cx, 679), (96, cy)), fill=(213, 234, 245))
    for xx, yy in ((78, 286), (512, 667)):
        draw.polygon(((xx, yy-16), (xx+10, yy), (xx, yy+16), (xx-10, yy)),
                     fill=(238, 248, 251), outline=p.accent)
    draw.line((124, 719, 466, 719), fill=p.line, width=2)


def _premiere_stage(base, draw, box, p):
    draw.rounded_rectangle((75, 253, 511, 715), radius=200, fill=(244, 239, 249))
    draw.polygon(((74, 263), (127, 263), (112, 710), (74, 710)), fill=(233, 226, 242))
    draw.polygon(((462, 263), (514, 263), (514, 710), (477, 710)), fill=(233, 226, 242))
    draw.arc((72, 239, 516, 707), 180, 360, fill=p.accent, width=5)
    draw.line((72, 474, 72, 713), fill=p.accent, width=5)
    draw.line((516, 474, 516, 713), fill=p.accent, width=5)
    draw.polygon(((67, 712), (522, 712), (533, 728), (56, 728)), fill=(219, 207, 233))
    draw.line((84, 727, 504, 727), fill=(152, 124, 179), width=2)


def _legend_medallion(base, draw, box, p):
    cx, cy = 294, 473
    draw.ellipse((58, 237, 530, 709), fill=(251, 246, 228), outline=(213, 191, 131), width=2)
    for radius in (228, 212, 191):
        draw.ellipse((cx-radius, cy-radius, cx+radius, cy+radius), outline=p.line, width=2)
    draw.line((cx-210, cy, cx+210, cy), fill=(230, 218, 183), width=1)
    draw.line((cx, cy-210, cx, cy+210), fill=(230, 218, 183), width=1)
    for index in range(16):
        angle = index*math.pi/8
        xx, yy = cx+math.cos(angle)*228, cy+math.sin(angle)*228
        draw.ellipse((xx-3, yy-3, xx+3, yy+3), fill=p.accent)
    draw.polygon(((294, 230), (306, 244), (294, 258), (282, 244)), fill=p.accent)
    draw.rounded_rectangle((169, 718, 419, 750), radius=6, fill=(248, 241, 219), outline=p.line)


def _prism_stage(base, draw, box, p):
    layer = Image.new("RGBA", base.size)
    ld = ImageDraw.Draw(layer)
    for index, color in enumerate(SPECTRUM):
        xx = 152+(index%3)*132
        yy = 358+(index//3)*210
        ld.ellipse((xx-78, yy-100, xx+78, yy+100), fill=(*color, 45))
    base.alpha_composite(layer.filter(ImageFilter.GaussianBlur(34)))
    draw = ImageDraw.Draw(base)
    draw.polygon(((119, 255), (459, 239), (520, 622), (186, 720)),
                 fill=(248, 252, 255), outline=(174, 181, 214))
    draw.polygon(((119, 255), (459, 239), (294, 473)), fill=(246, 239, 251))
    draw.polygon(((459, 239), (520, 622), (294, 473)), fill=(232, 247, 244))
    draw.polygon(((520, 622), (186, 720), (294, 473)), fill=(248, 244, 228))
    for index, color in enumerate(SPECTRUM):
        draw.line((122+index*56, 253-index*2, 174+index*56, 251-index*2), fill=color, width=3)
    for xx, yy, radius in ((78, 343, 15), (510, 685, 19)):
        draw.polygon(((xx, yy-radius), (xx+4, yy-4), (xx+radius, yy), (xx+4, yy+4),
                      (xx, yy+radius), (xx-4, yy+4), (xx-radius, yy), (xx-4, yy-4)), fill=p.accent)


@dataclass(frozen=True)
class EditionDesign:
    stage: Callable
    art: tuple
    name_y: int
    name_size: int
    left_width: int
    profile_cap: int
    band: str = ""


DESIGNS = {
    "C": EditionDesign(_observation_ring, (84, 260, 420, 420), 304, 56, 438, 9),
    "R": EditionDesign(_crystal_stage, (100, 270, 388, 410), 296, 60, 438, 8, "top"),
    "SR": EditionDesign(_premiere_stage, (74, 267, 440, 438), 308, 58, 438, 9),
    "SSR": EditionDesign(_legend_medallion, (62, 247, 464, 455), 305, 64, 522, 10),
    "SHINY": EditionDesign(_prism_stage, (82, 266, 424, 438), 296, 58, 438, 8, "bottom"),
}


def _single(item, context, loader):
    kind = edition(item)
    design, p = DESIGNS[kind], PALETTES[kind]
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    name_size = _fit(measure, item.name, 448, design.name_size, 38)
    name_h = len(_wrap(measure, item.name, name_size, 448, 3))*(name_size+12)
    type_positions, type_end = _type_layout(measure, item, 552, design.name_y+name_h+18, 448)
    stats_y = type_end+16
    hero_end = max(782 if kind == "SSR" else 764, stats_y+306+12)
    info_fields = _info(item)
    matchups = []
    if design.band:
        matchup_prefixes = ("弱点：", "抗性：", "免疫：")
        matchups = [field for field in info_fields if field[0].startswith(matchup_prefixes)]
        info_fields = [field for field in info_fields if not field[0].startswith(matchup_prefixes)]
    info = _block_lines(measure, info_fields, design.left_width-48)
    profile_x = 76+design.left_width+28
    profile_w = 1004-profile_x
    profile = _wrap(measure, item.row.get("profile") or "暂无图鉴摘要", 26,
                    profile_w-48, design.profile_cap)
    matchup_lines = _block_lines(measure, matchups, 880) if matchups else ()
    band_h = 38+len(matchup_lines)*38 if matchup_lines else 0
    info_y = hero_end+30+(band_h+18 if design.band == "top" and matchup_lines else 0)
    info_end = info_y+max(_block_height(info), _block_height(profile))
    footer_y = info_end+24+(band_h+18 if design.band == "bottom" and matchup_lines else 0)
    base = _base(footer_y+128, p)
    draw = ImageDraw.Draw(base)
    _frame(draw, base.height)
    _edition_frame(draw, base.height, kind)
    design.stage(base, draw, design.art, p)
    _art(base, ImageDraw.Draw(base), item, loader, design.art, p)
    draw = ImageDraw.Draw(base)
    _paper_header(draw, context)
    badge_end = _rarity_badge(draw, (552, 236), item.rarity)
    if item.is_new:
        badge_end = _pill(draw, (badge_end, 236), "NEW", NEW_GREEN)+10
    if item.shiny:
        _pill(draw, (badge_end, 236), "闪光", (123, 84, 160))
    _name(draw, item, 552, design.name_y, 448, p, size=design.name_size)
    for label, x, y in type_positions:
        _type_pill(draw, (x, y), label)
    _stat_bars(draw, item, 552, stats_y, 448, p, step=38)
    dex_y = 719 if kind == "SSR" else 740 if kind in ("R", "SR") else 716
    if kind == "SSR":
        _text(draw, 169, dex_y+3, f"#{item.dex_raw}", 24, p.accent, width=250, center=True)
    else:
        _text(draw, 84, dex_y, f"#{item.dex_raw}", 24, p.muted, width=420)
    separator_y = hero_end+12
    draw.line((76, separator_y, 1004, separator_y), fill=p.line, width=2)
    if kind == "SSR":
        draw.polygon(((529, separator_y), (540, separator_y-8), (551, separator_y), (540, separator_y+8)), fill=p.accent)
    elif kind == "SHINY":
        for index, color in enumerate(SPECTRUM):
            draw.line((76+index*154, separator_y, 76+(index+1)*154, separator_y), fill=color, width=3)
    _block(draw, 76, info_y, design.left_width, "宝可梦资料", info, p,
           plain=kind in ("C", "SR"))
    _block(draw, profile_x, info_y, profile_w, "图鉴摘要", profile, p,
           plain=kind in ("C", "R", "SR"))
    if kind == "C":
        draw.line((76, info_y+16, 76, info_end-12), fill=(195, 204, 175), width=3)
    elif kind == "SR":
        draw.line((76, info_y+12, 76+design.left_width, info_y+12), fill=p.accent, width=3)
        draw.line((profile_x, info_y+12, 1004, info_y+12), fill=p.line, width=3)
    if matchup_lines:
        yy = hero_end+30 if design.band == "top" else info_end+18
        _panel(draw, (76, yy, 1004, yy+band_h), p, radius=14)
        for index, line in enumerate(matchup_lines):
            _text(draw, 100, yy+18+index*38, line, 26, p.ink, width=880)
    _paper_footer(draw, item, footer_y)
    return _finish(base)


def _tile(base, draw, item, box, loader):
    x,y,w,h=box;p=PALETTES[edition(item)];kind=edition(item)
    _panel(draw,(x,y,x+w,y+h),p,radius=18)
    if kind=="C":
        draw.rounded_rectangle((x+12,y+14,x+w-12,y+54),radius=8,fill=(233,237,214))
        draw.ellipse((x+42,y+80,x+w-42,y+80+w-84),fill=(235,237,216),outline=p.line,width=2)
        art=(x+48,y+86,w-96,w-96)
        name_y=y+w+34
    elif kind=="R":
        draw.polygon([(x+w/2,y+59),(x+w-23,y+59+(w-46)/2),(x+w/2,y+w+13),(x+23,y+59+(w-46)/2)],fill=(220,239,247),outline=p.accent)
        art=(x+48,y+77,w-96,w-96);name_y=y+w+34
    elif kind=="SR":
        draw.arc((x+42,y+64,x+w-24,y+w+38),180,360,fill=p.accent,width=3)
        draw.line((x+42,y+140,x+42,y+w+10),fill=p.accent,width=3)
        draw.line((x+w-24,y+140,x+w-24,y+w+10),fill=p.accent,width=3)
        art=(x+53,y+89,w-90,w-90);name_y=y+w+34
    elif kind=="SSR":
        draw.ellipse((x+30,y+71,x+w-30,y+w+11),outline=p.accent,width=3)
        draw.line((x+18,y+65,x+18,y+w+14),fill=p.line,width=2);draw.line((x+w-18,y+65,x+w-18,y+w+14),fill=p.line,width=2)
        art=(x+37,y+76,w-74,w-74);name_y=y+w+34
    else:
        for index,color in enumerate(SPECTRUM):
            draw.line((x+12+index*(w-24)/6,y+14,x+12+(index+1)*(w-24)/6,y+14),fill=color,width=5)
        draw.polygon([(x+w/2,y+72),(x+w-29,y+114),(x+w-49,y+w+17),(x+29,y+w-23)],fill=(236,241,253),outline=p.accent)
        art=(x+44,y+86,w-88,w-88);name_y=y+w+34
    draw.text((x+19,y+24),f"{item.rarity} · {rarity_label(item.rarity)}" + (" · 闪光" if item.shiny else ""),font=_font(20),fill=p.accent,anchor="lt")
    _art(base,draw,item,loader,art,p);draw=ImageDraw.Draw(base)
    name_end=_text(draw,x+18,name_y,item.name,30 if w>400 else 26,p.ink,width=w-36,maximum=2)
    type_end=_text(draw,x+18,name_end+12,f"{item.types} · 总和 {item.bst}",24,p.muted,width=w-36,maximum=1)
    _text(draw,x+18,type_end+2,"NEW · 新伙伴" if item.is_new else f"重复 · +{item.dust}星尘",24,p.accent,width=w-36,maximum=1)


def _grid(items, context, loader):
    columns=2 if len(items)<=4 else 3
    gap=18; w=(WIDTH-120-gap*(columns-1))//columns
    measure=ImageDraw.Draw(Image.new("RGB",(1,1)))
    name_size=30 if w>400 else 26
    name_h=max(len(_wrap(measure,item.name,name_size,w-36,2))*(name_size+12) for item in items)
    h=w+34+name_h+12+36+2+36+12
    rows=math.ceil(len(items)/columns)
    height=214+rows*(h+gap)+100
    p=PALETTES["C"];base=_base(height,p);d=ImageDraw.Draw(base)
    _frame(d,height)
    _paper_header(d,context)
    for index,item in enumerate(items):
        x=MARGIN+(index%columns)*(w+gap);y=214+(index//columns)*(h+gap)
        _tile(base,d,item,(x,y,w,h),loader);d=ImageDraw.Draw(base)
    footer_y=height-88
    _text(d,48,footer_y,"重复宝可梦自动折算星尘 · 兑换宝可梦 <名称> 可定向获得",22,p.muted,width=984,maximum=1)
    return _finish(base)


def render_rarity_draw(items: Sequence[DrawItem], *, nickname: str, remaining: int, daily_limit: int,
                       dust: int, pity_ssr: int, ssr_pity_after: int,
                       image_loader: Callable | None = None) -> bytes:
    """One of five compositional editions, or a mixed collection of edition-specific stages."""
    if not items: raise ValueError("抽卡卡片至少需要一个结果")
    context=dict(nickname=nickname,remaining=remaining,daily_limit=daily_limit,dust=dust,pity_ssr=pity_ssr,ssr_pity_after=ssr_pity_after)
    if len(items)>1: return _grid(items,context,image_loader)
    return _single(items[0],context,image_loader)
