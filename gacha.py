"""宝可梦抽取的规则与数据模型（不依赖 AstrBot，可独立单测）。

职责边界：

* 稀有度阈值、概率、保底、星尘/兑换价格与里程碑常量；
* ``Partner`` / ``DrawItem`` / ``DrawOutcome`` 等不可变数据模型；
* 从图鉴库构建卡池。

持久化与抽卡流程在 :mod:`gacha_store`。这样规则、存储和界面可以各自替换，
也避免单个文件继续膨胀。

抽卡流程（每次固定数量，不做十连）：

1. 按配置权重抽取稀有度；连续未出 SSR 达到阈值时本次强制 SSR；
2. 在该稀有度的池子里等概率选一只；可选“连续未出新”保底强制未拥有；
3. 独立判定闪光；
4. 首次获得写入图鉴；重复获得折算星尘；
5. 达到收集里程碑时发放星尘与当日额外次数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

RARITIES: tuple[str, ...] = ("C", "R", "SR", "SSR")
RARITY_NAMES: dict[str, str] = {
    "C": "普通",
    "R": "稀有",
    "SR": "史诗",
    "SSR": "传说",
}
DEFAULT_WEIGHTS: dict[str, int] = {"C": 55, "R": 30, "SR": 12, "SSR": 3}
DEFAULT_DUPLICATE_DUST: dict[str, int] = {"C": 2, "R": 6, "SR": 20, "SSR": 80}
DEFAULT_SPARK_COSTS: dict[str, int] = {"C": 20, "R": 60, "SR": 150, "SSR": 400}

# BST：种族值总和。480 表示 >=480 且 <580 为 SR。
DEFAULT_RARE_MIN_BST = 400
DEFAULT_SUPER_RARE_MIN_BST = 480
DEFAULT_ULTRA_RARE_MIN_BST = 580

# 收集里程碑：(宝可梦数, 奖励星尘, 当日额外次数)
MILESTONE_REWARDS: dict[int, tuple[int, int]] = {
    10: (30, 0),
    30: (80, 0),
    60: (150, 0),
    100: (250, 1),
    200: (500, 2),
    400: (1000, 3),
    600: (1500, 5),
    800: (2000, 8),
}
ALL_COLLECTED_DUST = 5000
ALL_COLLECTED_BONUS = 20

STATE_VERSION = 2
MAX_DRAWS_PER_COMMAND = 10
MAX_DAILY_LIMIT = 100
MAX_PITY = 999


class GachaError(Exception):
    """可直接展示给用户的抽卡错误。"""


class ConfigError(ValueError):
    """配置不合法。插件加载时记录日志并回退默认值。"""


def _as_int(value: Any, default: int, lo: int | None = None, hi: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        result = default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def _as_float(
    value: Any, default: float, lo: float | None = None, hi: float | None = None
) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        result = default
    if lo is not None:
        result = max(lo, result)
    if hi is not None:
        result = min(hi, result)
    return result


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "是", "开"}:
            return True
        if lowered in {"0", "false", "no", "off", "否", "关"}:
            return False
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def rarity_label(rarity: str) -> str:
    return RARITY_NAMES.get(rarity, rarity)


@dataclass(frozen=True)
class GachaRules:
    """由插件配置生成的不可变规则快照。"""

    weights: Mapping[str, int]
    duplicate_dust: Mapping[str, int]
    spark_costs: Mapping[str, int]
    rare_min_bst: int = DEFAULT_RARE_MIN_BST
    super_rare_min_bst: int = DEFAULT_SUPER_RARE_MIN_BST
    ultra_rare_min_bst: int = DEFAULT_ULTRA_RARE_MIN_BST
    draws_per_command: int = 1
    daily_limit: int = 3
    cooldown_seconds: float = 3.0
    ssr_pity_after: int = 30
    new_pity_after: int = 0
    allow_duplicate_draw: bool = True
    shiny_chance_percent: float = 1.5
    shiny_dust_multiplier: float = 3.0
    shiny_spark_multiplier: float = 4.0
    include_alternate_forms: bool = False

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> "GachaRules":
        config = _mapping(config)

        def read_weights(
            key: str, defaults: Mapping[str, int], *, allow_zero: bool = False
        ) -> dict[str, int]:
            raw = _mapping(config.get(key))
            out = {r: max(0, _as_int(raw.get(r), defaults[r], 0, 100000)) for r in RARITIES}
            if not allow_zero and sum(out.values()) <= 0:
                raise ConfigError(f"{key} 不能全部为 0")
            return out

        rare = _as_int(config.get("rare_min_bst"), DEFAULT_RARE_MIN_BST, 0, 2000)
        super_rare = _as_int(
            config.get("super_rare_min_bst"), DEFAULT_SUPER_RARE_MIN_BST, 0, 2000
        )
        ultra_rare = _as_int(
            config.get("ultra_rare_min_bst"), DEFAULT_ULTRA_RARE_MIN_BST, 0, 2000
        )
        if not (rare < super_rare < ultra_rare):
            raise ConfigError(
                "稀有度阈值必须满足 rare_min_bst < super_rare_min_bst "
                "< ultra_rare_min_bst"
            )
        return cls(
            weights=read_weights("rarity_weights", DEFAULT_WEIGHTS),
            duplicate_dust=read_weights(
                "duplicate_dust", DEFAULT_DUPLICATE_DUST, allow_zero=True
            ),
            spark_costs=read_weights(
                "spark_costs", DEFAULT_SPARK_COSTS, allow_zero=True
            ),
            rare_min_bst=rare,
            super_rare_min_bst=super_rare,
            ultra_rare_min_bst=ultra_rare,
            draws_per_command=_as_int(
                config.get("draws_per_command"), 1, 1, MAX_DRAWS_PER_COMMAND
            ),
            daily_limit=_as_int(config.get("daily_draw_limit"), 3, 0, MAX_DAILY_LIMIT),
            cooldown_seconds=_as_float(
                config.get("draw_cooldown_seconds"), 3.0, 0.0, 3600.0
            ),
            ssr_pity_after=_as_int(config.get("ssr_pity_after"), 30, 0, MAX_PITY),
            new_pity_after=_as_int(config.get("new_pity_after"), 0, 0, MAX_PITY),
            allow_duplicate_draw=_as_bool(config.get("allow_duplicate_draw"), True),
            shiny_chance_percent=_as_float(
                config.get("shiny_chance_percent"), 1.5, 0.0, 100.0
            ),
            shiny_dust_multiplier=_as_float(
                config.get("shiny_dust_multiplier"), 3.0, 1.0, 100.0
            ),
            shiny_spark_multiplier=_as_float(
                config.get("shiny_spark_multiplier"), 4.0, 1.0, 100.0
            ),
            include_alternate_forms=_as_bool(
                config.get("include_alternate_forms"), False
            ),
        )

    def rarity_for_bst(self, bst: int) -> str:
        if bst >= self.ultra_rare_min_bst:
            return "SSR"
        if bst >= self.super_rare_min_bst:
            return "SR"
        if bst >= self.rare_min_bst:
            return "R"
        return "C"

    def weight_list(self) -> list[int]:
        return [max(0, int(self.weights.get(r, 0))) for r in RARITIES]

    def dust_for(self, rarity: str, shiny: bool = False) -> int:
        amount = max(0, int(self.duplicate_dust.get(rarity, 0)))
        return int(round(amount * self.shiny_dust_multiplier)) if shiny else amount

    def spark_cost_for(self, rarity: str, shiny: bool = False) -> int:
        amount = max(0, int(self.spark_costs.get(rarity, 0)))
        return int(round(amount * self.shiny_spark_multiplier)) if shiny else amount


@dataclass(frozen=True)
class MilestoneReward:
    threshold: int
    dust: int
    bonus: int


@dataclass(frozen=True)
class Partner:
    """抽卡池成员。``key`` 是收藏唯一键（编号+形态）。"""

    key: str
    dex: int
    dex_raw: str
    name: str
    types: str
    bst: int
    rarity: str
    row: Mapping[str, Any]

    @property
    def rarity_name(self) -> str:
        return rarity_label(self.rarity)


@dataclass(frozen=True)
class DrawItem:
    key: str
    dex: int
    dex_raw: str
    name: str
    types: str
    bst: int
    rarity: str
    shiny: bool
    is_new: bool
    dust: int
    owned_count: int
    row: Mapping[str, Any]

    @property
    def rarity_name(self) -> str:
        return rarity_label(self.rarity)


@dataclass(frozen=True)
class DrawOutcome:
    items: tuple[DrawItem, ...]
    requested: int
    used_today: int
    daily_limit: int
    remaining: int
    dust: int
    pity_ssr: int
    pity_new: int
    capped: bool
    milestones: tuple[MilestoneReward, ...] = ()

    @property
    def new_count(self) -> int:
        return sum(1 for item in self.items if item.is_new)

    @property
    def shiny_count(self) -> int:
        return sum(1 for item in self.items if item.shiny)


@dataclass(frozen=True)
class ProfileSummary:
    nickname: str
    collected: int
    total: int
    shiny_count: int
    dust: int
    used_today: int
    daily_limit: int
    remaining: int
    pity_ssr: int
    pity_new: int
    total_draws: int
    next_milestone: int | None = None

    @property
    def percent(self) -> float:
        return (self.collected / self.total * 100.0) if self.total else 0.0


@dataclass(frozen=True)
class OwnedPartner:
    partner: Partner
    normal_count: int
    shiny_count: int
    first_normal: str
    first_shiny: str


@dataclass(frozen=True)
class ExchangeResult:
    partner: Partner
    shiny: bool
    cost: int
    dust: int
    milestones: tuple[MilestoneReward, ...] = ()


def build_partners(
    db: Any,
    rules: GachaRules,
    include_alternate_forms: bool | None = None,
) -> tuple[Partner, ...]:
    """从图鉴库构建抽卡池。

    默认只取默认形态：1025 个种族，避免 295 个形态把图鉴和抽卡结果冲散。
    跳过没有种族值数据的行；同一 ``(id, form)`` 只保留一次，防止重复行放大概率。
    """
    include_forms = (
        rules.include_alternate_forms
        if include_alternate_forms is None
        else include_alternate_forms
    )
    seen: set[str] = set()
    out: list[Partner] = []
    for row in db.table("pokemon"):
        form = str(row.get("form_name") or "").strip()
        if form and not include_forms:
            continue
        parsed = db.first_stats(row)
        if not parsed:
            continue
        dex_raw = str(row.get("id_raw") or "").strip()
        try:
            dex = int(row.get("id") or dex_raw)
        except (TypeError, ValueError):
            continue
        name = str(row.get("name_zh") or "").strip()
        if not name:
            continue
        key = f"{dex}:{form}" if form else str(dex)
        if key in seen:
            continue
        seen.add(key)
        bst = int(parsed[1])
        out.append(
            Partner(
                key=key,
                dex=dex,
                dex_raw=dex_raw or f"{dex:04d}",
                name=f"{name}（{form}）" if form else name,
                types=str(row.get("types") or ""),
                bst=bst,
                rarity=rules.rarity_for_bst(bst),
                row=row,
            )
        )
    return tuple(out)
