"""对战配招与战报格式；入口只负责解析指令和发送。"""

from __future__ import annotations

import random
import re
import secrets

try:
    from .battle_engine import BattlePokemon, BattleSimulator, MOVE_EFFECTS, calc_stats, make_move
except ImportError:
    from battle_engine import BattlePokemon, BattleSimulator, MOVE_EFFECTS, calc_stats, make_move


def build_battle_pokemon(db, row: dict, level: int) -> BattlePokemon:
    """按实际可学列表推荐普通招式，优先适合自身攻防的本系与覆盖招。"""
    base, _ = db.first_stats(row)
    stats = calc_stats(dict(base or []), level)
    types = [t for t in (row.get("types") or "").split("/") if t]
    learn = db.learnset(row)
    names = {n for n, lv in learn["level_up"] if lv is None or lv <= level}
    names.update(n for n, _ in learn["machine"] + learn["egg"])
    index = db.move_index()
    candidates = [index[n] for n in sorted(names) if n in index]
    source = "升级/招式学习器/遗传招推荐（非实战队伍）"
    if not candidates:
        candidates = [m for m in index.values() if m.get("type") in types]
        source = "演示配招：本地未收录可学招式，不能保证可学"
    damage = []
    support = []
    for info in candidates:
        # Z招式、极巨招式和未登记的特殊公式不能直接当普通高威力招使用。
        if info.get("is_z") or any(k in info.get("name_zh", "") for k in ("极巨", "超极巨")):
            continue
        move = make_move(info, db.STATUS_MOVES.get(info.get("name_zh")))
        if move.category == "变化":
            if move.supported and (move.status or move.heal or move.changes):
                support.append(move)
            continue
        if not move.power:
            continue
        description = move.description
        special = any(k in description for k in ("连续", "数次", "２～", "2～", "２次", "2次", "一击", "越", "失败", "反作用力", "下回合", "下一回合", "２回合", "2回合", "蓄力", "混乱", "陷入昏厥"))
        if special and not move.supported:
            continue
        offense = stats["攻击" if move.category == "物理" else "特攻"]
        score = move.power * offense * (1.5 if move.type in types else 1) * (move.accuracy or 100) / 100
        if move.charge or move.recharge:
            score *= 0.5
        if move.self_destruct:
            score *= 0.35
        damage.append((score, move))
    damage.sort(key=lambda pair: (-pair[0], pair[1].name))
    selected = []
    used_types = set()
    for _, move in damage:
        if move.type not in used_types:
            selected.append(move)
            used_types.add(move.type)
        if len(selected) == 3:
            break
    if support:
        # 先选可解释的状态招，其后是回复/能力变化。
        support.sort(key=lambda m: (not bool(m.status), not bool(m.heal), m.name))
        selected.append(support[0])
    for _, move in damage:
        if len(selected) == 4:
            break
        if move not in selected:
            selected.append(move)
    pokemon = BattlePokemon(
        row.get("name_zh", ""), types, stats, selected, level,
        ability=re.split(r"[、/，,]", row.get("abilities") or "")[0].strip(),
    )
    pokemon.moves_source = source
    return pokemon


BATTLE_RULES = (
    "能力：31个体、0努力、中性性格。按先制度→速度行动；同速随机。",
    "选招：根据对手属性、预计伤害、剩余HP及状态/回复收益选择；收益相同才随机，不预知本回合随机数或对手选招。",
    "规则：本系/属性/暴击、PP、异常状态、能力等级及已登记效果。特性只判定普通特性的属性免疫；吸收/引火增益未模拟。",
    "道具、天气、场地、换人未模拟；未实现的招式效果在配招中标注。",
)


def build_battle_report(db, row1: dict, row2: dict, level: int, *, seed: int | None = None, detailed: bool = False, champion_line: str = "") -> dict:
    """只模拟一次；图片、合并转发和文本共用同一份结果与开场快照。"""
    seed = secrets.randbelow(2**32) if seed is None else int(seed) % (2**32)
    pokemon = [build_battle_pokemon(db, row, level) for row in (row1, row2)]
    snapshots = [
        {"stats": dict(p.stats), "types": list(p.types), "moves_source": p.moves_source}
        for p in pokemon
    ]
    report = BattleSimulator(db.type_chart(), rng=random.Random(seed)).simulate(*pokemon)
    for setup, snapshot in zip(report["setup"], snapshots):
        setup.update(snapshot)
    return {
        "row1": dict(row1), "row2": dict(row2), "level": level, "seed": seed,
        "result": report, "champion_line": champion_line, "detailed": detailed,
        "rules": list(BATTLE_RULES),
    }


def format_battle_report_data(battle: dict) -> str:
    """把已有结果格式化为战报，不重新计算对局或消耗随机数。"""
    row1, row2 = battle["row1"], battle["row2"]
    level, seed = battle["level"], battle["seed"]
    report = battle["result"]
    detailed, champion_line = battle.get("detailed", False), battle.get("champion_line", "")
    lines = [f"【{row1.get('name_zh', '')} vs {row2.get('name_zh', '')}】Lv.{level} 对战 · 种子={seed}",
             *battle.get("rules", BATTLE_RULES)]
    trainers = battle.get("trainer_names") or []
    if len(trainers) == 2:
        lines.insert(1, f"收藏对战：{trainers[0]} vs {trainers[1]}")
    if battle.get("duel_id"):
        lines.append(f"对局编号：{battle['duel_id'][:8]}")
    if champion_line:
        lines.append(champion_line)
    for setup in report["setup"]:
        lines.append(f"\n{setup['name']}｜特性：{setup['ability'] or '未收录'}｜{setup.get('moves_source', '演示配招')}")
        if not setup["moves"]:
            lines.append("无可用招式，将使用挣扎（50威力，每次反伤最大HP的1/4）。")
        for move in setup["moves"]:
            accuracy = "必定命中" if move["accuracy"] is None else f"命中{move['accuracy']}%"
            lines.append(f"• {move['name']} [{move['type'] or '无属性'}·{move['category']}] 威力{move['power'] or '—'} / {accuracy} / PP{move['pp']}")
            lines.append(f"  模拟效果：{move['effects']}")
            if move["description"]:
                lines.append(f"  图鉴：{move['description']}")
    result = "平局" if report["winner"] == "平局" else f"{report['winner']}获胜"
    lines.append(f"\n结果：{result} · {report['turns']}回合（{report['reason']}）")
    for name, final in report["final"].items():
        lines.append(f"{name}：HP {final['hp']}/{final['max']}（{final['pct']}%）" + (f" · {final['status']}" if final["status"] else "") + (" · 倒下" if final["fainted"] else ""))
    lines.append("\n逐回合战报：")
    turns = report["turn_log"]
    if detailed or len(turns) <= 10:
        lines.extend(line for turn in turns for line in turn)
    else:
        lines.extend(line for turn in turns[:6] for line in turn)
        lines.append(f"……省略中间{len(turns) - 8}回合……")
        lines.extend(line for turn in turns[-2:] for line in turn)
        lines.append(f"完整复盘：对战 {row1.get('name_zh', '')} {row2.get('name_zh', '')} {level} 详细 种子={seed}")
    return "\n".join(lines)


def format_battle_report(db, row1: dict, row2: dict, level: int, *, seed: int | None = None, detailed: bool = False, champion_line: str = "") -> str:
    """兼容原文本调用；显示配招效果与默认预览或详细回合。"""
    return format_battle_report_data(build_battle_report(
        db, row1, row2, level, seed=seed, detailed=detailed, champion_line=champion_line,
    ))


def parse_battle_options(text: str) -> tuple[bool, int | None]:
    """额外参数使用原始指令，兼容 AstrBot 截断到声明参数的行为。"""
    detailed = bool(re.search(r"(?:^|\s)(?:详细|完整)(?:\s|$)", text))
    match = re.search(r"(?:种子|seed)\s*[=:：]\s*(\d{1,10})(?!\d)", text, re.IGNORECASE)
    return detailed, int(match.group(1)) if match else None


def split_battle_text(text: str, limit: int = 1800) -> list[str]:
    """按完整行拆分聊天结果，避免长战报超过平台单条消息限制。"""
    parts, current = [], ""
    for line in text.splitlines():
        if current and len(current) + len(line) + 1 > limit:
            parts.append(current)
            current = ""
        current += ("\n" if current else "") + line
    if current:
        parts.append(current)
    return parts
