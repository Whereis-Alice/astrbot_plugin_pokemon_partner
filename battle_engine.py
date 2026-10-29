"""宝可梦计算引擎：等级能力值、逐回合事件与登记的招式效果。

能力值公式与官方一致；对战支持优先度、能力等级、状态、回复、反伤、
蓄力/休息与部分特性免疫。未覆盖的效果会在战报明示。
属性倍率读取插件本地 types.tsv 基准属性表
（由 PokedexDB.type_chart 注入，引擎自身不读文件）。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass, field

# 25 种性格中文名 -> (提升项 +10%, 降低项 -10%)，中性性格为 None
NATURES: dict[str, tuple[str, str] | None] = {
    "勤奋": None,
    "怕寂寞": ("攻击", "防御"),
    "勇敢": ("攻击", "速度"),
    "固执": ("攻击", "特攻"),
    "顽皮": ("攻击", "特防"),
    "大胆": ("防御", "攻击"),
    "坦率": None,
    "悠闲": ("防御", "速度"),
    "淘气": ("防御", "特攻"),
    "乐天": ("防御", "特防"),
    "胆小": ("速度", "攻击"),
    "急躁": ("速度", "防御"),
    "认真": None,
    "爽朗": ("速度", "特攻"),
    "天真": ("速度", "特防"),
    "内敛": ("特攻", "攻击"),
    "慢吞吞": ("特攻", "防御"),
    "温和": ("特攻", "速度"),
    "害羞": None,
    "马虎": ("特攻", "特防"),
    "沉着": ("特防", "攻击"),
    "温柔": ("特防", "防御"),
    "自大": ("特防", "速度"),
    "慎重": ("特防", "特攻"),
    "浮躁": None,
}

STAT_KEYS = ("HP", "攻击", "防御", "特攻", "特防", "速度")

# 异常状态的属性免疫（官方规则：电免麻痹、火免灼伤、毒/钢免中毒）
STATUS_IMMUNE_TYPES: dict[str, set[str]] = {
    "麻痹": {"电"},
    "灼伤": {"火"},
    "中毒": {"毒", "钢"},
}

# 受击类特性 → 免疫属性：持有该特性时对应属性招式对自身无效（特性覆盖属性表）。
# 仅收录纯免疫特性；干燥皮肤（水免疫但火弱点×1.25）等附带修正的特性暂不收录。
# 特性名与本地特性总表一致（该数据集作「飘浮」）。
DEFENSIVE_ABILITY_IMMUNITY: dict[str, str] = {
    "避雷针": "电",
    "蓄电": "电",
    "引火": "火",
    "储水": "水",
    "飘浮": "地面",
}


def _first_int(text) -> int | None:
    m = re.search(r"\d+", str(text or ""))
    return int(m.group()) if m else None


def _per_stat(value, key: str) -> int:
    """个体/努力参数支持单一数值或 {能力项: 数值}。"""
    if isinstance(value, dict):
        return int(value.get(key, 0) or 0)
    return int(value or 0)


def calc_stats(
    base: dict,
    level: int,
    iv: int | dict = 31,
    ev: int | dict | None = None,
    nature: str | None = None,
) -> dict:
    """按官方公式计算等级能力值。

    base 为 {HP/攻击/防御/特攻/特防/速度: 种族值}；iv/ev 可传单一数值或
    分项 dict；nature 传中文名（见 NATURES），未识别按无修正计算。
    HP 种族值为 1 时保持 1（脱壳忍者）。返回同键 dict（附"总和"）。
    """
    level = max(1, min(100, int(level)))
    boost, reduce = NATURES.get(nature or "") or (None, None)
    out = {}
    for key in STAT_KEYS:
        b = int(base.get(key, 1) or 1)
        i = _per_stat(iv, key)
        e = _per_stat(ev, key)
        if key == "HP":
            out[key] = 1 if b == 1 else (2 * b + i + e // 4) * level // 100 + level + 10
        else:
            v = (2 * b + i + e // 4) * level // 100 + 5
            if key == boost:
                v = int(v * 1.1)
            elif key == reduce:
                v = int(v * 0.9)
            out[key] = v
    out["总和"] = sum(out[k] for k in STAT_KEYS)
    return out


# ---------------- 回合制对战模拟（简化规则） ----------------
@dataclass
class BattleMove:
    """参战招式（由招式总表数据构建）。type=None 表示无属性（挣扎）。"""

    name: str
    type: str | None
    category: str  # 物理 / 特殊 / 变化
    power: int | None
    accuracy: int | None
    pp: int
    status: str | None = None  # 命中后施加的异常状态（仅状态招）
    description: str = ""
    priority: int = 0
    effect_chance: float = 1.0
    changes: tuple[tuple[str, str, int], ...] = ()
    heal: float = 0.0
    drain: float = 0.0
    recoil: float = 0.0
    flinch: float = 0.0
    protect: bool = False
    charge: bool = False
    recharge: bool = False
    self_destruct: bool = False
    thaws_user: bool = False
    thaws_target: bool = False
    supported: bool = False


# 只应用明确登记的效果，不从自然语言猜测概率或把全部图鉴效果视为已实现。
MOVE_EFFECTS: dict[str, dict] = {}


def _effects(names: str, **kwargs) -> None:
    for name in names.split("/"):
        MOVE_EFFECTS[name] = dict(kwargs)


_effects("电磁波/麻痹粉/大蛇瞪眼", status="麻痹")
_effects("磷火/鬼火", status="灼伤")
_effects("毒粉/毒瓦斯", status="中毒")
_effects("剧毒", status="剧毒")
_effects("催眠术/睡眠粉/催眠粉/蘑菇孢子/唱歌/恶魔之吻", status="睡眠")
_effects("十万伏特/雷电拳/电击/雷电牙", status="麻痹", effect_chance=0.1)
_effects("打雷/放电", status="麻痹", effect_chance=0.3)
_effects("泰山压顶", status="麻痹", effect_chance=0.3)
_effects("火焰放射/大字爆炎/火焰拳/火花/火焰牙", status="灼伤", effect_chance=0.1)
_effects("热风/喷射火焰", status="灼伤", effect_chance=0.1)
_effects("热水/喷烟", status="灼伤", effect_chance=0.3)
_effects("冰冻光束/暴风雪/冰冻拳/冰冻牙", status="冰冻", effect_chance=0.1)
_effects("污泥炸弹/污泥波/毒击", status="中毒", effect_chance=0.3)
MOVE_EFFECTS["污泥波"] = {"status": "中毒", "effect_chance": 0.1}
_effects("电光一闪/音速拳/子弹拳/冰砾/影子偷袭/水流喷射/真空波", priority=1)
_effects("神速", priority=2)
_effects("守住/看穿/尖刺防守", priority=4, protect=True)
_effects("自我再生/羽栖/生蛋/偷懒/光合作用/月光/晨光", heal=0.5)
_effects("吸取/超级吸取/终极吸取/吸取拳/吸血", drain=0.5)
_effects("吸取之吻", drain=0.75)
_effects("猛撞/地狱翻滚", recoil=0.25)
_effects("舍身冲撞/伏特攻击/闪焰冲锋/勇鸟猛攻/木槌/波动冲", recoil=1 / 3)
MOVE_EFFECTS["伏特攻击"].update(status="麻痹", effect_chance=0.1)
MOVE_EFFECTS["闪焰冲锋"] = {"recoil": 1 / 3, "status": "灼伤", "effect_chance": 0.1}
_effects("剑舞", changes=(("self", "攻击", 2),))
_effects("诡计", changes=(("self", "特攻", 2),))
_effects("高速移动/岩石打磨", changes=(("self", "速度", 2),))
_effects("铁壁/溶化", changes=(("self", "防御", 2),))
_effects("龙之舞", changes=(("self", "攻击", 1), ("self", "速度", 1)))
_effects("冥想", changes=(("self", "特攻", 1), ("self", "特防", 1)))
_effects("健美", changes=(("self", "攻击", 1), ("self", "防御", 1)))
_effects("蝶舞", changes=(("self", "特攻", 1), ("self", "特防", 1), ("self", "速度", 1)))
_effects("叫声", changes=(("target", "攻击", -1),))
_effects("摇尾巴/瞪眼", changes=(("target", "防御", -1),))
_effects("金属音", changes=(("target", "特防", -2),))
_effects("刺耳声", changes=(("target", "防御", -2),))
_effects("近身战", changes=(("self", "防御", -1), ("self", "特防", -1)))
_effects("蛮力", changes=(("self", "攻击", -1), ("self", "防御", -1)))
_effects("过热/流星群/飞叶风暴", changes=(("self", "特攻", -2),))
_effects("精神强念", changes=(("target", "特防", -1),), effect_chance=0.1)
_effects("咬碎/暗影球", changes=(("target", "防御", -1),), effect_chance=0.2)
MOVE_EFFECTS["暗影球"] = {"changes": (("target", "特防", -1),), "effect_chance": 0.2}
_effects("冰冻之风/岩石封锁/重踏", changes=(("target", "速度", -1),))
_effects("空气斩/空气之刃/岩崩/铁头/咬住/惊吓/头锤", flinch=0.3)
_effects("恶之波动/神通力/龙之俯冲", flinch=0.2)
_effects("日光束/日光刃/飞翔/挖洞/潜水/弹跳", charge=True)
_effects("破坏光线/终极冲击/爆炸烈焰/加农水炮/疯狂植物/岩石炮/时光咆哮", recharge=True)
_effects("自爆/大爆炸/薄雾炸裂", self_destruct=True)
_effects("火焰车", status="灼伤", effect_chance=0.1, thaws_user=True)
_effects("神圣之火", status="灼伤", effect_chance=0.5, thaws_user=True)
for _name in ("闪焰冲锋", "热水"):
    MOVE_EFFECTS[_name]["thaws_user"] = True
_effects("蒸汽爆炸", status="灼伤", effect_chance=0.3, thaws_user=True, thaws_target=True)
MOVE_EFFECTS["热水"]["thaws_target"] = True


def move_effect_text(move: BattleMove) -> str:
    """战报中的效果说明，与引擎实际执行的登记规则保持一致。"""
    parts = []
    if move.priority:
        parts.append(f"先制度 {move.priority:+d}")
    if move.status:
        parts.append(f"{move.effect_chance * 100:g}% 施加{move.status}")
    for target, stat, delta in move.changes:
        chance = "" if move.effect_chance == 1 else f"{move.effect_chance * 100:g}% "
        parts.append(f"{chance}{'自身' if target == 'self' else '对手'}{stat}{delta:+d}级")
    if move.heal:
        parts.append(f"回复最大HP的{move.heal * 100:g}%")
    if move.drain:
        parts.append(f"回复伤害的{move.drain * 100:g}%")
    if move.recoil:
        parts.append("反伤为伤害的1/3" if move.recoil == 1 / 3 else f"反伤为伤害的{move.recoil * 100:g}%")
    if move.flinch:
        parts.append(f"{move.flinch * 100:g}% 畏缩（先手命中时）")
    if move.protect:
        parts.append("本回合守住，连续使用成功率递减；尖刺反伤未模拟")
    if move.charge:
        parts.append("蓄力一回合，下一回合攻击；蓄力期间可被命中")
    if move.recharge:
        parts.append("攻击后下一回合休息")
    if move.self_destruct:
        parts.append("使用后自身倒下")
    if move.thaws_user:
        parts.append("使用前可解除自身冰冻")
    if move.thaws_target:
        parts.append("命中后解除对手冰冻")
    if move.name == "剧毒":
        parts.append("毒属性使用者必定命中")
    if move.name == "羽栖":
        parts.append("飞行属性暂时移除未模拟")
    if move.name in {"光合作用", "月光", "晨光"}:
        parts.append("按无天气回复量结算")
    if move.name in {"雷电牙", "火焰牙", "冰冻牙"}:
        parts.append("其余附加效果未模拟")
    if not move.supported and move.description:
        parts.append("仅结算基础伤害；图鉴中的额外效果未模拟" if move.power else "该效果尚未模拟")
    return "；".join(parts) or ("直接伤害" if move.power else "效果尚未模拟")


def make_move(info: dict, status: str | None = None) -> BattleMove:
    """从 move_list.tsv 行构建参战招式；power/accuracy/pp 取首个数字，缺失取安全默认。"""
    acc = _first_int(info.get("accuracy"))
    name = info.get("name_zh", "")
    effects = dict(MOVE_EFFECTS.get(name, {}))
    effects.setdefault("status", status)
    return BattleMove(
        name=name,
        type=info.get("type") or None,
        category=info.get("category") or "变化",
        power=_first_int(info.get("power")),
        accuracy=acc,
        pp=_first_int(info.get("pp")) or 10,
        description=info.get("description") or "",
        supported=name in MOVE_EFFECTS or (bool(_first_int(info.get("power"))) and not any(
            key in (info.get("description") or "") for key in
            ("有时", "降低", "提高", "回复", "恢复", "状态", "畏缩", "反作用", "失败", "连续", "回合", "越", "要害", "混乱", "精神", "比例", "昏厥", "先制", "抢先")
        )),
        **effects,
    )


STRUGGLE = BattleMove(
    name="挣扎", type=None, category="物理", power=50, accuracy=100, pp=0
)


@dataclass
class BattlePokemon:
    """参战宝可梦：等级能力值 + 招式 + 当前 HP/异常状态。"""

    name: str
    types: list[str]
    stats: dict  # calc_stats 输出（含"总和"）
    moves: list[BattleMove]
    level: int = 50
    current_hp: int = 0
    status: str | None = None  # 麻痹 / 灼伤 / 中毒
    ability: str = ""
    stages: dict[str, int] = field(default_factory=dict)
    status_turns: int = 0
    poison_turns: int = 0
    flinched: bool = False
    protected: bool = False
    protect_streak: int = 0
    charging: BattleMove | None = None
    resting: bool = False

    def __post_init__(self):
        if self.current_hp <= 0:
            self.current_hp = self.stats["HP"]

    @property
    def max_hp(self) -> int:
        return self.stats["HP"]

    @property
    def hp_pct(self) -> float:
        return max(0.0, self.current_hp / self.max_hp)

    @property
    def fainted(self) -> bool:
        return self.current_hp <= 0

    @property
    def effective_speed(self) -> int:
        return (
            int(self.effective_stat("速度") * 0.5)
            if self.status == "麻痹"
            else self.effective_stat("速度")
        )

    def effective_stat(self, key: str, *, critical: bool = False, attacker: bool = True) -> int:
        stage = max(-6, min(6, self.stages.get(key, 0)))
        if critical and ((attacker and stage < 0) or (not attacker and stage > 0)):
            stage = 0
        multiplier = (2 + stage) / 2 if stage >= 0 else 2 / (2 - stage)
        return max(1, int(self.stats.get(key, 1) * multiplier))

    def take_damage(self, dmg: int) -> int:
        actual = max(0, min(dmg, self.current_hp))
        self.current_hp -= actual
        return actual


class BattleSimulator:
    """回合制对战：先制度、能力等级、PP、属性、状态与登记的招式效果。

    chart 为 {(防守属性, 进攻属性): 倍率}。每回合保留完整日志，规则未覆盖的
    图鉴效果由 move_effect_text 明示；没有道具、天气、场地、换人或完整特性系统。
    """

    def __init__(
        self,
        chart: dict[tuple[str, str], float],
        rng: random.Random | None = None,
        max_turns: int = 100,
    ):
        self.chart = chart
        self.rng = rng or random.Random()
        self.max_turns = max(1, min(100, int(max_turns)))
        self.log: list[str] = []  # (供测试与调试的完整事件流)
        self._labels: dict[int, str] = {}

    def _label(self, pokemon: BattlePokemon) -> str:
        return self._labels.get(id(pokemon), pokemon.name)

    def effectiveness(self, atk_type: str | None, def_types: list[str]) -> float:
        if not atk_type:
            return 1.0
        m = 1.0
        for d in def_types:
            m *= self.chart.get((d, atk_type), 1.0)
        return m

    def simulate(self, p1: BattlePokemon, p2: BattlePokemon) -> dict:
        """模拟整场对战，返回完整回合战报与开场配招快照。"""
        self.log = []
        pokemon = (p1, p2)
        self._labels = {
            id(p1): p1.name if p1.name != p2.name else f"{p1.name}（甲）",
            id(p2): p2.name if p1.name != p2.name else f"{p2.name}（乙）",
        }
        setup = [
            {"name": self._label(p), "ability": p.ability,
             "moves": [{"name": m.name, "type": m.type, "category": m.category,
                        "power": m.power, "accuracy": m.accuracy, "pp": m.pp,
                        "description": m.description, "effects": move_effect_text(m)} for m in p.moves]}
            for p in pokemon
        ]
        turn_reports = []
        self.log.append(
            f"{p1.name}（{'/'.join(p1.types)}）vs {p2.name}（{'/'.join(p2.types)}），Lv.{p1.level}"
        )
        turns = 0
        while not (p1.fainted or p2.fainted) and turns < self.max_turns:
            turns += 1
            start = len(self.log)
            for p in pokemon:
                p.protected = p.flinched = False
            moves = {
                id(p1): p1.charging or self._select_move(p1, p2),
                id(p2): p2.charging or self._select_move(p2, p1),
            }
            key1 = (moves[id(p1)].priority, p1.effective_speed)
            key2 = (moves[id(p2)].priority, p2.effective_speed)
            first = key1 > key2 or (key1 == key2 and self.rng.random() < 0.5)
            order = pokemon if first else pokemon[::-1]
            self.log.insert(start,
                f"第{turns}回合：{self._label(order[0])}先手"
                f"（先制度{moves[id(order[0])].priority:+d}，速度{order[0].effective_speed}；"
                f"对手先制度{moves[id(order[1])].priority:+d}，速度{order[1].effective_speed}）"
            )
            for attacker, defender in (order, order[::-1]):
                if attacker.fainted or defender.fainted:
                    continue
                self._take_turn(attacker, defender, moves[id(attacker)])
            # 招式内反伤/自爆已结算；一方倒下时，1v1 不再进入回合末阶段。
            if not (p1.fainted or p2.fainted):
                for p in pokemon:  # 回合结束结算异常状态
                    if p.fainted:
                        continue
                    if p.status == "灼伤":
                        self._status_damage(p, max(1, p.max_hp // 16), "灼伤")
                    elif p.status == "中毒":
                        self._status_damage(p, max(1, p.max_hp // 8), "中毒")
                    elif p.status == "剧毒":
                        p.poison_turns += 1
                        self._status_damage(p, max(1, p.max_hp * min(15, p.poison_turns) // 16), "剧毒")
            turn_reports.append(self.log[start:].copy())

        if p1.fainted and p2.fainted:
            winner = loser = "平局"
        elif p2.fainted:
            winner, loser = self._label(p1), self._label(p2)
        elif p1.fainted:
            winner, loser = self._label(p2), self._label(p1)
        else:
            winner = loser = "平局"
        moments = self._key_moments()
        return {
            "winner": winner,
            "loser": loser,
            "turns": turns,
            "final": {
                self._label(p): {
                    "hp": max(0, p.current_hp),
                    "max": p.max_hp,
                    "pct": round(p.hp_pct * 100),
                    "status": p.status,
                    "fainted": p.fainted,
                }
                for p in pokemon
            },
            "key_moments": moments,
            "setup": setup,
            "turn_log": turn_reports,
            "log": self.log.copy(),
            "reason": "回合上限，双方尚未倒下" if not (p1.fainted or p2.fainted) else "双方倒下" if p1.fainted and p2.fainted else "一方倒下",
        }

    # ---------- 单回合 ----------
    def _take_turn(self, attacker: BattlePokemon, defender: BattlePokemon, move: BattleMove | None = None):
        label = self._label(attacker)
        if attacker.resting:
            attacker.resting = False
            self.log.append(f"{label} 因上回合招式的反作用，本回合休息。")
            return
        if attacker.flinched:
            self.log.append(f"{label} 因畏缩无法行动！")
            return
        move = move or attacker.charging or self._select_move(attacker, defender)
        if attacker.status == "睡眠":
            attacker.status_turns -= 1
            if attacker.status_turns > 0:
                self.log.append(f"{label} 正在睡眠，无法行动（还剩{attacker.status_turns}次行动）。")
                return
            attacker.status = None
            self.log.append(f"{label} 醒来了！")
        if attacker.status == "冰冻":
            if move is not None and move.thaws_user:
                attacker.status = None
                self.log.append(f"{label} 使用{move.name}前解除了自身冰冻。")
            elif self.rng.random() >= 0.2:
                self.log.append(f"{label} 被冰冻，无法行动！")
                return
            else:
                attacker.status = None
                self.log.append(f"{label} 解冻了！")
        if attacker.status == "麻痹" and self.rng.random() < 0.25:
            self.log.append(f"{label} 因麻痹无法行动！")
            return
        releasing = attacker.charging is move
        if releasing:
            attacker.charging = None
        elif move.pp > 0:
            move.pp -= 1
        self.log.append(f"{label} 使用【{move.name}】（{move.type or '无属性'}·{move.category}，PP {'—' if move is STRUGGLE else move.pp}）。")
        if not move.protect:
            attacker.protect_streak = 0
        if move.charge and not releasing:
            attacker.charging = move
            self.log.append(f"{label} 开始蓄力，下一回合发动攻击。")
            return
        if move.protect:
            success = self.rng.random() < 1 / (3 ** attacker.protect_streak)
            attacker.protect_streak += 1
            attacker.protected = success
            self.log.append(f"{label} {'守住了本回合的攻击！' if success else '连续守住失败！'}")
            return
        if not self._hit(move, attacker):
            self.log.append(f"{label} 的{move.name}没打中（命中率{move.accuracy}%）！")
            if move.self_destruct:
                self._self_destruct(attacker)
            return
        targets_enemy = move.category != "变化" or move.status or any(t == "target" for t, _, _ in move.changes)
        if defender.protected and targets_enemy:
            self.log.append(f"{self._label(defender)} 守住，挡下了{move.name}！")
            if move.self_destruct:
                self._self_destruct(attacker)
            return
        if (move.category != "变化" or move.status) and defender.ability in DEFENSIVE_ABILITY_IMMUNITY and DEFENSIVE_ABILITY_IMMUNITY[defender.ability] == move.type:
            self.log.append(f"{self._label(defender)} 的【{defender.ability}】使{move.name}无效（仅模拟属性免疫）。")
            if move.self_destruct:
                self._self_destruct(attacker)
            return
        if move.category == "变化":
            if move.status:
                if (move.name.endswith("粉") or move.name == "蘑菇孢子") and "草" in defender.types:
                    self.log.append(f"{self._label(defender)} 的草属性免疫粉末，{move.name}无效！")
                    return
                if move.type == "电" and "地面" in defender.types:
                    self.log.append(f"{self._label(defender)} 对电属性免疫，{move.name}无效！")
                    return
                self._apply_status(move, attacker, defender)
            if move.heal:
                self._heal(attacker, max(1, int(attacker.max_hp * move.heal)), move.name)
            self._apply_changes(move, attacker, defender)
            if not (move.status or move.heal or move.changes):
                self.log.append(f"{move.name} 的图鉴效果尚未模拟，本次未产生效果。")
            return
        landed = self._deal_damage(attacker, defender, move, struggle=move is STRUGGLE)
        if move.recharge and landed:
            attacker.resting = True
            self.log.append(f"{label} 下回合需要休息。")
        if move.self_destruct:
            self._self_destruct(attacker)

    def _status_can_affect(self, move: BattleMove, defender: BattlePokemon) -> bool:
        if not move.status or defender.status:
            return False
        immune_types = STATUS_IMMUNE_TYPES.get("中毒" if move.status == "剧毒" else move.status, set())
        if set(defender.types) & immune_types or (move.status == "冰冻" and "冰" in defender.types):
            return False
        if move.category == "变化":
            if (move.name.endswith("粉") or move.name == "蘑菇孢子") and "草" in defender.types:
                return False
            if move.type == "电" and "地面" in defender.types:
                return False
            if move.type and DEFENSIVE_ABILITY_IMMUNITY.get(defender.ability) == move.type:
                return False
        return True

    def _expected_damage(self, attacker: BattlePokemon, defender: BattlePokemon, move: BattleMove) -> float:
        """仅估算已实现的基础伤害，不预知随机数或对手本回合选招。"""
        if move.category == "变化" or not move.power:
            return 0.0
        if move.type and DEFENSIVE_ABILITY_IMMUNITY.get(defender.ability) == move.type:
            return 0.0
        eff = self.effectiveness(move.type, defender.types)
        atk = attacker.effective_stat("攻击" if move.category == "物理" else "特攻")
        defense = defender.effective_stat("防御" if move.category == "物理" else "特防")
        damage = ((2 * attacker.level / 5 + 2) * move.power * atk / defense / 50 + 2)
        damage *= eff * (1.5 if move.type in attacker.types else 1) * 0.925
        if attacker.status == "灼伤" and move.category == "物理":
            damage *= 0.5
        return damage

    def _move_score(self, p: BattlePokemon, defender: BattlePokemon, move: BattleMove) -> tuple[float, str]:
        chance = 1.0 if move.accuracy is None or (move.name == "剧毒" and "毒" in p.types) else move.accuracy / 100
        damage = self._expected_damage(p, defender, move)
        applied_damage = min(damage, defender.current_hp)
        can_land_effects = move.category == "变化" or damage > 0
        incoming = max((self._expected_damage(defender, p, m) for m in defender.moves if m.pp > 0), default=0)
        outgoing = max((self._expected_damage(p, defender, m) for m in p.moves if m.pp > 0), default=0)
        score = applied_damage * chance
        reason = f"预计伤害约{damage:.0f}，命中收益{score:.0f}"
        if move.charge or move.recharge:
            score *= 0.5
            reason += "，计入蓄力/休息成本"
        if damage >= defender.current_hp and not move.charge:
            # 濒危时先制补刀优先于通常速度较慢的高威力招。
            faster = move.priority > 0 or (move.priority == 0 and p.effective_speed >= defender.effective_speed)
            if faster:
                score += defender.current_hp * 2 * chance
                reason += "，有望先手结束对战"
        if move.drain:
            score += min(p.max_hp - p.current_hp, applied_damage * move.drain) * chance * 0.8
        if move.recoil:
            score -= min(p.current_hp, applied_damage * move.recoil) * 0.5
        if move.self_destruct:
            score -= p.current_hp + p.max_hp * 0.5
            reason += "，计入自身倒下成本"
        if can_land_effects and move.status and self._status_can_affect(move, defender):
            status_gain = {
                "睡眠": incoming * 1.8,
                "冰冻": incoming * 1.8,
                "麻痹": incoming * (1.0 if p.effective_speed < defender.effective_speed else 0.4),
                "灼伤": defender.max_hp / 8 + incoming * 0.5,
                "中毒": defender.max_hp / 4,
                "剧毒": defender.max_hp * 3 / 8,
            }.get(move.status, 0)
            score += status_gain * chance * move.effect_chance
            if move.category == "变化":
                reason = f"可施加{move.status}，预计后续收益{score:.0f}"
        if move.heal:
            missing = p.max_hp - p.current_hp
            score += min(missing, p.max_hp * move.heal) * (0.9 if p.hp_pct <= 0.6 else 0.2)
            reason = f"可回复{min(missing, int(p.max_hp * move.heal))}HP，按当前受伤程度评估"
        for target, stat, delta in move.changes:
            if not can_land_effects:
                continue
            affected = p if target == "self" else defender
            stage = affected.stages.get(stat, 0)
            if (delta > 0 and stage >= 6) or (delta < 0 and stage <= -6):
                continue
            attack_stat = stat in {"攻击", "特攻"}
            source = affected if attack_stat else defender if affected is p else p
            relevant = any(m.category == ("物理" if stat in {"攻击", "防御"} else "特殊") and m.power for m in source.moves)
            applied = abs(max(-6, min(6, stage + delta)) - stage)
            gain = ((outgoing if source is p else incoming) * 0.35 if relevant else 0) * applied
            if stat == "速度" and p.effective_speed < defender.effective_speed:
                gain = incoming * 0.7
            if p.hp_pct < 0.4:
                gain *= 0.25
            beneficial = (target == "self" and delta > 0) or (target == "target" and delta < 0)
            score += gain * move.effect_chance * (1 if beneficial else -1)
            if move.category == "变化":
                reason = "按可用招式、能力等级与剩余HP评估强化/削弱收益"
        if move.protect:
            residual = defender.max_hp / (8 if defender.status == "中毒" else 16) if defender.status in {"灼伤", "中毒", "剧毒"} else 0
            score = residual * 1.2 / (3 ** p.protect_streak)
            reason = "守住可等待对手异常状态损耗" if residual else "本回合守住缺少持续收益"
        if p.status == "冰冻" and move.thaws_user:
            score += max(incoming, p.max_hp / 4)
            reason += "，可解除自身冰冻"
        return max(0.0, score), reason

    def _select_move(self, p: BattlePokemon, defender: BattlePokemon | None = None) -> BattleMove:
        usable = [m for m in p.moves if m.pp > 0]
        if not usable:
            self.log.append(f"{self._label(p)} 没有可用招式，只能使用挣扎！")
            return STRUGGLE
        if defender is None:
            return self.rng.choice(usable)
        ranked = [(self._move_score(p, defender, m), m) for m in usable]
        best = max(score for (score, _reason), _move in ranked)
        candidates = [(reason, m) for (score, reason), m in ranked if abs(score - best) < 1e-9]
        reason, selected = self.rng.choice(candidates)
        self.log.append(f"{self._label(p)} 选择【{selected.name}】：{reason}（启发式估算，非胜率预测）。")
        return selected

    def _hit(self, move: BattleMove, attacker: BattlePokemon | None = None) -> bool:
        return (move.name == "剧毒" and attacker is not None and "毒" in attacker.types) or move.accuracy is None or self.rng.random() * 100 < move.accuracy

    def _apply_status(
        self, move: BattleMove, attacker: BattlePokemon, defender: BattlePokemon
    ):
        if defender.status:
            self.log.append(
                f"{self._label(defender)} 已处于{defender.status}状态，{move.name}没有效果。"
            )
            return
        immune = STATUS_IMMUNE_TYPES.get("中毒" if move.status == "剧毒" else move.status, set()) & set(defender.types)
        if move.status == "冰冻" and "冰" in defender.types:
            immune = {"冰"}
        if immune:
            self.log.append(
                f"{self._label(defender)}（{'/'.join(defender.types)}）免受{move.status}，{move.name}没有效果。"
            )
            return
        defender.status = move.status
        if move.status == "睡眠":
            # 本回合若尚未行动，会立即消耗第一次睡眠行动。
            defender.status_turns = self.rng.randint(1, 3) + 1
        defender.poison_turns = 0
        rules = {"麻痹": "速度减半，25%概率无法行动", "灼伤": "物理伤害减半，每回合损失最大HP的1/16",
                 "中毒": "每回合损失最大HP的1/8", "剧毒": "每回合损失最大HP的N/16，N逐回合递增",
                 "睡眠": "1～3次行动内无法行动", "冰冻": "每次行动20%概率解冻"}
        self.log.append(f"{self._label(defender)} 陷入{move.status}状态！（{rules.get(move.status, '')}）")

    def _apply_changes(self, move: BattleMove, attacker: BattlePokemon, defender: BattlePokemon) -> None:
        for target, stat, delta in move.changes:
            pokemon = attacker if target == "self" else defender
            if pokemon.fainted:
                continue
            old = pokemon.stages.get(stat, 0)
            new = max(-6, min(6, old + delta))
            pokemon.stages[stat] = new
            self.log.append(f"{self._label(pokemon)} {stat}等级 {old:+d}→{new:+d}" + ("（已达上限/下限）。" if old == new else f"，当前{stat}{pokemon.effective_stat(stat)}。"))

    def _heal(self, pokemon: BattlePokemon, amount: int, source: str) -> None:
        actual = max(0, min(amount, pokemon.max_hp - pokemon.current_hp))
        pokemon.current_hp += actual
        self.log.append(f"{self._label(pokemon)} 通过{source}回复{actual}HP，现为{pokemon.current_hp}/{pokemon.max_hp}。")

    def _self_destruct(self, pokemon: BattlePokemon) -> None:
        pokemon.current_hp = 0
        self.log.append(f"{self._label(pokemon)} 因自爆效果倒下了！")

    def _deal_damage(
        self,
        attacker: BattlePokemon,
        defender: BattlePokemon,
        move: BattleMove,
        struggle: bool = False,
    ) -> bool:
        eff = self.effectiveness(move.type, defender.types)
        if defender.ability in DEFENSIVE_ABILITY_IMMUNITY and DEFENSIVE_ABILITY_IMMUNITY[defender.ability] == move.type:
            self.log.append(f"{self._label(defender)} 的【{defender.ability}】使{move.name}无效（仅模拟属性免疫）。")
            return False
        if eff == 0:
            self.log.append(f"{self._label(defender)} 对{move.type}属性攻击免疫，{move.name}倍率×0，不受伤害！")
            return False
        if not move.power:
            self.log.append(f"{move.name} 的可变威力或特殊伤害公式尚未模拟，本次未产生伤害。")
            return False
        crit = self.rng.random() < 1 / 24
        atk = attacker.effective_stat("攻击" if move.category == "物理" else "特攻", critical=crit)
        dfn = defender.effective_stat("防御" if move.category == "物理" else "特防", critical=crit, attacker=False)
        burned = attacker.status == "灼伤" and move.category == "物理"
        stab = 1.5 if move.type and move.type in attacker.types else 1.0
        dmg = (2 * attacker.level / 5 + 2) * (move.power or 0) * atk / dfn / 50 + 2
        dmg *= stab * eff * (1.5 if crit else 1.0) * (0.5 if burned else 1) * self.rng.uniform(0.85, 1.0)
        final = max(1, int(dmg))
        actual = defender.take_damage(final)
        note = ("，暴击！" if crit else "") + (
            "，效果拔群！" if eff > 1 else "，收效甚微…" if eff < 1 else ""
        )
        self.log.append(
            f"{self._label(attacker)} 使用{move.name}，{self._label(defender)} 受到 {actual} 点伤害{note}"
            f"（威力{move.power}，属性×{eff:g}，本系×{stab:g}"
            f"{'，灼伤×0.5' if burned else ''}），HP {defender.current_hp}/{defender.max_hp}。"
        )
        if defender.status == "冰冻" and (move.type == "火" or move.thaws_target) and not defender.fainted:
            defender.status = None
            self.log.append(f"{self._label(defender)} 因{move.name}命中解冻。")
        if defender.fainted:
            self.log.append(f"{self._label(defender)} 倒下了！")
        elif move.effect_chance >= 1 or self.rng.random() < move.effect_chance:
            if move.status:
                self._apply_status(move, attacker, defender)
            self._apply_changes(move, attacker, defender)
        # 使用者副作用在对手倒下时仍然结算。
        if defender.fainted and move.effect_chance == 1:
            self._apply_changes(move, attacker, defender)
        if move.drain and not attacker.fainted:
            self._heal(attacker, max(1, int(actual * move.drain)), move.name)
        if struggle or move.recoil:
            recoil = max(1, attacker.max_hp // 4 if struggle else int(actual * move.recoil))
            actual_recoil = attacker.take_damage(recoil)
            self.log.append(f"{self._label(attacker)} 受到{move.name}的反作用力 {actual_recoil} 点伤害，HP {attacker.current_hp}/{attacker.max_hp}。")
            if attacker.fainted:
                self.log.append(f"{self._label(attacker)} 倒下了！")
        if not defender.fainted and move.flinch and self.rng.random() < move.flinch:
            defender.flinched = True
            self.log.append(f"{self._label(defender)} 畏缩了，本回合尚未行动时将无法行动。")
        return True

    def _status_damage(self, p: BattlePokemon, dmg: int, label: str):
        actual = p.take_damage(dmg)
        self.log.append(f"{self._label(p)} 受到{label}伤害 {actual} 点，HP {p.current_hp}/{p.max_hp}！")
        if p.fainted:
            self.log.append(f"{self._label(p)} 倒下了！")

    def _key_moments(self, limit: int = 5) -> list[str]:
        marks = ("陷入", "倒下", "暴击", "效果拔群", "免疫", "挣扎", "无法行动")
        picks = [line for line in self.log if any(m in line for m in marks)]
        if len(picks) > limit:
            picks = picks[: limit - 1] + [picks[-1]]
        return picks
