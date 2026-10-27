"""伙伴系统的 Pillow 卡片渲染。

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
            title += "  ✦"
        draw.text((tx, y + 24), title, font=name_font, fill=GOLD if item.shiny else TEXT)
        meta = f"{item.types or '未知属性'} · 种族值 {item.bst}"
        draw.text((tx, y + 74), meta, font=meta_font, fill=SUBTEXT)
        if item.shiny:
            draw.text(
                (tx, y + 108),
                "闪光个体 · 星尘加成",
                font=small,
                fill=GOLD,
            )
        elif item.is_new:
            draw.text((tx, y + 108), "新伙伴已加入图鉴", font=small, fill=NEW_GREEN)
        else:
            draw.text(
                (tx, y + 108),
                f"重复获得 · 转化为 {item.dust} 星尘",
                font=small,
                fill=SUBTEXT,
            )

        badge_x = W - PAD - 190
        _rarity_badge(draw, (badge_x, y + 22), item.rarity, small=True)
        status = "NEW" if item.is_new else f"+{item.dust} ✦"
        status_color = NEW_GREEN if item.is_new else GOLD
        sf = _font(26)
        tw = draw.textlength(status, font=sf)
        draw.text((W - PAD - 24 - tw, y + 92), status, font=sf, fill=status_color)
        y += 166
    return y


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
    """抽卡结果卡片。固定抽取数量最多 10，一屏可读。"""
    count = max(1, len(items))
    height = 190 + count * 166 + 120
    base = _gradient((W, height)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    _starfield(draw, W, height)
    _round_panel(draw, [16, 16, W - 16, height - 16], fill=(17, 24, 43), radius=28)
    _round_panel(draw, [PAD, PAD, W - PAD, PAD + 108], fill=PANEL, radius=22)

    title_font = _font(46)
    draw.text((PAD + 28, PAD + 18), "伙伴召唤", font=title_font, fill=TEXT)
    sub_font = _font(23)
    draw.text(
        (PAD + 30, PAD + 74),
        f"{nickname} · 今日剩余 {remaining}/{daily_limit} 次",
        font=sub_font,
        fill=SUBTEXT,
    )
    right = f"星尘 {dust}"
    tw = draw.textlength(right, font=sub_font)
    draw.text((W - PAD - 28 - tw, PAD + 26), right, font=sub_font, fill=GOLD)
    if ssr_pity_after > 0:
        pity = f"SSR 保底 {pity_ssr}/{ssr_pity_after}"
        tw = draw.textlength(pity, font=sub_font)
        draw.text((W - PAD - 28 - tw, PAD + 64), pity, font=sub_font, fill=SUBTEXT)

    _draw_result_rows(base, draw, items, image_loader)

    foot_font = _font(21)
    draw.text(
        (PAD + 4, height - 78),
        "重复伙伴自动折算星尘，可在「伙伴兑换」定向获得未拥有伙伴。",
        font=foot_font,
        fill=SUBTEXT,
    )
    return _encode(base)


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

    draw.text((PAD + 28, PAD + 16), "伙伴图鉴", font=_font(44), fill=TEXT)
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
            footer += f"  ✦{owned_item.shiny_count}"
        tw = draw.textlength(footer, font=small)
        draw.text(
            (x + (cell_w - tw) / 2, y + cell_h - 34),
            footer,
            font=small,
            fill=GOLD if shiny else SUBTEXT,
        )

    pages = max(1, math.ceil(total / max(1, page_size)))
    foot = f"第 {page}/{pages} 页 · 伙伴图鉴 <页码> 翻页 · 伙伴兑换 <名称> 定向获取"
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
        ("闪光伙伴", f"{summary.shiny_count}", "✦"),
        ("今日剩余", f"{summary.remaining}/{summary.daily_limit}", "次"),
        ("星尘", f"{summary.dust}", "✦"),
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
        hint = f"下一收集里程碑：{summary.next_milestone} 位伙伴"
    else:
        hint = "全部伙伴已收集，图鉴完成！"
    day_hint = "发送「伙伴召唤」开始今天的邂逅。"
    draw.text((PAD + 4, height - 76), hint, font=_font(22), fill=SUBTEXT)
    draw.text((PAD + 4, height - 42), day_hint, font=_font(22), fill=SUBTEXT)
    return _encode(base)


def render_rarity_legend(rules: GachaRules, pool_counts: dict[str, int]) -> bytes:
    """概率与规则说明卡，帮助页和抽卡前提示共用。"""
    height = 700
    base = _gradient((W, height)).convert("RGBA")
    draw = ImageDraw.Draw(base)
    _starfield(draw, W, height)
    _round_panel(draw, [16, 16, W - 16, height - 16], fill=(17, 24, 43), radius=28)
    draw.text((PAD, PAD), "稀有度与概率", font=_font(44), fill=TEXT)
    total_weight = sum(rules.weight_list()) or 1
    y = PAD + 86
    for rarity in ("SSR", "SR", "R", "C"):
        color = RARITY_COLORS[rarity]
        _round_panel(draw, [PAD, y, W - PAD, y + 96], fill=PANEL_ALT, radius=18)
        draw.rounded_rectangle([PAD, y, PAD + 10, y + 96], radius=5, fill=color)
        _rarity_badge(draw, (PAD + 30, y + 26), rarity)
        pct = rules.weights.get(rarity, 0) / total_weight * 100
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
        f"连续 {rules.ssr_pity_after} 次未出 SSR 时，下一次必出 SSR。" if rules.ssr_pity_after else "SSR 保底已关闭。",
        f"闪光概率 {rules.shiny_chance_percent:g}%，重复伙伴折算星尘。",
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
