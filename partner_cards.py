"""宝可梦系统的 Pillow 卡片渲染。

与 ``card_renderer.py`` 共用字体查找逻辑，但版式独立：抽卡结果、收藏图鉴、
训练家资料三张卡片都使用深色收藏册风格，避免和图鉴卡混淆。

渲染函数只接收普通数据，不读配置、不访问数据库，便于单测与替换。
"""

from __future__ import annotations

import io
import math
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

BG_TOP = (12, 18, 34)
BG_BOTTOM = (24, 31, 54)
PANEL = (28, 37, 61)
PANEL_ALT = (34, 44, 71)
TEXT = (238, 242, 255)
SUBTEXT = (148, 163, 184)
LINE = (51, 65, 95)
GOLD = (250, 204, 21)
NEW_GREEN = (52, 211, 153)

RARITY_COLORS: dict[str, tuple[int, int, int]] = {
    "C": (100, 116, 139),
    "R": (56, 132, 255),
    "SR": (168, 85, 247),
    "SSR": (245, 158, 11),
}
RARITY_GLOW: dict[str, tuple[int, int, int]] = {
    "C": (71, 85, 105),
    "R": (30, 64, 175),
    "SR": (88, 28, 135),
    "SSR": (146, 64, 14),
}

# 属性配色（与官方属性色接近，保证不同属性在深色背景上可区分）
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
    """确定性星点，避免每次渲染都变化。"""
    seed = 20261027
    for index in range(70):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        x = seed % width
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        y = seed % height
        radius = 1 + (index % 3 == 0)
        color = (58, 74, 110) if index % 4 else (76, 96, 140)
        draw.ellipse([x, y, x + radius, y + radius], fill=color)


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
        (x + 11, y + 10), label, font=font, fill=(15, 23, 42) if color == GOLD else (255, 255, 255)
    )
    return x + w


def _type_pill(
    draw: ImageDraw.ImageDraw, xy: tuple[int, int], type_name: str
) -> int:
    """属性胶囊，颜色取自官方属性配色，返回右边界。"""
    color = TYPE_COLORS.get(type_name, (100, 116, 139))
    font = _font(24)
    tw = draw.textlength(type_name, font=font)
    x, y = xy
    w, h = int(tw) + 26, 36
    draw.rounded_rectangle([x, y, x + w, y + h], radius=18, fill=color)
    luminance = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
    text_color = (15, 23, 42) if luminance > 170 else (255, 255, 255)
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
            [bar_x, top + 8, bar_x + bar_w, top + 24], radius=8, fill=(15, 23, 42)
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
    x, y, size, _ = box
    image = art.convert("RGBA")
    image.thumbnail((size, size), Image.Resampling.LANCZOS)
    base.paste(
        image,
        (x + (size - image.width) // 2, y + (size - image.height) // 2),
        image,
    )


def _placeholder_art(
    draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rarity: str
) -> None:
    x, y, size, _ = box
    color = RARITY_GLOW.get(rarity, RARITY_GLOW["C"])
    draw.rounded_rectangle(
        [x, y, x + size, y + size], radius=18, fill=(21, 29, 49), outline=color, width=2
    )
    cx, cy = x + size / 2, y + size / 2
    for angle in range(0, 360, 60):
        rad = math.radians(angle)
        draw.ellipse(
            [
                cx + math.cos(rad) * size * 0.26 - 3,
                cy + math.sin(rad) * size * 0.26 - 3,
                cx + math.cos(rad) * size * 0.26 + 3,
                cy + math.sin(rad) * size * 0.26 + 3,
            ],
            fill=color,
        )
    draw.ellipse(
        [cx - size * 0.12, cy - size * 0.12, cx + size * 0.12, cy + size * 0.12],
        outline=color,
        width=3,
    )


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
) -> bytes:
    """抽卡结果卡片：以宝可梦立绘为视觉主体。

    单个结果用大图展示（立绘 + 名字 + 属性 + 稀有度），多个结果用网格。
    图片缺失时用同尺寸占位，版式不会塌。
    """
    count = max(1, len(items))
    if count == 1:
        height = 1180
    else:
        columns, rows, card_h = _grid_layout(count)
        height = 214 + rows * card_h + (rows - 1) * 18 + 96
    base = _gradient((W, height)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    _starfield(draw, W, height)
    _round_panel(draw, [16, 16, W - 16, height - 16], fill=(17, 24, 43), radius=28)
    _draw_draw_header(draw, nickname, remaining, daily_limit, dust, pity_ssr, ssr_pity_after)

    if count == 1:
        _draw_hero_result(base, draw, items[0], image_loader, height)
    else:
        columns, _rows, _card_h = _grid_layout(count)
        _draw_result_grid(base, draw, items, image_loader, columns=columns, top=214)

    foot_font = _font(21)
    draw.text(
        (PAD + 4, height - 62),
        "重复宝可梦自动折算星尘 · 兑换宝可梦 <名称> 可定向获得",
        font=foot_font,
        fill=SUBTEXT,
    )
    return _encode(base)


def _draw_draw_header(
    draw: ImageDraw.ImageDraw,
    nickname: str,
    remaining: int,
    daily_limit: int,
    dust: int,
    pity_ssr: int,
    ssr_pity_after: int,
) -> None:
    """顶部信息条：标题、玩家、剩余次数、星尘与保底。"""
    _round_panel(draw, [PAD, PAD, W - PAD, PAD + 132], fill=PANEL, radius=24)
    draw.text((PAD + 30, PAD + 16), "今日宝可梦", font=_font(46), fill=TEXT)
    draw.text(
        (PAD + 32, PAD + 78),
        _ellipsize(draw, nickname, _font(24), 420),
        font=_font(24),
        fill=SUBTEXT,
    )

    # 右上：星尘（金色高亮）
    dust_text = f"星尘 {dust}"
    font_dust = _font(30)
    tw = draw.textlength(dust_text, font=font_dust)
    draw.text((W - PAD - 30 - tw, PAD + 22), dust_text, font=font_dust, fill=GOLD)

    # 右下：剩余次数胶囊
    cap_text = f"今日剩余 {remaining}/{daily_limit}"
    font_cap = _font(22)
    tw = draw.textlength(cap_text, font=font_cap)
    cap_w, cap_h = int(tw) + 30, 38
    cap_x, cap_y = W - PAD - 30 - cap_w, PAD + 70
    draw.rounded_rectangle(
        [cap_x, cap_y, cap_x + cap_w, cap_y + cap_h],
        radius=19,
        fill=(30, 41, 59),
        outline=LINE,
    )
    draw.text((cap_x + 15, cap_y + 7), cap_text, font=font_cap, fill=TEXT)

    if ssr_pity_after > 0:
        # 保底进度条：接近保底时变金色
        ratio = max(0.0, min(1.0, pity_ssr / max(1, ssr_pity_after)))
        bar_x, bar_w = PAD + 32, 300
        bar_y = PAD + 108
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + bar_w, bar_y + 8], radius=4, fill=(15, 23, 42)
        )
        if ratio > 0:
            color = GOLD if ratio >= 0.8 else (99, 102, 241)
            draw.rounded_rectangle(
                [bar_x, bar_y, bar_x + max(8, int(bar_w * ratio)), bar_y + 8],
                radius=4,
                fill=color,
            )
        draw.text(
            (bar_x + bar_w + 14, PAD + 96),
            f"SSR 保底 {pity_ssr}/{ssr_pity_after}",
            font=_font(19),
            fill=SUBTEXT,
        )


def _draw_hero_result(
    base: Image.Image,
    draw: ImageDraw.ImageDraw,
    item: DrawItem,
    image_loader: ImageLoader | None,
    height: int,
) -> None:
    """单个结果：左侧大立绘，右侧信息；稀有度光环 + 闪光光晕。"""
    top = 190
    art_size = 420
    glow = RARITY_GLOW.get(item.rarity, RARITY_GLOW["C"])

    # 背景光环
    cx, cy = PAD + 40 + art_size // 2, top + 40 + art_size // 2
    for radius, alpha in ((300, 34), (240, 52), (180, 74)):
        draw.ellipse(
            [cx - radius, cy - radius, cx + radius, cy + radius],
            outline=(*glow, alpha),
            width=3,
        )
    draw.ellipse(
        [cx - 200, cy - 200, cx + 200, cy + 200], fill=(*glow, 26)
    )

    art = image_loader(item.row) if image_loader else None
    if art is not None:
        _paste_art(base, art, (PAD + 40, top + 40, art_size, 0))
    else:
        _placeholder_art(draw, (PAD + 40, top + 40, art_size, 0), item.rarity)

    x = PAD + 40 + art_size + 48
    width = W - PAD - 40 - x

    # 稀有度徽章 + 新增/闪光标记
    badge_y = top + 40
    badge_end = _rarity_badge(draw, (x, badge_y), item.rarity)
    if item.is_new:
        _pill(draw, (badge_end, badge_y), "NEW", NEW_GREEN)
    if item.shiny:
        _pill(draw, (badge_end + (76 if item.is_new else 0), badge_y), "闪光", GOLD)

    # 名字
    name_font = _font(58)
    name = _ellipsize(draw, item.name, name_font, width)
    draw.text((x, badge_y + 62), name, font=name_font, fill=GOLD if item.shiny else TEXT)

    # 编号 + 属性
    meta_font = _font(26)
    draw.text((x, badge_y + 140), f"#{item.dex_raw}", font=meta_font, fill=SUBTEXT)
    tx = x + 110
    for type_name in [t.strip() for t in item.types.split("/") if t.strip()]:
        tx = _type_pill(draw, (tx, badge_y + 136), type_name)

    # 种族值
    stats = _stat_dict(item)
    if stats:
        sy = badge_y + 200
        _stat_bars(draw, x, sy, width - 20, stats, item.bst)
    else:
        draw.text(
            (x, badge_y + 204),
            f"种族值总和 {item.bst}",
            font=_font(26),
            fill=SUBTEXT,
        )

    # 基础信息：分类 / 身高体重 / 特性
    info_top = top + art_size + 18
    info = _info_lines(item)
    line_font = _font(23)
    for index, line in enumerate(info):
        draw.text(
            (PAD + 40, info_top + index * 40),
            _ellipsize(draw, line, line_font, W - PAD * 2 - 80),
            font=line_font,
            fill=SUBTEXT,
        )

    # 弱点 / 抗性（最多两行）
    matchups = _matchup_lines(item, line_font)
    for index, line in enumerate(matchups):
        draw.text(
            (PAD + 40, info_top + len(info) * 40 + 8 + index * 40),
            _ellipsize(draw, line, line_font, W - PAD * 2 - 80),
            font=line_font,
            fill=SUBTEXT,
        )

    extra_lines = []
    egg_groups = str(item.row.get("egg_groups") or "").strip()
    gender_ratio = str(item.row.get("gender_ratio") or "").strip()
    if egg_groups or gender_ratio:
        extra_lines.append(
            " · ".join(
                part
                for part in (
                    f"蛋群：{egg_groups}" if egg_groups else "",
                    f"性别：{gender_ratio}" if gender_ratio else "",
                )
                if part
            )
        )
    evolution = str(item.row.get("evolution") or "").strip()
    if evolution:
        extra_lines.append(f"进化：{evolution}")
    extra_top = info_top + (len(info) + len(matchups)) * 40 + 16
    for index, line in enumerate(extra_lines[:2]):
        draw.text(
            (PAD + 40, extra_top + index * 40),
            _ellipsize(draw, line, line_font, W - PAD * 2 - 80),
            font=line_font,
            fill=SUBTEXT,
        )

    # 右侧摘要利用立绘与种族值下方的空间，让单抽既有视觉主体也有可读资料。
    profile = str(item.row.get("profile") or "").strip()
    if profile:
        profile_font = _font(21)
        profile_lines = _wrap(draw, profile, profile_font, width)
        profile_lines = [
            line if index == 0 else line.lstrip("，。；：、！？")
            for index, line in enumerate(profile_lines)
        ]
        if len(profile_lines) > 5:
            profile_lines = profile_lines[:5]
            profile_lines[-1] = _ellipsize(
                draw, profile_lines[-1] + "…", profile_font, width
            )
        draw.text((x, top + 510), "图鉴摘要", font=_font(23), fill=SUBTEXT)
        for index, line in enumerate(profile_lines):
            draw.text(
                (x, top + 548 + index * 31),
                line,
                font=profile_font,
                fill=SUBTEXT,
            )

    # 结果说明
    note = (
        f"重复获得 · 转化为 {item.dust} 星尘"
        if not item.is_new
        else "新宝可梦已加入图鉴"
    )
    draw.text(
        (PAD + 40, height - 108),
        note,
        font=_font(24),
        fill=GOLD if not item.is_new else NEW_GREEN,
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
    art_size = min(card_w - 36, 160) if columns == 3 else min(card_w - 48, 228)

    for index, item in enumerate(items):
        row, col = divmod(index, columns)
        x = PAD + col * (card_w + gap)
        y = top + row * (card_h + gap)
        color = RARITY_COLORS.get(item.rarity, RARITY_COLORS["C"])
        _round_panel(
            draw,
            [x, y, x + card_w, y + card_h],
            fill=PANEL_ALT,
            outline=GOLD if item.shiny else LINE,
            width=3 if item.shiny else 1,
            radius=20,
        )
        draw.rounded_rectangle([x, y, x + card_w, y + 7], radius=4, fill=color)

        art = image_loader(item.row) if image_loader else None
        art_x = x + (card_w - art_size) // 2
        art_y = y + 20
        if art is not None:
            _paste_art(base, art, (art_x, art_y, art_size, 0))
        else:
            _placeholder_art(draw, (art_x, art_y, art_size, 0), item.rarity)

        name_y = art_y + art_size + 12
        name_font = _font(25 if columns == 3 else 30)
        name = _ellipsize(draw, item.name, name_font, card_w - 28)
        tw = draw.textlength(name, font=name_font)
        draw.text(
            (x + (card_w - tw) / 2, name_y),
            name,
            font=name_font,
            fill=GOLD if item.shiny else TEXT,
        )

        pill_y = name_y + (36 if columns == 3 else 40)
        pill_font = _font(19)
        chip_text = f"{item.rarity} · {item.types or '—'}"
        tw = draw.textlength(chip_text, font=pill_font)
        chip_w = int(tw) + 22
        draw.rounded_rectangle(
            [x + (card_w - chip_w) / 2, pill_y, x + (card_w + chip_w) / 2, pill_y + 30],
            radius=15,
            fill=color,
        )
        draw.text(
            (x + (card_w - tw) / 2, pill_y + 4),
            chip_text,
            font=pill_font,
            fill=(255, 255, 255),
        )

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
    """收藏图鉴：每页 ``page_size`` 个，6 列网格。"""
    cols = 6
    rows = max(1, math.ceil(len(owned) / cols))
    cell_w, cell_h = 158, 196
    grid_top = 174
    height = grid_top + rows * (cell_h + 18) + 104
    base = _gradient((W, height)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    _starfield(draw, W, height)
    _round_panel(draw, [16, 16, W - 16, height - 16], fill=(17, 24, 43), radius=28)
    _round_panel(draw, [PAD, PAD, W - PAD, PAD + 104], fill=PANEL, radius=22)

    draw.text((PAD + 28, PAD + 16), "我的宝可梦", font=_font(44), fill=TEXT)
    sub = _font(23)
    draw.text(
        (PAD + 30, PAD + 68),
        f"{nickname} · 已收集 {collected}/{total} · 第 {page} 页",
        font=sub,
        fill=SUBTEXT,
    )

    gap = (W - PAD * 2 - cols * cell_w) // (cols - 1)
    name_font, small = _font(23), _font(19)
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
        art_box = (x + 38, y + 20, 82, 0)
        if art is None:
            _placeholder_art(draw, art_box, partner.rarity)
        else:
            _paste_art(base, art, art_box)
        lines = _wrap(draw, partner.name, name_font, cell_w - 20)[:2]
        for line_index, line in enumerate(lines):
            tw = draw.textlength(line, font=name_font)
            draw.text(
                (x + (cell_w - tw) / 2, y + 112 + line_index * 28),
                line,
                font=name_font,
                fill=TEXT,
            )
        footer = f"#{partner.dex_raw}"
        if shiny:
            footer += f"  闪光×{owned_item.shiny_count}"
        tw = draw.textlength(footer, font=small)
        draw.text(
            (x + (cell_w - tw) / 2, y + cell_h - 34),
            footer,
            font=small,
            fill=GOLD if shiny else SUBTEXT,
        )

    # 分页按"已收集"计算；total 是卡池规模，只用于展示进度。
    pages = max(1, math.ceil(len(owned) / max(1, page_size))) if owned else 1
    foot = f"第 {page}/{pages} 页 · 我的宝可梦 <页码> 翻页 · 兑换宝可梦 <名称> 定向获取"
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
    _round_panel(draw, [16, 16, W - 16, height - 16], fill=(17, 24, 43), radius=28)
    _round_panel(draw, [PAD, PAD, W - PAD, PAD + 160], fill=PANEL, radius=24)

    draw.text((PAD + 30, PAD + 20), summary.nickname, font=_font(46), fill=TEXT)
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
        [bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], radius=9, fill=(15, 23, 42)
    )
    ratio = min(1.0, summary.percent / 100.0)
    if ratio > 0:
        draw.rounded_rectangle(
            [bar_x, bar_y, bar_x + max(18, int(bar_w * ratio)), bar_y + bar_h],
            radius=9,
            fill=(99, 102, 241),
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
        draw.text((x + 26, y + 54), value, font=_font(42), fill=TEXT)
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
    draw.text((PAD + 4, height - 76), hint, font=_font(22), fill=SUBTEXT)
    draw.text((PAD + 4, height - 42), day_hint, font=_font(22), fill=SUBTEXT)
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
    _round_panel(draw, [16, 16, W - 16, height - 16], fill=(17, 24, 43), radius=28)
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
