"""宝可梦系统的 Pillow 卡片渲染。

与 ``card_renderer.py`` 共用字体查找逻辑，但版式独立：抽卡结果、收藏图鉴、
训练家资料使用精灵球红、纸白与墨色的图鉴手册风格。

渲染函数只接收普通数据，不读配置、不访问数据库，便于单测与替换。
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from typing import Callable, Sequence

from PIL import Image, ImageDraw, ImageFont

try:
    from .card_renderer import load_font
    from .gacha import (
        DrawItem,
        GachaRules,
        OwnedPartner,
        ProfileSummary,
        rarity_label,
    )
except ImportError:  # 兼容以普通模块方式加载插件
    from card_renderer import load_font
    from gacha import (
        DrawItem,
        GachaRules,
        OwnedPartner,
        ProfileSummary,
        rarity_label,
    )

W = 1080
PAD = 44

BG_TOP = (247, 245, 239)
BG_BOTTOM = (235, 233, 225)
PANEL = (255, 255, 252)
PANEL_ALT = (246, 246, 241)
TEXT = (30, 40, 47)
SUBTEXT = (87, 101, 108)
LINE = (219, 224, 218)
RED = (199, 53, 53)
GOLD = (155, 102, 12)
NEW_GREEN = (22, 124, 94)

RARITY_COLORS: dict[str, tuple[int, int, int]] = {
    "C": (87, 105, 114),
    "R": (34, 105, 177),
    "SR": (111, 72, 151),
    "SSR": (155, 102, 12),
}
RARITY_GLOW: dict[str, tuple[int, int, int]] = {
    "C": (71, 85, 105),
    "R": (30, 64, 175),
    "SR": (88, 28, 135),
    "SSR": (146, 64, 14),
}

# 属性配色（文字依据底色选择明暗，保证标签可读）
TYPE_COLORS: dict[str, tuple[int, int, int]] = {
    "一般": (154, 161, 159),
    "火": (230, 40, 40),
    "水": (41, 128, 239),
    "草": (63, 161, 41),
    "电": (232, 184, 0),
    "冰": (61, 206, 243),
    "格斗": (255, 128, 0),
    "毒": (145, 65, 203),
    "地面": (145, 81, 33),
    "飞行": (129, 185, 239),
    "超能力": (239, 65, 121),
    "虫": (145, 161, 25),
    "岩石": (175, 169, 129),
    "幽灵": (112, 65, 112),
    "龙": (80, 96, 225),
    "恶": (98, 77, 78),
    "钢": (161, 161, 161),
    "妖精": (239, 112, 239),
}

ImageLoader = Callable[[dict], Image.Image | None]


def _font(size: int) -> ImageFont.FreeTypeFont:
    return load_font(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in str(text):
        if ch == "\n":
            lines.append(current)
            current = ""
            continue
        if draw.textlength(current + ch, font=font) > width and current:
            lines.append(current)
            current = ch
        else:
            current += ch
    if current:
        lines.append(current)
    return lines or [""]


def _limited_wrapped_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    width: int,
    max_lines: int,
) -> list[str]:
    """按宽度换行并限制行数；超长时只在最后一行加省略号。"""
    lines = _wrap(draw, text, font, width)
    lines = [
        line if index == 0 else line.lstrip("，。；：、！？")
        for index, line in enumerate(lines)
    ]
    if len(lines) <= max_lines:
        return lines
    lines = lines[:max_lines]
    lines[-1] = _ellipsize(draw, lines[-1] + "…", font, width)
    return lines


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
    """把超宽文本截断成一行并以省略号收尾，避免压到右侧徽章。"""
    if draw.textlength(text, font=font) <= width:
        return text
    ellipsis = "…"
    budget = width - draw.textlength(ellipsis, font=font)
    out = ""
    for ch in text:
        if draw.textlength(out + ch, font=font) > budget:
            break
        out += ch
    return (out or text[:1]) + ellipsis


def _fit_text_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    width: int,
    max_size: int,
    min_size: int,
):
    """在可读范围内选择最大字号，尽量完整显示长名称。"""
    for size in range(max_size, min_size - 1, -2):
        font = _font(size)
        if draw.textlength(text, font=font) <= width:
            return font
    return _font(min_size)


def _gender_text(value: str) -> str:
    compact = str(value or "").replace(" ", "")
    if compact == "♂0%♀0%":
        return "无性别"
    if compact == "♂0%♀100%":
        return "仅雌性"
    if compact == "♂100%♀0%":
        return "仅雄性"
    return str(value or "")


def _gradient(size: tuple[int, int]) -> Image.Image:
    width, height = size
    img = Image.new("RGB", (1, height))
    draw = ImageDraw.Draw(img)
    for y in range(height):
        ratio = y / max(1, height - 1)
        color = tuple(
            int(BG_TOP[i] + (BG_BOTTOM[i] - BG_TOP[i]) * ratio) for i in range(3)
        )
        draw.point((0, y), fill=color)
    return img.resize((width, height), Image.Resampling.NEAREST)


def _starfield(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """确定性的笔记纸点阵。"""
    for y in range(24, height, 24):
        for x in range(24, width, 24):
            draw.point((x, y), fill=(218, 220, 213))


def _frame(draw: ImageDraw.ImageDraw, height: int) -> None:
    _round_panel(draw, [16, 16, W - 16, height - 16], fill=PANEL, outline=LINE, radius=28)
    draw.rounded_rectangle([36, 32, W - 36, 40], radius=4, fill=RED)


def _pokeball(
    draw: ImageDraw.ImageDraw,
    cx: float,
    cy: float,
    radius: float,
    *,
    muted: bool = False,
) -> None:
    """用矢量几何绘制精灵球；不依赖额外图片资源。"""
    outline = LINE if muted else TEXT
    top_color = (231, 220, 211) if muted else RED
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=PANEL, outline=outline, width=2)
    draw.pieslice([cx - radius, cy - radius, cx + radius, cy + radius], 180, 360, fill=top_color)
    draw.line([cx - radius, cy, cx + radius, cy], fill=outline, width=max(2, int(radius * .07)))
    center = radius * .23
    draw.ellipse([cx - center, cy - center, cx + center, cy + center], fill=PANEL, outline=outline, width=max(2, int(radius * .04)))
    draw.ellipse([cx - center * .45, cy - center * .45, cx + center * .45, cy + center * .45], outline=outline, width=2)


def _round_panel(
    draw: ImageDraw.ImageDraw,
    box: Sequence[int],
    *,
    fill: tuple[int, int, int] = PANEL,
    outline: tuple[int, int, int] | None = None,
    width: int = 2,
    radius: int = 20,
) -> None:
    draw.rounded_rectangle(
        list(box), radius=radius, fill=fill, outline=outline, width=width
    )


def _rarity_badge(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    rarity: str,
    *,
    small: bool = False,
) -> int:
    font = _font(22 if small else 24)
    label = f"{rarity} · {rarity_label(rarity)}"
    color = RARITY_COLORS.get(rarity, RARITY_COLORS["C"])
    tw = draw.textlength(label, font=font)
    w, h = int(tw) + 26, 38 if small else 42
    x, y = xy
    draw.rounded_rectangle([x, y, x + w, y + h], radius=12, fill=color)
    draw.text((x + 13, y + (7 if small else 8)), label, font=font, fill=(255, 255, 255))
    return x + w + 10


def _pill(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    label: str,
    color: tuple[int, int, int],
) -> int:
    """小标签（NEW / 闪光），返回右边界。"""
    font = _font(21)
    tw = draw.textlength(label, font=font)
    x, y = xy
    w, h = int(tw) + 22, 32
    draw.rounded_rectangle([x, y + 5, x + w, y + 5 + h], radius=16, fill=color)
    draw.text(
        (x + 11, y + 10), label, font=font, fill=(255, 255, 255)
    )
    return x + w


def _type_pill(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], type_name: str, *, small: bool = False
) -> int:
    """属性胶囊，颜色取自官方属性配色，返回右边界。"""
    color = TYPE_COLORS.get(type_name, (100, 116, 139))
    font = _font(19 if small else 24)
    tw = draw.textlength(type_name, font=font)
    x, y = xy
    w, h = int(tw) + 26, 30 if small else 36
    draw.rounded_rectangle([x, y, x + w, y + h], radius=18, fill=color)
    channels = [c / 255 for c in color]
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4 for c in channels]
    luminance = .2126 * linear[0] + .7152 * linear[1] + .0722 * linear[2]
    text_color = TEXT if luminance > .22 else (255, 255, 255)
    draw.text((x + 13, y + 5), type_name, font=font, fill=text_color)
    return x + w + 8


def _stat_dict(item: DrawItem) -> dict[str, int]:
    """从原始行解析六项种族值；解析不到返回空 dict。"""
    import re

    text = str(item.row.get("stats") or "")
    match = re.search(
        r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)", text
    )
    if not match:
        return {}
    keys = ("HP", "攻击", "防御", "特攻", "特防", "速度")
    return {key: int(value) for key, value in zip(keys, match.groups())}


def _info_lines(item: DrawItem) -> list[str]:
    """基础信息行：分类、身高体重、捕获度、特性。"""
    row = item.row
    lines: list[str] = []
    basics = " · ".join(
        part
        for part in (
            str(row.get("category") or "").strip(),
            str(row.get("height") or "").strip(),
            str(row.get("weight") or "").strip(),
        )
        if part
    )
    if basics:
        lines.append(basics)
    abilities = str(row.get("abilities") or "").strip()
    hidden = str(row.get("hidden_abilities") or "").strip()
    if abilities or hidden:
        text = f"特性：{abilities or '—'}"
        if hidden:
            text += f"（隐藏：{hidden}）"
        lines.append(text)
    catch = str(row.get("catch_rate") or "").strip()
    if catch:
        lines.append(f"捕获度：{catch}")
    return lines[:3]


def _matchup_lines(item: DrawItem, font) -> list[str]:
    """弱点与抗性摘要，最多两行，避免卡片无限增高。"""
    row = item.row
    lines: list[str] = []
    weak = str(row.get("weak_to") or "").strip()
    resist = str(row.get("resist_to") or "").strip()
    immune = str(row.get("immune_to") or "").strip()
    if weak:
        lines.append(f"弱点：{weak}")
    summary = []
    if resist:
        summary.append(f"抗性 {resist}")
    if immune:
        summary.append(f"免疫 {immune}")
    if summary:
        lines.append(" · ".join(summary))
    return lines[:2]


STAT_BAR_COLORS = {
    "HP": (255, 89, 94),
    "攻击": (255, 144, 60),
    "防御": (255, 202, 58),
    "特攻": (74, 144, 226),
    "特防": (67, 200, 144),
    "速度": (168, 85, 247),
}


def _stat_bars(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    stats: dict[str, int],
    total: int,
) -> None:
    """六项种族值横向条形图。"""
    label_font, value_font = _font(21), _font(21)
    label_w = 64
    bar_x = x + label_w
    bar_w = max(60, width - label_w - 62)
    row_h = 38
    for index, (key, value) in enumerate(stats.items()):
        top = y + index * row_h
        draw.text((x, top + 2), key, font=label_font, fill=SUBTEXT)
        draw.rounded_rectangle(
            [bar_x, top + 8, bar_x + bar_w, top + 24], radius=8, fill=(226, 231, 225)
        )
        ratio = max(0.0, min(1.0, value / 180))
        if ratio > 0:
            draw.rounded_rectangle(
                [bar_x, top + 8, bar_x + max(10, int(bar_w * ratio)), top + 24],
                radius=8,
                fill=STAT_BAR_COLORS.get(key, (120, 120, 120)),
            )
        draw.text(
            (bar_x + bar_w + 10, top + 2),
            str(value),
            font=value_font,
            fill=TEXT,
        )
    draw.text(
        (x, y + len(stats) * row_h + 2),
        f"总和 {total}",
        font=_font(21),
        fill=SUBTEXT,
    )


def _paste_art(
    base: Image.Image, art: Image.Image | None, box: tuple[int, int, int, int]
) -> None:
    if art is None:
        return
    x, y, size, box_h = box
    box_h = box_h or size
    image = art.convert("RGBA")
    bounds = image.getchannel("A").getbbox()
    if bounds:
        image = image.crop(bounds)
    image.thumbnail((size, box_h), Image.Resampling.LANCZOS)
    base.paste(
        image,
        (x + (size - image.width) // 2, y + (box_h - image.height) // 2),
        image,
    )


def _placeholder_art(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rarity: str
) -> None:
    x, y, size, _ = box
    draw.rounded_rectangle([x, y, x + size, y + size], radius=18, fill=PANEL_ALT, outline=LINE, width=1)
    cx, cy = x + size / 2, y + size / 2
    _pokeball(draw, cx, cy - size * .03, size * .22, muted=True)
    if size >= 160:
        label = "立绘暂未缓存"
        font = _font(18)
        tw = draw.textlength(label, font=font)
        draw.text((cx - tw / 2, cy + size * .29), label, font=font, fill=SUBTEXT)


def _draw_result_rows(
    base: Image.Image,
    draw: ImageDraw.ImageDraw,
    items: Sequence[DrawItem],
    image_loader: ImageLoader | None,
) -> int:
    y = 190
    name_font, meta_font, small = _font(34), _font(23), _font(21)
    for index, item in enumerate(items):
        color = RARITY_COLORS.get(item.rarity, RARITY_COLORS["C"])
        box = [PAD, y, W - PAD, y + 150]
        _round_panel(
            draw,
            box,
            fill=PANEL_ALT if index % 2 else PANEL,
            outline=GOLD if item.shiny else LINE,
            width=3 if item.shiny else 1,
            radius=22,
        )
        draw.rounded_rectangle(
            [PAD, y, PAD + 10, y + 150], radius=5, fill=color
        )
        art = image_loader(item.row) if image_loader else None
        art_box = (PAD + 28, y + 22, 104, 0)
        if art is None:
            _placeholder_art(draw, art_box, item.rarity)
        else:
            _paste_art(base, art, art_box)

        tx = PAD + 156
        title = f"#{item.dex_raw}  {item.name}"
        if item.shiny:
            title += "  [闪光]"
        title = _ellipsize(draw, title, name_font, W - PAD - 210 - tx)
        draw.text((tx, y + 24), title, font=name_font, fill=GOLD if item.shiny else TEXT)
        meta = _ellipsize(
            draw,
            f"{item.types or '未知属性'} · 种族值 {item.bst}",
            meta_font,
            W - PAD - 210 - tx,
        )
        draw.text((tx, y + 74), meta, font=meta_font, fill=SUBTEXT)
        if item.shiny:
            draw.text(
                (tx, y + 108),
                "闪光个体 · 星尘加成",
                font=small,
                fill=GOLD,
            )
        elif item.is_new:
            draw.text((tx, y + 108), "新宝可梦已加入图鉴", font=small, fill=NEW_GREEN)
        else:
            draw.text(
                (tx, y + 108),
                f"重复获得 · 转化为 {item.dust} 星尘",
                font=small,
                fill=SUBTEXT,
            )

        badge_x = W - PAD - 190
        _rarity_badge(draw, (badge_x, y + 22), item.rarity, small=True)
        status = "NEW" if item.is_new else f"+{item.dust} 星尘"
        status_color = NEW_GREEN if item.is_new else GOLD
        sf = _font(26)
        tw = draw.textlength(status, font=sf)
        draw.text((W - PAD - 24 - tw, y + 92), status, font=sf, fill=status_color)
        y += 166
    return y


def _grid_layout(count: int) -> tuple[int, int, int]:
    """返回多抽网格的 ``(列数, 行数, 卡片高度)``。"""
    columns = 2 if count <= 4 else 3
    rows = math.ceil(count / columns)
    return columns, rows, 460 if columns == 2 else 330


@dataclass(frozen=True)
class _HeroLayout:
    """单抽页的测量结果，绘制与画布高度共用同一组换行。"""

    height: int
    name_font: ImageFont.FreeTypeFont
    name_lines: tuple[str, ...]
    name_top: int
    name_step: int
    type_positions: tuple[tuple[str, int, int], ...]
    stats_top: int
    info_top: int
    info_lines: tuple[tuple[str, int], ...]
    profile_lines: tuple[str, ...]


def _hero_layout(draw: ImageDraw.ImageDraw, item: DrawItem) -> _HeroLayout:
    """先测量名称和资料，避免长名称相撞或长进化链被底部结果条截断。"""
    top, x = 214, PAD + 40 + 420 + 48
    width = W - PAD - 40 - x
    name_font = _fit_text_font(draw, item.name, width, 54, 30)
    name_lines = tuple(_limited_wrapped_lines(draw, item.name, name_font, width, 2))
    name_top, name_step = top + 22 + 64, name_font.size + 10
    type_y = name_top + len(name_lines) * name_step + 12
    tx = x
    type_positions = []
    for type_name in [t.strip() for t in item.types.split("/") if t.strip()]:
        pill_width = int(draw.textlength(type_name, font=_font(24))) + 34
        if tx > x and tx + pill_width - 8 > x + width:
            tx, type_y = x, type_y + 44
        type_positions.append((type_name, tx, type_y))
        tx += pill_width
    stats_top = type_y + (36 if type_positions else 0) + 52
    # 六项能力条与总和占用 257px；左侧包含立绘及其编号。
    stats_bottom = stats_top + (257 if _stat_dict(item) else 34)
    hero_bottom = max(top + 466 + 30, stats_bottom)
    info_top = hero_bottom + 76

    left_width = x - (PAD + 40) - 28
    line_font, line_h = _font(20), 27
    info_lines: list[tuple[str, int]] = []
    cursor = 0

    def append_block(text: str, max_lines: int) -> None:
        nonlocal cursor
        for line in _limited_wrapped_lines(draw, text, line_font, left_width, max_lines):
            info_lines.append((line, cursor))
            cursor += line_h

    for line in _info_lines(item):
        append_block(line, 2)
    cursor += 4
    for line in _matchup_lines(item, line_font):
        append_block(line, 2)
    cursor += 4
    egg_groups = str(item.row.get("egg_groups") or "").strip()
    gender_ratio = _gender_text(str(item.row.get("gender_ratio") or "").strip())
    detail = " · ".join(part for part in (
        f"蛋群：{egg_groups}" if egg_groups else "",
        f"性别：{gender_ratio}" if gender_ratio else "",
    ) if part)
    if detail:
        append_block(detail, 2)
        cursor += 4
    evolution = str(item.row.get("evolution") or "").strip()
    if evolution:
        append_block(f"进化：{evolution}", 5)
    if not info_lines:
        append_block("暂无基础资料", 1)

    profile = str(item.row.get("profile") or "").strip() or "暂无图鉴摘要"
    profile_lines = tuple(_limited_wrapped_lines(draw, profile, _font(23), width, 12))
    content_height = max(cursor, len(profile_lines) * 32)
    height = max(960, info_top + content_height + 154)
    return _HeroLayout(height, name_font, name_lines, name_top, name_step,
                       tuple(type_positions), stats_top, info_top,
                       tuple(info_lines), profile_lines)


def render_draw_card(
    items: Sequence[DrawItem],
    *,
    nickname: str,
    remaining: int,
    daily_limit: int,
    dust: int,
    pity_ssr: int,
    ssr_pity_after: int,
    image_loader: ImageLoader | None = None,
    style: str = "classic",
) -> bytes:
    """切换今日卡面；未指定或未知样式时保留现有稀有度卡面。"""
    if style == "tcg":
        try:
            from .tcg_cards import render_tcg_draw as render
        except ImportError:
            from tcg_cards import render_tcg_draw as render
    else:
        try:
            from .rarity_cards import render_rarity_draw as render
        except ImportError:
            from rarity_cards import render_rarity_draw as render
    return render(
        items, nickname=nickname, remaining=remaining, daily_limit=daily_limit,
        dust=dust, pity_ssr=pity_ssr, ssr_pity_after=ssr_pity_after,
        image_loader=image_loader,
    )


def _draw_draw_header(
    draw: ImageDraw.ImageDraw,
    nickname: str,
    remaining: int,
    daily_limit: int,
    dust: int,
    pity_ssr: int,
    ssr_pity_after: int,
) -> None:
    """顶部信息条：标题、玩家与三个互不重叠的状态区。"""
    _pokeball(draw, PAD + 28, PAD + 38, 24)
    draw.text((PAD + 68, PAD + 5), "今日宝可梦", font=_font(46), fill=TEXT)
    draw.text(
        (PAD + 68, PAD + 67),
        _ellipsize(draw, f"训练家 · {nickname}", _font(22), 390),
        font=_font(22),
        fill=SUBTEXT,
    )
    status_x, status_w = 562, 226
    for i, (label, value, color) in enumerate((
        ("今日剩余次数", f"{remaining} / {daily_limit}", TEXT),
        ("星尘余额", str(dust), GOLD),
    )):
        x = status_x + i * (status_w + 18)
        _round_panel(draw, [x, PAD + 6, x + status_w, PAD + 86], fill=PANEL_ALT, outline=LINE, radius=16, width=1)
        draw.text((x + 18, PAD + 15), label, font=_font(18), fill=SUBTEXT)
        value_font = _fit_text_font(draw, value, status_w - 36, 30, 20)
        draw.text((x + 18, PAD + 41), _ellipsize(draw, value, value_font, status_w - 36), font=value_font, fill=color)

    draw.line([PAD, 192, W - PAD, 192], fill=LINE, width=2)
    if ssr_pity_after > 0:
        ratio = max(0.0, min(1.0, pity_ssr / max(1, ssr_pity_after)))
        bar_x, bar_w = 562, 224
        bar_y = PAD + 109
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + bar_w, bar_y + 8], radius=4, fill=LINE
        )
        if ratio > 0:
            color = GOLD if ratio >= 0.8 else RED
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + max(8, int(bar_w * ratio)), bar_y + 8],
                radius=4,
                fill=color,
            )
        draw.text(
            (bar_x + bar_w + 18, PAD + 97),
            f"SSR 保底 {pity_ssr}/{ssr_pity_after}",
            font=_font(19),
            fill=SUBTEXT,
        )


def _draw_hero_result(
    base: Image.Image,
    draw: ImageDraw.ImageDraw,
    item: DrawItem,
    image_loader: ImageLoader | None,
    layout: _HeroLayout,
) -> None:
    """单抽图鉴页：大立绘、独立能力区、分栏资料与结果条。"""
    top = 214
    art_size = 420
    color = RARITY_COLORS.get(item.rarity, RARITY_COLORS["C"])
    cx, cy = PAD + 40 + art_size // 2, top + 40 + art_size // 2
    stage = (248, 241, 217) if item.shiny else PANEL_ALT
    draw.ellipse([cx - 219, cy - 219, cx + 219, cy + 219], fill=stage)
    _pokeball(draw, cx, cy, 183, muted=True)
    draw.arc([cx - 214, cy - 214, cx + 214, cy + 214], 220, 305, fill=color, width=6)
    draw.arc([cx - 214, cy - 214, cx + 214, cy + 214], 40, 90, fill=color, width=6)
    art = image_loader(item.row) if image_loader else None
    if art is not None:
        _paste_art(base, art, (PAD + 40, top + 40, art_size, 0))
    else:
        _placeholder_art(draw, (PAD + 40, top + 40, art_size, 0), item.rarity)

    x = PAD + 40 + art_size + 48
    width = W - PAD - 40 - x

    # 稀有度徽章 + 新增/闪光标记
    badge_y = top + 22
    badge_end = _rarity_badge(draw, (x, badge_y), item.rarity)
    if item.is_new:
        badge_end = _pill(draw, (badge_end, badge_y), "NEW", NEW_GREEN) + 10
    if item.shiny:
        _pill(draw, (badge_end, badge_y), "闪光", GOLD)

    # 名字
    for index, name in enumerate(layout.name_lines):
        draw.text((x, layout.name_top + index * layout.name_step), name,
                  font=layout.name_font, fill=TEXT, anchor="lt")

    # 编号 + 属性
    dex_label = f"#{item.dex_raw}"
    dex_font = _fit_text_font(draw, dex_label, 160, 24, 18)
    draw.text((PAD + 40, top + 466), dex_label, font=dex_font, fill=SUBTEXT)
    for type_name, tx, ty in layout.type_positions:
        _type_pill(draw, (tx, ty), type_name)

    # 种族值
    stats = _stat_dict(item)
    if stats:
        sy = layout.stats_top
        draw.text((x, sy - 32), "种族值", font=_font(20), fill=SUBTEXT)
        _stat_bars(draw, x, sy, width - 20, stats, item.bst)
    else:
        draw.text(
            (x, layout.stats_top),
            f"种族值总和 {item.bst}",
            font=_font(26),
            fill=SUBTEXT,
        )

    # 立绘下方分成两栏：左侧基础资料与进化摘要，右侧图鉴摘要。
    info_top = layout.info_top
    left_x = PAD + 40
    line_font = _font(20)
    draw.line([PAD + 30, info_top - 61, W - PAD - 30, info_top - 61], fill=LINE, width=2)
    draw.text((left_x, info_top - 40), "宝可梦资料", font=_font(24), fill=TEXT)

    for line, offset in layout.info_lines:
        draw.text((left_x, info_top + offset), line, font=line_font, fill=SUBTEXT)

    # 右侧摘要使用完整半栏宽度，并显著增加可读行数。
    profile_font = _font(23)
    draw.text((x, info_top - 40), "图鉴摘要", font=_font(24), fill=TEXT)
    for index, line in enumerate(layout.profile_lines):
        draw.text((x, info_top + index * 32), line, font=profile_font, fill=SUBTEXT)

    # 结果说明
    note = (
        f"重复获得 · 转化为 {item.dust} 星尘"
        if not item.is_new
        else "新宝可梦已加入图鉴"
    )
    note_color = GOLD if not item.is_new else NEW_GREEN
    note_bg = (249, 243, 224) if not item.is_new else (230, 244, 235)
    height = layout.height
    _round_panel(draw, [PAD + 30, height - 128, W - PAD - 30, height - 80], fill=note_bg, radius=12)
    draw.text(
        (PAD + 48, height - 119),
        note,
        font=_font(24),
        fill=note_color,
    )


def _draw_result_grid(
    base: Image.Image,
    draw: ImageDraw.ImageDraw,
    items: Sequence[DrawItem],
    image_loader: ImageLoader | None,
    *,
    columns: int,
    top: int,
) -> None:
    """2–10 个结果：等宽网格，每个卡片含立绘、名字、属性与稀有度。"""
    gap = 18
    card_w = (W - PAD * 2 - gap * (columns - 1)) // columns
    card_h = 330 if columns == 3 else 460
    art_size = 138 if columns == 3 else 204

    for index, item in enumerate(items):
        row, col = divmod(index, columns)
        x = PAD + col * (card_w + gap)
        y = top + row * (card_h + gap)
        color = RARITY_COLORS.get(item.rarity, RARITY_COLORS["C"])
        _round_panel(
            draw,
            [x, y, x + card_w, y + card_h],
            fill=(250, 246, 230) if item.shiny else PANEL_ALT,
            outline=GOLD if item.shiny else LINE,
            width=3 if item.shiny else 1,
            radius=20,
        )
        draw.rounded_rectangle([x, y, x + card_w, y + 7], radius=4, fill=color)

        dex_font = _font(18)
        draw.text((x + 18, y + 17), _ellipsize(draw, f"#{item.dex_raw}", dex_font, card_w - 112), font=dex_font, fill=SUBTEXT)
        rarity_font = _font(19)
        tw = draw.textlength(item.rarity, font=rarity_font)
        draw.text((x + card_w - 18 - tw, y + 17), item.rarity, font=rarity_font, fill=color)
        art = image_loader(item.row) if image_loader else None
        art_x = x + (card_w - art_size) // 2
        art_y = y + 40
        cx, cy = x + card_w / 2, art_y + art_size / 2
        draw.ellipse([cx - art_size / 2 - 6, cy - art_size / 2 - 6, cx + art_size / 2 + 6, cy + art_size / 2 + 6], fill=PANEL)
        if art is not None:
            _paste_art(base, art, (art_x, art_y, art_size, 0))
        else:
            _placeholder_art(draw, (art_x, art_y, art_size, 0), item.rarity)

        name_y = art_y + art_size + 12
        name_font = _fit_text_font(
            draw,
            item.name,
            card_w - 28,
            25 if columns == 3 else 30,
            19 if columns == 3 else 22,
        )
        name = _ellipsize(draw, item.name, name_font, card_w - 28)
        tw = draw.textlength(name, font=name_font)
        draw.text(
            (x + (card_w - tw) / 2, name_y),
            name,
            font=name_font,
            fill=TEXT,
        )

        pill_y = name_y + (36 if columns == 3 else 40)
        types = [t.strip() for t in item.types.split("/") if t.strip()] or ["未知"]
        pill_font = _font(19)
        chips_width = sum(int(draw.textlength(t, font=pill_font)) + 26 for t in types) + (len(types) - 1) * 8
        tx = int(x + (card_w - chips_width) / 2)
        for type_name in types:
            tx = _type_pill(draw, (tx, pill_y), type_name, small=True)

        # 两列卡片补特性；三列保持单行种族值，避免越出卡片。
        detail_font = _font(19)
        stat_text = f"种族值总和 {item.bst}"
        tw = draw.textlength(stat_text, font=detail_font)
        draw.text(
            (x + (card_w - tw) / 2, pill_y + 42),
            stat_text,
            font=detail_font,
            fill=SUBTEXT,
        )
        if columns == 2:
            ability = str(item.row.get("abilities") or "").strip()
            if ability:
                ability_text = _ellipsize(
                    draw, f"特性：{ability}", detail_font, card_w - 28
                )
                tw = draw.textlength(ability_text, font=detail_font)
                draw.text(
                    (x + (card_w - tw) / 2, pill_y + 70),
                    ability_text,
                    font=detail_font,
                    fill=SUBTEXT,
                )

        tag = "NEW" if item.is_new else f"+{item.dust} 星尘"
        if item.shiny:
            tag = "闪光 " + tag
        tag_font = _font(21)
        tw = draw.textlength(tag, font=tag_font)
        draw.text(
            (x + (card_w - tw) / 2, y + card_h - 34),
            tag,
            font=tag_font,
            fill=GOLD if (item.shiny or not item.is_new) else NEW_GREEN,
        )


def render_collection_card(
    owned: Sequence[OwnedPartner],
    *,
    nickname: str,
    page: int,
    page_size: int,
    total: int,
    collected: int,
    image_loader: ImageLoader | None = None,
) -> bytes:
    """收藏图鉴：每页 ``page_size`` 个，按全国图鉴编号标记的6列网格。"""
    cols = 6
    rows = max(1, math.ceil(len(owned) / cols))
    cell_w, cell_h = 158, 240
    grid_top = 174
    height = grid_top + rows * (cell_h + 18) + 104
    base = _gradient((W, height)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    _starfield(draw, W, height)
    _frame(draw, height)
    _round_panel(draw, [PAD, PAD, W - PAD, PAD + 104], fill=PANEL, radius=22)

    draw.text((PAD + 28, PAD + 16), "我的宝可梦", font=_font(44), fill=TEXT)
    sub = _font(23)
    draw.text(
        (PAD + 30, PAD + 68),
        _ellipsize(draw, f"{nickname} · 已收集 {collected}/{total} · 第 {page} 页", sub, W - PAD * 2 - 60),
        font=sub,
        fill=SUBTEXT,
    )

    gap = (W - PAD * 2 - cols * cell_w) // (cols - 1)
    name_font, small = _font(21), _font(17)
    for index, owned_item in enumerate(owned):
        row, col = divmod(index, cols)
        x = PAD + col * (cell_w + gap)
        y = grid_top + row * (cell_h + 18)
        partner = owned_item.partner
        color = RARITY_COLORS.get(partner.rarity, RARITY_COLORS["C"])
        shiny = owned_item.shiny_count > 0
        _round_panel(
            draw,
            [x, y, x + cell_w, y + cell_h],
            fill=PANEL_ALT,
            outline=GOLD if shiny else color,
            width=3 if shiny else 1,
            radius=18,
        )
        draw.rounded_rectangle(
            [x, y, x + cell_w, y + 8], radius=4, fill=color
        )
        art = image_loader(partner.row) if image_loader else None
        art_box = (x + 34, y + 50, 90, 0)
        if art is None:
            _placeholder_art(draw, art_box, partner.rarity)
        else:
            _paste_art(base, art, art_box)
        dex_label = f"图鉴 #{partner.dex:04d}"
        dex_font = _font(18)
        dex_width = int(draw.textlength(dex_label, font=dex_font)) + 14
        draw.rounded_rectangle(
            [x + 8, y + 13, x + 8 + dex_width, y + 42],
            radius=8, fill=(251, 233, 230),
        )
        draw.text((x + 15, y + 17), dex_label, font=dex_font, fill=TEXT)
        display_name = partner.name
        form = str(partner.row.get("form_name") or "")
        species_name = str(partner.row.get("name_zh") or "")
        item_name_font = name_font
        if form:
            # A form already containing the species name is complete on its own.
            # Avoid repeating it and clipping the distinguishing form suffix.
            if species_name and species_name in form:
                display_name = form
            for size in range(21, 13, -1):
                item_name_font = _font(size)
                lines = _wrap(draw, display_name, item_name_font, cell_w - 20)
                if len(lines) <= 2:
                    break
            else:
                raise ValueError("完整形态名超出收藏卡片的空间，请使用文本列表。")
        else:
            lines = _limited_wrapped_lines(draw, display_name, item_name_font, cell_w - 20, 2)
        for line_index, line in enumerate(lines):
            tw = draw.textlength(line, font=item_name_font)
            draw.text(
                (x + (cell_w - tw) / 2, y + 146 + line_index * 25),
                line,
                font=item_name_font,
                fill=TEXT,
            )
        footer = f"持有×{owned_item.normal_count + owned_item.shiny_count}"
        if shiny:
            footer += f" · 闪光×{owned_item.shiny_count}"
        count_font = _fit_text_font(draw, footer, cell_w - 20, 17, 13)
        footer = _ellipsize(draw, footer, count_font, cell_w - 20)
        tw = draw.textlength(footer, font=count_font)
        draw.text(
            (x + (cell_w - tw) / 2, y + cell_h - 34),
            footer,
            font=count_font,
            fill=GOLD if shiny else SUBTEXT,
        )

    # 分页按"已收集"计算；total 是卡池规模，只用于展示进度。
    pages = max(1, math.ceil(collected / max(1, page_size)))
    foot = f"第 {page}/{pages} 页 · 数字即图鉴编号 · 挑战示例：宝可梦挑战 25 @群友 · 我的宝可梦 2 翻页"
    tw = draw.textlength(foot, font=small)
    draw.text(((W - tw) / 2, height - 66), foot, font=small, fill=SUBTEXT)
    return _encode(base)


def render_profile_card(
    summary: ProfileSummary,
    *,
    rank: int | None = None,
    rules: GachaRules | None = None,
) -> bytes:
    """训练家资料卡：进度、次数、星尘、保底。"""
    height = 780
    base = _gradient((W, height)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    _starfield(draw, W, height)
    _frame(draw, height)
    _round_panel(draw, [PAD, PAD, W - PAD, PAD + 160], fill=PANEL, radius=24)

    nickname_font = _fit_text_font(draw, summary.nickname, W - PAD * 2 - (260 if rank else 60), 46, 28)
    nickname = _ellipsize(draw, summary.nickname, nickname_font, W - PAD * 2 - (260 if rank else 60))
    draw.text((PAD + 30, PAD + 20), nickname, font=nickname_font, fill=TEXT)
    draw.text(
        (PAD + 32, PAD + 84),
        f"训练家档案 · 已召唤 {summary.total_draws} 次",
        font=_font(24),
        fill=SUBTEXT,
    )
    if rank:
        text = f"本群第 {rank} 名"
        tw = draw.textlength(text, font=_font(24))
        draw.text((W - PAD - 30 - tw, PAD + 30), text, font=_font(24), fill=GOLD)

    # 进度条
    bar_x, bar_y, bar_w, bar_h = PAD + 32, PAD + 124, W - PAD * 2 - 64, 18
    draw.rounded_rectangle(
        [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=9, fill=LINE
    )
    ratio = min(1.0, summary.percent / 100.0)
    if ratio > 0:
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + max(18, int(bar_w * ratio)), bar_y + bar_h],
            radius=9,
            fill=RED,
        )

    stats = [
        ("已收集", f"{summary.collected}/{summary.total}", f"{summary.percent:.1f}%"),
        ("闪光宝可梦", f"{summary.shiny_count}", "计数"),
        ("今日剩余", f"{summary.remaining}/{summary.daily_limit}", "次"),
        ("星尘", f"{summary.dust}", "余额"),
        ("SSR 保底", f"{summary.pity_ssr}/{rules.ssr_pity_after if rules else '—'}", ""),
        ("连续未出新", f"{summary.pity_new}", "次"),
    ]
    card_w = (W - PAD * 2 - 24) // 2
    card_h = 128
    top = PAD + 196
    for index, (label, value, note) in enumerate(stats):
        row, col = divmod(index, 2)
        x = PAD + col * (card_w + 24)
        y = top + row * (card_h + 18)
        _round_panel(draw, [x, y, x + card_w, y + card_h], fill=PANEL_ALT, radius=20)
        draw.text((x + 26, y + 20), label, font=_font(23), fill=SUBTEXT)
        value_font = _fit_text_font(draw, value, card_w - 52, 42, 24)
        draw.text((x + 26, y + 54), _ellipsize(draw, value, value_font, card_w - 52), font=value_font, fill=TEXT)
        if note:
            tw = draw.textlength(note, font=_font(24))
            draw.text(
                (x + card_w - 26 - tw, y + 24), note, font=_font(24), fill=GOLD
            )

    if summary.next_milestone:
        hint = f"下一收集里程碑：{summary.next_milestone} 位宝可梦"
    else:
        hint = "全部宝可梦已收集，图鉴完成！"
    day_hint = "发送「今日宝可梦」开始今天的邂逅。"
    draw.text((PAD + 4, height - 90), hint, font=_font(22), fill=SUBTEXT)
    draw.text((PAD + 4, height - 56), day_hint, font=_font(22), fill=SUBTEXT)
    return _encode(base)


def render_rarity_legend(
    rules: GachaRules,
    pool_counts: dict[str, int],
    effective_weights: dict[str, int] | None = None,
) -> bytes:
    """概率与规则说明卡。

    Args:
        rules: 抽卡规则。
        pool_counts: 各档卡池数量。
        effective_weights: 实际生效权重（已剔除空档位）。为 ``None`` 时退回配置权重。
    """
    height = 700
    base = _gradient((W, height)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    _starfield(draw, W, height)
    _frame(draw, height)
    draw.text((PAD, PAD), "稀有度与概率", font=_font(44), fill=TEXT)
    weights = effective_weights or dict(
        zip(("C", "R", "SR", "SSR"), rules.weight_list())
    )
    total_weight = sum(weights.values()) or 1
    y = PAD + 86
    for rarity in ("SSR", "SR", "R", "C"):
        color = RARITY_COLORS[rarity]
        _round_panel(draw, [PAD, y, W - PAD, y + 96], fill=PANEL_ALT, radius=18)
        draw.rounded_rectangle([PAD, y, PAD + 10, y + 96], radius=5, fill=color)
        _rarity_badge(draw, (PAD + 30, y + 26), rarity)
        pct = weights.get(rarity, 0) / total_weight * 100
        count = pool_counts.get(rarity, 0)
        draw.text(
            (W - PAD - 260, y + 24),
            f"{pct:g}%",
            font=_font(36),
            fill=color,
        )
        draw.text(
            (W - PAD - 260, y + 66),
            f"池内 {count} 位",
            font=_font(20),
            fill=SUBTEXT,
        )
        y += 108
    notes = [
        f"每次召唤固定 {rules.draws_per_command} 位；每日 {rules.daily_limit} 次。",
        (
            f"连续 {rules.ssr_pity_after} 次未出 SSR 时，下一次必出 SSR。"
            if rules.ssr_pity_after and pool_counts.get("SSR", 0)
            else "SSR 保底未启用（已关闭或卡池无 SSR）。"
        ),
        f"闪光概率 {rules.shiny_chance_percent:g}%，重复宝可梦折算星尘。",
    ]
    for note in notes:
        draw.text((PAD, y), note, font=_font(22), fill=SUBTEXT)
        y += 34
    return _encode(base)


def _encode(image: Image.Image, *, quality: int = 92) -> bytes:
    """统一输出 JPEG，控制单图体积。"""
    buf = io.BytesIO()
    image.convert("RGB").save(
        buf, format="JPEG", quality=quality, optimize=True, progressive=True
    )
    return buf.getvalue()
