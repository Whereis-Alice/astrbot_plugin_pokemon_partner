"""Three Pokémon battle images: poster, both movesets, and the complete process.

Rendering uses a completed battle snapshot; it never simulates a second battle.
Move descriptions and every turn event are retained in measured long images.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

try:
    from .battle_engine import calc_stats
    from .card_renderer import load_font, TYPE_COLORS
    from .tcg_symbols import render_energy_icon
    from .battle_card_style import arena_stock, report_stock, section_rule, hp_track
except ImportError:
    from battle_engine import calc_stats
    from card_renderer import load_font, TYPE_COLORS
    from tcg_symbols import render_energy_icon
    from battle_card_style import arena_stock, report_stock, section_rule, hp_track

WIDTH = 1200
# JPEG dimensions must remain below 65500. Measure before allocating a canvas;
# excessive external snapshots fail explicitly rather than lose any content.
MAX_HEIGHT = 60000
PAD = 54
BG = (15, 28, 39)
PANEL = (21, 35, 47)
BORDER = (46, 66, 82)
TEXT = (245, 248, 250)
MUTED = (177, 192, 208)
FAINT = (161, 182, 200)
GOLD = (255, 212, 115)
MINT = (126, 225, 183)

ImageLoader = Callable[[dict], Image.Image | bytes | None]


@lru_cache(maxsize=48)
def _font(size: int):
    return load_font(size)


@dataclass(frozen=True)
class _Line:
    text: str
    size: int = 26
    color: tuple[int, int, int] = MUTED
    gap: int = 7


@dataclass(frozen=True)
class _Block:
    title: str
    lines: tuple[_Line, ...]
    accent: tuple[int, int, int] = MINT
    type_name: str | None = None


def _blend(a, b, ratio):
    return tuple(round(a[i] * (1 - ratio) + b[i] * ratio) for i in range(3))


def _color(row: dict):
    kind = str(row.get("types") or "一般").split("/")[0]
    return _blend(TYPE_COLORS.get(kind, (96, 154, 190)), (255, 255, 255), 0.2)


def _wrap(draw, text: str, size: int, width: int) -> list[str]:
    font = _font(size)
    closing = "，。；：、！？）】〉》」』,.;:!?)]}"
    output, current = [], ""
    for char in str(text):
        if char == "\n":
            output.append(current)
            current = ""
        elif current and draw.textlength(current + char, font=font) > width:
            tail = re.search(r"[A-Za-z0-9_./%]+$", current) if char.isascii() else None
            if char in closing and len(current) > 1:
                split = len(current) - 1
                while split > 0 and (current[split] in closing or current[split].isspace()):
                    split -= 1
                if split:
                    output.append(current[:split])
                    current = current[split:] + char
                else:
                    output.append(current)
                    current = char
            elif (
                tail
                and tail.start() > 0
                and draw.textlength(tail.group() + char, font=font) <= width
            ):
                output.append(current[: tail.start()])
                current = tail.group() + char
            else:
                output.append(current)
                current = char
        else:
            current += char
    if current or not output:
        output.append(current)
    return output


def _text(draw, xy, text, size=26, color=TEXT):
    draw.text(xy, str(text), font=_font(size), fill=color, anchor="lt")


def _fitted(draw, text, width, max_size=52, min_size=30):
    for size in range(max_size, min_size - 1, -2):
        if draw.textlength(text, font=_font(size)) <= width:
            return size, [text]
    lines = _wrap(draw, text, min_size, width)
    if len(lines) > 3:
        lines = lines[:3]
        while draw.textlength(lines[-1] + "…", font=_font(min_size)) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return min_size, lines


def _single_line(draw, text, size, width):
    if draw.textlength(text, font=_font(size)) <= width:
        return text
    while text and draw.textlength(text + "…", font=_font(size)) > width:
        text = text[:-1]
    return text + "…"


def _owned_shiny(battle, index):
    items = battle.get("owned_items") or []
    if index >= len(items):
        return False
    item = items[index]
    return bool(
        item.get("shiny") if isinstance(item, dict) else getattr(item, "shiny", False)
    )


def _ball(draw, cx, cy, radius, color, width=2):
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius], outline=color, width=width
    )
    draw.line([cx - radius, cy, cx + radius, cy], fill=color, width=width)
    draw.ellipse(
        [cx - radius * 0.2, cy - radius * 0.2, cx + radius * 0.2, cy + radius * 0.2],
        fill=BG,
        outline=color,
        width=width,
    )


def _base(height: int, accents=None):
    canvas = arena_stock(WIDTH, height, accents) if accents else report_stock(WIDTH, height)
    draw = ImageDraw.Draw(canvas)
    return canvas, draw


def _header(draw, title, eyebrow, seed):
    _ball(draw, PAD + 16, 62, 16, MINT, 2)
    _text(draw, (PAD + 46, 45), "POKÉMON  /  ARENA ARCHIVE", 22, MINT)
    _text(draw, (WIDTH - PAD - 230, 48), f"SEED {seed}", 20, FAINT)
    _text(draw, (PAD, 103), title, 44)
    _text(draw, (PAD, 162), eyebrow, 24, MUTED)
    section_rule(draw, PAD, 207, WIDTH-2*PAD, MINT)


def _load_art(loader: ImageLoader | None, row: dict):
    if loader is None:
        return None
    try:
        art = loader(row)
        if isinstance(art, bytes):
            with Image.open(io.BytesIO(art)) as image:
                return image.convert("RGBA")
        if isinstance(art, Image.Image):
            return art.convert("RGBA")
    except Exception:
        pass
    return None


def _art(canvas, draw, row, loader, center, top, side, color):
    draw.arc([center-side//2, top+15, center+side//2, top+side-15],
             203, 280, fill=_blend(BORDER,color,.60), width=2)
    art = _load_art(loader, row)
    if art is None:
        _ball(draw, center, top + side // 2, 90, _blend(BORDER, color, 0.7), 6)
        _text(draw, (center - 104, top + side - 60), "立绘暂未缓存", 26, FAINT)
        return
    bbox = art.getbbox()
    if not bbox:
        _ball(draw, center, top + side // 2, 90, _blend(BORDER, color, 0.7), 6)
        _text(draw, (center - 104, top + side - 60), "立绘暂未缓存", 26, FAINT)
        return
    art = art.crop(bbox)
    art.thumbnail((side - 30, side - 20), Image.Resampling.LANCZOS)
    x, y = center - art.width // 2, top + (side - art.height) // 2
    silhouette = Image.new("RGBA", art.size, (*color, 0))
    silhouette.putalpha(art.getchannel("A").point(lambda a: round(a * 0.32)))
    glow = Image.new("RGBA", canvas.size)
    glow.alpha_composite(silhouette, (x, y + 12))
    glow = glow.filter(ImageFilter.GaussianBlur(17))
    canvas.paste(glow, (0, 0), glow)
    canvas.paste(art, (x, y), art)


def _stats(row, level):
    values = re.search(
        r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)",
        str(row.get("stats") or ""),
    )
    return (
        calc_stats(
            dict(
                zip(
                    ("HP", "攻击", "防御", "特攻", "特防", "速度"),
                    map(int, values.groups()),
                )
            ),
            level,
        )
        if values
        else {}
    )


def _display_name(row, name):
    form = str(row.get("form_name") or "")
    if not form:
        return name
    suffix = (
        "（甲）"
        if str(name).endswith("（甲）")
        else "（乙）"
        if str(name).endswith("（乙）")
        else ""
    )
    return form + suffix


def _poster(battle, loader):
    result = battle["result"]
    rows = (battle["row1"], battle["row2"])
    colors = [_color(row) for row in rows]
    canvas, draw = _base(1420, [(250, 420, colors[0]), (950, 420, colors[1])])
    _header(
        draw,
        "宝可梦挑战" if battle.get("trainer_names") else "宝可梦对战",
        f"Lv.{battle['level']}  /  {result['turns']} 回合  /  对阵与最终战况",
        battle["seed"],
    )
    draw.line((598, 731, 598, 1110), fill=BORDER, width=1)
    centers = (300, 900)
    for index, (row, color, center) in enumerate(zip(rows, colors, centers)):
        label_color = _blend(color, TEXT, .30)
        art_row = dict(row)
        art_row.pop("_artwork_shiny", None)
        if _owned_shiny(battle, index):
            art_row["_artwork_shiny"] = True
        _art(canvas, draw, art_row, loader, center, 238, 442, color)
        trainers = battle.get("trainer_names") or []
        trainer = (
            f"训练家 · {trainers[index]}"
            if index < len(trainers)
            else f"CHALLENGER {index + 1:02d}"
        )
        _text(
            draw, (center - 220, 703), _single_line(draw, trainer, 24, 440), 24, label_color
        )
        section_rule(draw, center-220, 733, 440, color)
        setup = result["setup"][index]
        size, names = _fitted(draw, _display_name(row, setup["name"]), 440)
        y = 741
        for line in names:
            _text(draw, (center - 220, y), line, size)
            y += size + 7
        x = center - 220
        types = [kind for kind in str(row.get("types") or "").split("/") if kind]
        for kind in types:
            icon = render_energy_icon(kind, 31)
            canvas.paste(icon, (x, 875), icon)
            x += 40
        kind = f"Lv.{battle['level']}" + ("  ·  闪光个体" if _owned_shiny(battle, index) else "")
        if not types:
            kind = "属性未收录  /  " + kind
        _text(draw, (x + 4, 878), kind, 25, label_color)
        final = result["final"][setup["name"]]
        _text(draw, (center - 220, 927), f"HP {final['hp']} / {final['max']}", 31)
        _text(draw, (center + 129, 930), f"{final['pct']}%", 26, label_color)
        pct = max(0, min(1, final["hp"] / max(1, final["max"])))
        hp_track(draw, (center-216, 974, center+220, 990), pct,
                 color if pct>.25 else (246, 148, 122))
        label = "倒下" if final.get("fainted") else final.get("status") or "战斗结束"
        _text(draw, (center - 220, 1005), label, 24, MUTED)
        stats = setup.get("stats") or _stats(row, battle["level"])
        for n, key in enumerate(("攻击", "防御", "特攻", "特防", "速度")):
            sx = center - 220 + (n % 3) * 150
            sy = 1055 + (n // 3) * 42
            _text(draw, (sx, sy), f"{key} {stats.get(key, '—')}", 25, MUTED)
    draw.polygon(((598, 406),(656,457),(598,515),(540,457)), fill=(12,24,35), outline=(149,179,188), width=2)
    _text(draw, (556, 434), "VS", 52, GOLD)
    win = result["winner"]
    winner_name = next(
        (
            _display_name(row, setup["name"])
            for row, setup in zip(rows, result["setup"])
            if setup["name"] == win
        ),
        win,
    )
    banner_color = GOLD if win == "平局" else MINT
    draw.polygon(((PAD,1187),(PAD+15,1174),(WIDTH-PAD,1174),
                  (WIDTH-PAD,1300),(WIDTH-PAD-15,1313),(PAD,1313)),
                 fill=(22,40,50), outline=_blend(BORDER,banner_color,.6), width=2)
    draw.line((PAD+1,1177,PAD+255,1177),fill=banner_color,width=3)
    _text(
        draw,
        (PAD + 26, 1195),
        "DRAW / 平局" if win == "平局" else "VICTORY / 胜者",
        22,
        banner_color,
    )
    banner = result["reason"] if win == "平局" else winner_name
    size, names = _fitted(draw, banner, WIDTH - 2 * PAD - 55, 46, 28)
    # Full combatant names remain on the combined moveset reference.
    _text(
        draw,
        (PAD + 26, 1235),
        _single_line(draw, banner, size, WIDTH - 2 * PAD - 55)
        if len(names) > 1
        else names[0],
        size,
        TEXT,
    )
    _text(
        draw,
        (PAD, 1338),
        "31 个体 · 0 努力 · 中性性格  /  后续查看完整配招与对战过程",
        24,
        FAINT,
    )
    return canvas


@dataclass(frozen=True)
class _MeasuredBlock:
    block: _Block
    headings: tuple[str, ...]
    lines: tuple[_Line, ...]
    height: int


def _measure_block(draw, block: _Block, width: int) -> _MeasuredBlock:
    headings = tuple(_wrap(draw, block.title, 30, width - (43 if block.type_name else 0)))
    lines = tuple(
        _Line(text, item.size, item.color, item.gap)
        for item in block.lines
        for text in _wrap(draw, item.text, item.size, width)
    )
    height = (
        22 + len(headings) * 39 + 15 + sum(line.size + line.gap for line in lines) + 17
    )
    return _MeasuredBlock(block, headings, lines, height)


def _content_image(battle, title, eyebrow, rows):
    """Measure all rows, then draw one complete image without clipping sections."""
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    layouts = []
    for row in rows:
        columns = len(row)
        column_width = (WIDTH - 2 * PAD - (columns - 1) * 24 - (18 if columns == 1 else 0)) // columns
        cells = tuple(
            _measure_block(measure, block, column_width - 48)
            if block is not None
            else None
            for block in row
        )
        row_height = max(cell.height for cell in cells if cell is not None)
        layouts.append((cells, column_width, row_height))
    height = max(650, 240 + sum(item[2] + 20 for item in layouts) + 76)
    if height > MAX_HEIGHT:
        raise ValueError(
            f"{title}完整长图超过安全尺寸，无法保留所有内容（{height}px）。"
        )
    canvas, draw = _base(height)
    _header(draw, title, eyebrow, battle["seed"])
    y = 240
    for cells, column_width, row_height in layouts:
        single = len(cells) == 1
        if single:
            draw.line((PAD,y+5,PAD,y+row_height+20),fill=BORDER,width=2)
            draw.ellipse((PAD-5,y+26,PAD+5,y+36),fill=cells[0].block.accent)
        for column, cell in enumerate(cells):
            if cell is None:
                continue
            x = PAD + (18 if single else 0) + column * (column_width + 24)
            accent = cell.block.accent
            # Numbered report sections sit on continuous printed stock, rather
            # than a stack of large rounded application panels.
            section_rule(draw, x, y, column_width, accent)
            band_end = y + 22 + len(cell.headings)*39 + 7
            draw.polygon(((x,y+3),(x+column_width,y+3),
                          (x+column_width-12,band_end),(x,band_end)),
                         fill=_blend(BG,accent,.08))
            ty = y + 22
            inset = 0
            if cell.block.type_name:
                icon = render_energy_icon(cell.block.type_name, 32)
                canvas.paste(icon,(x+23,ty-1),icon)
                inset = 43
            for heading in cell.headings:
                _text(draw, (x + 24 + inset, ty), heading, 30, accent)
                ty += 39
            ty += 15
            for line in cell.lines:
                _text(draw, (x + 24, ty), line.text, line.size, line.color)
                ty += line.size + line.gap
            if not single:
                draw.line((x+column_width,y+90,x+column_width,y+row_height-10),fill=BORDER,width=1)
        y += row_height + 20
    return canvas


def _move_image(battle):
    result = battle["result"]
    trainers = battle.get("trainer_names") or []
    columns = []
    for index, (setup, row) in enumerate(
        zip(result["setup"], (battle["row1"], battle["row2"]))
    ):
        accent = _blend(_color(row), TEXT, .30)
        if index == 1 and _color(battle["row1"]) == _color(row):
            accent = _blend(accent, (174, 158, 247), 0.45)
        side = "甲方" if index == 0 else "乙方"
        lines = []
        if index < len(trainers):
            lines.append(_Line(f"训练家：{trainers[index]}", 26, TEXT))
            lines.append(
                _Line(
                    "来自训练家的收藏"
                    + (" · 闪光个体" if _owned_shiny(battle, index) else ""),
                    24,
                    GOLD if _owned_shiny(battle, index) else MUTED,
                )
            )
        lines.extend(
            (
                _Line(f"属性：{row.get('types') or '未收录'}", 25, accent),
                _Line(f"特性：{setup.get('ability') or '未收录'}", 25, TEXT),
                _Line(
                    str(
                        setup.get("moves_source")
                        or "升级 / 学习器 / 遗传招推荐；非实战队伍"
                    ),
                    24,
                    FAINT,
                ),
            )
        )
        blocks = [
            _Block(
                f"{side} / {_display_name(row, setup['name'])}", tuple(lines), accent
            )
        ]
        for number, move in enumerate(setup.get("moves", []), 1):
            move_accent = _blend(TYPE_COLORS.get(move.get("type"), (122, 178, 191)), TEXT, .47)
            accuracy = (
                "必定命中"
                if move.get("accuracy") is None
                else f"命中{move['accuracy']}%"
            )
            blocks.append(
                _Block(
                    f"{number:02d}  {move['name']}",
                    (
                        _Line(
                            f"{move.get('category') or '未知'} / 威力{move.get('power') or '—'} / {accuracy} / PP{move.get('pp', '—')}",
                            24,
                            move_accent,
                        ),
                        _Line(
                            "模拟效果：" + str(move.get("effects") or "未实现的效果"),
                            26,
                            TEXT,
                        ),
                        _Line(
                            "图鉴说明："
                            + str(move.get("description") or "本地资料未收录。"),
                            25,
                            MUTED,
                        ),
                    ),
                    move_accent,
                    str(move.get("type") or "一般"),
                )
            )
        if not setup.get("moves"):
            blocks.append(
                _Block(
                    "无可用招式",
                    (_Line("使用挣扎：50 威力，每次反伤最大 HP 的 1/4。"),),
                    accent,
                )
            )
        columns.append(blocks)
    rows = [
        tuple(column[number] if number < len(column) else None for column in columns)
        for number in range(max(map(len, columns)))
    ]
    return _content_image(
        battle, "双方配招与效果", "MOVESET  /  特性 · 招式 · 模拟效果 · 图鉴说明", rows
    )


def _turn_image(battle):
    turns = battle["result"].get("turn_log", [])
    blocks = []
    for number, events in enumerate(turns, 1):
        lines = []
        for event in events:
            event = str(event)
            decision = "选择【" in event
            main = (
                "使用【" in event
                or "倒下" in event
                or "解冻" in event
                or "醒来" in event
            )
            color = FAINT if decision else TEXT if main else MUTED
            if not decision and any(word in event for word in ("灼伤", "中毒", "剧毒", "麻痹", "冰冻", "睡眠", "倒下")):
                color = (249, 183, 154)
            elif not decision and any(word in event for word in ("回复", "恢复", "吸取", "醒来", "解冻")):
                color = MINT
            lines.append(
                _Line(
                    event,
                    24 if decision else 26,
                    color,
                )
            )
        blocks.append(
            _Block(
                f"ROUND {number:02d}  /  第 {number} 回合",
                tuple(lines),
                MINT if number % 2 else (145, 181, 239),
            )
        )
    if not blocks:
        blocks.append(_Block("本场无行动回合", (_Line("战斗在双方行动前结束。"),)))
    rules = tuple(
        _Line(line, 24)
        for line in battle.get(
            "rules",
            [
                "先制度 → 速度决定行动顺序；同速与同收益选招由种子控制随机。",
                "支持本系 / 属性 / 暴击、PP、异常状态、能力等级及配招中写明的效果。",
                "普通特性仅实现属性免疫；吸收增益、道具、天气、场地、换人未模拟。",
                "选招理由是启发式估算，不预知对手本回合选招，也不提供胜率预测。",
            ],
        )
    )
    if battle.get("champion_line"):
        rules += (_Line(str(battle["champion_line"]), 24, GOLD),)
    blocks.append(_Block("对战规则", rules, GOLD))
    return _content_image(
        battle,
        "对战过程",
        "TURN LOG  /  全回合 · 招式 · 伤害 · HP · 状态 · 决策",
        [(block,) for block in blocks],
    )


def render_battle_cards(
    battle: dict, *, image_loader: ImageLoader | None = None
) -> list[bytes]:
    """Render exactly three images, retaining both movesets and every turn.

    The overview is 1200×1420. Movesets share one measured, two-column long
    image; the complete process occupies another. No detailed flag is needed
    for image output. Oversized snapshots raise before allocating a long
    canvas so the caller can fall back using the same battle result.
    A shiny combatant's loader receives a row copy with _artwork_shiny=True.
    """
    if not isinstance(battle.get("result"), dict):
        raise ValueError("缺少已完成的对战快照")
    encoded = []
    # Generate, encode, and release each canvas before constructing the next.
    for index, render in enumerate(
        (
            lambda: _poster(battle, image_loader),
            lambda: _move_image(battle),
            lambda: _turn_image(battle),
        ),
        1,
    ):
        image = render()
        try:
            draw = ImageDraw.Draw(image)
            _text(
                draw,
                (PAD, image.height - 46),
                "POKÉMON PARTNER  /  对战档案",
                19,
                FAINT,
            )
            _text(
                draw,
                (WIDTH - PAD - 124, image.height - 46),
                f"{index:02d} / 03",
                21,
                FAINT,
            )
            stream = io.BytesIO()
            image.save(stream, "JPEG", quality=92, subsampling=0, optimize=False)
            encoded.append(stream.getvalue())
        finally:
            image.close()
    return encoded
