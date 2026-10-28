"""Paginated Pokémon battle posters and readable move / turn reports.

Rendering uses a completed battle snapshot; it never simulates a second battle.
All move descriptions and selected turn events are retained across bounded pages.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Callable

from PIL import Image, ImageDraw, ImageFilter

try:
    from .battle_engine import calc_stats
    from .card_renderer import load_font, TYPE_COLORS
except ImportError:
    from battle_engine import calc_stats
    from card_renderer import load_font, TYPE_COLORS

WIDTH = 1200
MAX_HEIGHT = 1680
PAD = 54
BG = (13, 22, 38)
PANEL = (23, 35, 54)
BORDER = (43, 60, 81)
TEXT = (245, 248, 250)
MUTED = (177, 192, 208)
FAINT = (129, 152, 175)
GOLD = (255, 212, 115)
MINT = (126, 225, 183)

ImageLoader = Callable[[dict], Image.Image | bytes | None]


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


def _blend(a, b, ratio):
    return tuple(round(a[i] * (1 - ratio) + b[i] * ratio) for i in range(3))


def _color(row: dict):
    kind = str(row.get("types") or "一般").split("/")[0]
    return _blend(TYPE_COLORS.get(kind, (96, 154, 190)), (255, 255, 255), .2)


def _wrap(draw, text: str, size: int, width: int) -> list[str]:
    font = load_font(size)
    output, current = [], ""
    for char in str(text):
        if char == "\n":
            output.append(current)
            current = ""
        elif current and draw.textlength(current + char, font=font) > width:
            tail = re.search(r"[A-Za-z0-9_./%]+$", current) if char.isascii() else None
            if tail and tail.start() > 0 and draw.textlength(tail.group()+char, font=font) <= width:
                output.append(current[:tail.start()])
                current = tail.group()+char
            elif char in "，。；：、！？）】〉》」』" and len(current) > 1:
                output.append(current[:-1])
                current = current[-1]+char
            else:
                output.append(current)
                current = char
        else:
            current += char
    if current or not output:
        output.append(current)
    return output


def _text(draw, xy, text, size=26, color=TEXT):
    draw.text(xy, str(text), font=load_font(size), fill=color, anchor="lt")


def _fitted(draw, text, width, max_size=52, min_size=30):
    for size in range(max_size, min_size - 1, -2):
        if draw.textlength(text, font=load_font(size)) <= width:
            return size, [text]
    lines = _wrap(draw, text, min_size, width)
    if len(lines) > 3:
        lines = lines[:3]
        while draw.textlength(lines[-1] + "…", font=load_font(min_size)) > width:
            lines[-1] = lines[-1][:-1]
        lines[-1] += "…"
    return min_size, lines


def _heading_lines(draw, text, width):
    return _fitted(draw, text, width, 30, 30)[1]


def _single_line(draw, text, size, width):
    if draw.textlength(text, font=load_font(size)) <= width:
        return text
    while text and draw.textlength(text + "…", font=load_font(size)) > width:
        text = text[:-1]
    return text + "…"


def _owned_shiny(battle, index):
    items = battle.get("owned_items") or []
    if index >= len(items):
        return False
    item = items[index]
    return bool(item.get("shiny") if isinstance(item, dict) else getattr(item, "shiny", False))


def _ball(draw, cx, cy, radius, color, width=2):
    draw.ellipse([cx-radius, cy-radius, cx+radius, cy+radius], outline=color, width=width)
    draw.line([cx-radius, cy, cx+radius, cy], fill=color, width=width)
    draw.ellipse([cx-radius*.2, cy-radius*.2, cx+radius*.2, cy+radius*.2], fill=BG, outline=color, width=width)


def _base(height: int, accents=None):
    canvas = Image.new("RGB", (WIDTH, height), BG)
    if accents:
        light = Image.new("RGBA", canvas.size)
        d = ImageDraw.Draw(light)
        for cx, cy, color in accents:
            d.ellipse([cx-250, cy-250, cx+250, cy+250], fill=(*color, 45))
        canvas = Image.alpha_composite(canvas.convert("RGBA"), light.filter(ImageFilter.GaussianBlur(100))).convert("RGB")
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle([18, 18, WIDTH-18, height-18], radius=26, outline=BORDER, width=2)
    return canvas, draw


def _header(draw, title, eyebrow, seed):
    _ball(draw, PAD+16, 62, 16, MINT, 2)
    _text(draw, (PAD+46, 45), "POKÉMON / BATTLE REPORT", 22, MINT)
    _text(draw, (WIDTH-PAD-230, 48), f"SEED {seed}", 20, FAINT)
    _text(draw, (PAD, 103), title, 44)
    _text(draw, (PAD, 162), eyebrow, 24, MUTED)
    draw.line([PAD, 207, WIDTH-PAD, 207], fill=BORDER, width=2)


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
    _ball(draw, center, top+side//2, side//2-12, _blend(BORDER, color, .3), 3)
    draw.arc([center-side//2, top, center+side//2, top+side], 204, 322, fill=color, width=7)
    art = _load_art(loader, row)
    if art is None:
        _ball(draw, center, top+side//2, 90, _blend(BORDER, color, .7), 6)
        _text(draw, (center-104, top+side-60), "立绘暂未缓存", 26, FAINT)
        return
    bbox = art.getbbox()
    if bbox:
        art = art.crop(bbox)
    art.thumbnail((side-30, side-20), Image.Resampling.LANCZOS)
    x, y = center-art.width//2, top+(side-art.height)//2
    silhouette = Image.new("RGBA", art.size, (*color, 0))
    silhouette.putalpha(art.getchannel("A").point(lambda a: round(a*.32)))
    glow = Image.new("RGBA", canvas.size)
    glow.alpha_composite(silhouette, (x, y+12))
    glow = glow.filter(ImageFilter.GaussianBlur(17))
    canvas.paste(glow, (0, 0), glow)
    canvas.paste(art, (x, y), art)


def _stats(row, level):
    values = re.search(r"HP(\d+)/攻击(\d+)/防御(\d+)/特攻(\d+)/特防(\d+)/速度(\d+)", str(row.get("stats") or ""))
    return calc_stats(dict(zip(("HP", "攻击", "防御", "特攻", "特防", "速度"), map(int, values.groups()))), level) if values else {}


def _display_name(row, name):
    form = str(row.get("form_name") or "")
    if not form:
        return name
    suffix = "（甲）" if str(name).endswith("（甲）") else "（乙）" if str(name).endswith("（乙）") else ""
    return form + suffix


def _poster(battle, loader):
    result = battle["result"]
    rows = (battle["row1"], battle["row2"])
    colors = [_color(row) for row in rows]
    canvas, draw = _base(1420, [(250, 420, colors[0]), (950, 420, colors[1])])
    _header(draw, "宝可梦对战", f"Lv.{battle['level']}  ·  {result['turns']} 回合  ·  {'完整复盘' if battle.get('detailed') else '对战速览'}", battle['seed'])
    draw.polygon([(580, 245), (632, 245), (585, 697), (544, 697)], fill=(35, 49, 67))
    centers = (300, 900)
    for index, (row, color, center) in enumerate(zip(rows, colors, centers)):
        _art(canvas, draw, row, loader, center, 238, 442, color)
        trainers = battle.get("trainer_names") or []
        trainer = f"训练家 · {trainers[index]}" if index < len(trainers) else f"CHALLENGER {index+1:02d}"
        _text(draw, (center-220, 703), _single_line(draw, trainer, 22, 440), 22, color)
        setup = result["setup"][index]
        size, names = _fitted(draw, _display_name(row, setup["name"]), 440)
        y = 741
        for line in names:
            _text(draw, (center-220, y), line, size)
            y += size+7
        kind = (row.get("types") or "属性未收录") + ("  /  闪光个体" if _owned_shiny(battle, index) else "")
        _text(draw, (center-220, 875), kind, 26, color)
        final = result["final"][setup["name"]]
        _text(draw, (center-220, 927), f"HP {final['hp']} / {final['max']}", 31)
        _text(draw, (center+129, 930), f"{final['pct']}%", 26, color)
        draw.rounded_rectangle([center-220, 974, center+220, 988], radius=7, fill=BORDER)
        pct = max(0, min(1, final["hp"]/max(1, final["max"])))
        if pct:
            draw.rounded_rectangle([center-220, 974, center-220+440*pct, 988], radius=7, fill=color)
        label = "倒下" if final.get("fainted") else final.get("status") or "战斗结束"
        _text(draw, (center-220, 1005), label, 24, MUTED)
        stats = setup.get("stats") or _stats(row, battle["level"])
        for n, key in enumerate(("攻击", "防御", "特攻", "特防", "速度")):
            sx = center-220+(n%3)*150
            sy = 1055+(n//3)*42
            _text(draw, (sx, sy), f"{key} {stats.get(key, '—')}", 23, MUTED)
    draw.ellipse([548, 420, 652, 524], fill=(19, 29, 45), outline=BORDER, width=2)
    _text(draw, (559, 444), "VS", 51, GOLD)
    win = result["winner"]
    winner_name = next((_display_name(row, setup["name"]) for row, setup in zip(rows, result["setup"]) if setup["name"] == win), win)
    banner_color = GOLD if win == "平局" else MINT
    draw.rounded_rectangle([PAD, 1174, WIDTH-PAD, 1313], radius=22, fill=(28, 44, 58), outline=_blend(BORDER, banner_color, .6), width=2)
    _text(draw, (PAD+26, 1195), "DRAW / 平局" if win == "平局" else "VICTORY / 胜者", 22, banner_color)
    banner = result["reason"] if win == "平局" else winner_name
    size, names = _fitted(draw, banner, WIDTH-2*PAD-55, 46, 28)
    # Full combatant names remain on the paginated move reference.
    _text(draw, (PAD+26, 1235), _single_line(draw, banner, size, WIDTH-2*PAD-55) if len(names) > 1 else names[0], size, TEXT)
    _text(draw, (PAD, 1338), "31 个体 · 0 努力 · 中性性格  /  后续卡片查看配招与回合记录", 23, FAINT)
    return canvas


def _expanded(draw, lines, width):
    return [_Line(text, item.size, item.color, item.gap) for item in lines for text in _wrap(draw, item.text, item.size, width)]


def _content_pages(battle, title, eyebrow, blocks):
    """Keep ordinary sections intact; split oversized sections without dropping text."""
    measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    width = WIDTH-2*PAD-48
    available = MAX_HEIGHT-244-92
    pages, current, used = [], [], 0
    for block in blocks:
        lines = _expanded(measure, block.lines, width)
        if len(_wrap(measure, block.title, 30, width)) > 3:
            lines = _expanded(measure, (_Line("完整名称：" + block.title, 24),), width) + lines
        full_height = len(_heading_lines(measure, block.title, width))*39+56+sum(line.size+line.gap for line in lines)+16
        if current and full_height <= available and used+full_height > available:
            pages.append(current); current, used = [], 0
        cursor, part = 0, 0
        while cursor < len(lines) or (not lines and cursor == 0):
            heading = block.title if part == 0 else block.title + "（续）"
            headings = _heading_lines(measure, heading, width)
            header_height = len(headings)*39+34
            if available-used < header_height+40 and current:
                pages.append(current); current, used = [], 0
            capacity = available-used-header_height-22
            batch, taken = [], 0
            while cursor < len(lines) and taken+lines[cursor].size+lines[cursor].gap <= capacity:
                line = lines[cursor]
                batch.append(line); taken += line.size+line.gap; cursor += 1
            if not batch and lines:
                pages.append(current); current, used = [], 0
                continue
            part += 1
            current.append((_Block(heading, tuple(batch), block.accent), header_height+taken+22))
            used += header_height+taken+22+16
            if not lines:
                break
            if cursor < len(lines):
                pages.append(current); current, used = [], 0
    if current:
        pages.append(current)
    images = []
    for page in pages:
        height = max(650, min(MAX_HEIGHT, 244+sum(h+16 for _, h in page)+76))
        canvas, draw = _base(height)
        _header(draw, title, eyebrow, battle["seed"])
        y = 240
        for block, block_height in page:
            draw.rounded_rectangle([PAD, y, WIDTH-PAD, y+block_height], radius=18, fill=PANEL, outline=BORDER, width=1)
            draw.rounded_rectangle([PAD, y+17, PAD+5, y+53], radius=2, fill=block.accent)
            ty = y+19
            for heading in _heading_lines(draw, block.title, width):
                _text(draw, (PAD+24, ty), heading, 30, TEXT)
                ty += 39
            ty += 15
            for line in block.lines:
                _text(draw, (PAD+24, ty), line.text, line.size, line.color)
                ty += line.size+line.gap
            y += block_height+16
        images.append(canvas)
    return images


def _move_pages(battle):
    pages = []
    result = battle["result"]
    for index, (setup, row) in enumerate(zip(result["setup"], (battle["row1"], battle["row2"]))):
        accent = _color(row)
        blocks = [_Block(_display_name(row, setup["name"]), (
            _Line(f"特性：{setup.get('ability') or '未收录'}  ·  属性：{row.get('types') or '未收录'}", 25, accent),
            _Line(str(setup.get('moves_source') or "升级 / 学习器 / 遗传招推荐；非实战队伍"), 24, FAINT),
        ), accent)]
        trainers = battle.get("trainer_names") or []
        if index < len(trainers):
            lines = [_Line(f"训练家：{trainers[index]}", 26, TEXT)]
            if _owned_shiny(battle, index):
                lines.append(_Line("来自训练家的收藏 · 闪光个体", 25, GOLD))
            else:
                lines.append(_Line("来自训练家的收藏", 25, MUTED))
            blocks.insert(0, _Block("PVP / 训练家档案", tuple(lines), accent))
        for number, move in enumerate(setup.get("moves", []), 1):
            accuracy = "必定命中" if move.get("accuracy") is None else f"命中 {move['accuracy']}%"
            blocks.append(_Block(f"{number:02d}  {move['name']}", (
                _Line(f"{move.get('type') or '无属性'} · {move.get('category') or '未知'}   /   威力 {move.get('power') or '—'}   /   {accuracy}   /   PP {move.get('pp', '—')}", 25, accent),
                _Line("模拟效果：" + str(move.get("effects") or "未实现的效果"), 26, TEXT),
                _Line("图鉴说明：" + str(move.get("description") or "本地资料未收录。"), 25, MUTED),
            ), accent))
        if not setup.get("moves"):
            blocks.append(_Block("无可用招式", (_Line("使用挣扎：50 威力，每次反伤最大 HP 的 1/4。"),), accent))
        pages.extend(_content_pages(battle, "配招与效果", f"CHALLENGER {index+1:02d}  /  本场携带的推荐配招", blocks))
    return pages


def _turn_pages(battle):
    turns = battle["result"].get("turn_log", [])
    selected = list(enumerate(turns, 1))
    preview = not battle.get("detailed") and len(turns) > 10
    if preview:
        selected = selected[:6]+[(None, None)]+selected[-2:]
    blocks = []
    for number, events in selected:
        if number is None:
            blocks.append(_Block("中间回合已折叠", (
                _Line(f"省略中间 {len(turns)-8} 回合，当前展示前 6 回合及最后 2 回合。"),
                _Line(f"完整复盘：对战 {battle['row1'].get('form_name') or battle['row1'].get('name_zh')} {battle['row2'].get('form_name') or battle['row2'].get('name_zh')} {battle['level']} 详细 种子={battle['seed']}", 24, MINT),
            ), GOLD))
            continue
        lines = []
        for event in events:
            decision = "选择【" in event
            main = "使用【" in event or "倒下" in event or "解冻" in event or "醒来" in event
            lines.append(_Line(event, 24 if decision else 26, FAINT if decision else TEXT if main else MUTED))
        blocks.append(_Block(f"ROUND {number:02d}  /  第 {number} 回合", tuple(lines), MINT))
    if not blocks:
        blocks.append(_Block("本场无行动回合", (_Line("战斗在双方行动前结束。"),)))
    rules = tuple(_Line(line, 24) for line in battle.get("rules", [
        "先制度 → 速度决定行动顺序；同速与同收益选招由种子控制随机。",
        "支持本系 / 属性 / 暴击、PP、异常状态、能力等级及配招中写明的效果。",
        "普通特性仅实现属性免疫；吸收增益、道具、天气、场地、换人未模拟。",
        "选招理由是启发式估算，不预知对手本回合选招，也不提供胜率预测。",
    ]))
    if battle.get("champion_line"):
        rules += (_Line(str(battle["champion_line"]), 24, GOLD),)
    blocks.append(_Block("对战规则", rules, GOLD))
    return _content_pages(battle, "逐回合复盘", "TURN LOG  /  招式 · 伤害 · HP · 状态 · 决策", blocks)


def render_battle_cards(battle: dict, *, image_loader: ImageLoader | None = None) -> list[bytes]:
    """Render one overview poster followed by readable move and turn pages.

    The first page is 1200×1420. Every following page is at most 1200×1680,
    with 24–30 px body text. Detailed mode retains all turn events; ordinary
    mode uses the text report's first-six / last-two preview for long battles.
    """
    if not isinstance(battle.get("result"), dict):
        raise ValueError("缺少已完成的对战快照")
    images = [_poster(battle, image_loader), *_move_pages(battle), *_turn_pages(battle)]
    encoded = []
    for index, image in enumerate(images, 1):
        draw = ImageDraw.Draw(image)
        _text(draw, (PAD, image.height-46), "POKÉMON PARTNER  /  对战档案", 19, FAINT)
        _text(draw, (WIDTH-PAD-124, image.height-46), f"{index:02d} / {len(images):02d}", 21, FAINT)
        stream = io.BytesIO()
        image.save(stream, "JPEG", quality=92, optimize=True)
        encoded.append(stream.getvalue())
    return encoded
