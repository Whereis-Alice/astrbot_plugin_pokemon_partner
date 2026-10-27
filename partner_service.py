"""伙伴系统的业务编排：图鉴库 + 抽卡存储 + 图片素材。

这一层不依赖 AstrBot，负责把 :mod:`gacha` 与 :mod:`pokedex_db` 组装起来，
并处理素材查找。插件入口只做事件/消息转换，方便以后加 WebUI 或测试。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from .gacha import (
        GachaRules,
        Partner,
        ProfileSummary,
        build_partners,
    )
    from .gacha_store import GachaStore
except ImportError:  # 兼容以普通模块方式加载
    from gacha import (
        GachaRules,
        Partner,
        ProfileSummary,
        build_partners,
    )
    from gacha_store import GachaStore

ArtworkLoader = Callable[[Mapping[str, Any]], "Any | None"]


@dataclass(frozen=True)
class PartnerService:
    """把抽卡池、规则和素材查找绑在一起。"""

    db: Any
    store: GachaStore
    artwork_loader: ArtworkLoader | None = None

    @classmethod
    def create(
        cls,
        db: Any,
        state_dir: str | Path,
        rules: GachaRules,
        *,
        artwork_loader: ArtworkLoader | None = None,
    ) -> "PartnerService":
        partners = build_partners(db, rules)
        store = GachaStore(state_dir, partners, rules)
        return cls(db=db, store=store, artwork_loader=artwork_loader)

    @property
    def partners(self) -> tuple[Partner, ...]:
        return self.store.partners

    def recovery_notice(self) -> str:
        """数据从备份恢复或无法读取时的用户可见提示；正常时返回空串。"""
        if getattr(self.store, "_load_failed", False):
            return "伙伴数据文件损坏且备份不可用，已停止写入以保护原文件，请联系管理员检查。"
        if getattr(self.store, "_restored_from_backup", False):
            return "提示：检测到伙伴主数据损坏，已从备份恢复，最后一次写入可能丢失。"
        return ""

    def pool_counts(self) -> dict[str, int]:
        return {rarity: len(items) for rarity, items in self.store.by_rarity.items()}

    def effective_weights(self) -> dict[str, int]:
        """实际生效的稀有度权重（已剔除空档位）。"""
        return self.store.effective_weights()

    def artwork(self, row: Mapping[str, Any]) -> Any | None:
        if self.artwork_loader is None:
            return None
        try:
            return self.artwork_loader(row)
        except Exception:
            return None

    def profile(self, scope: str, user_id: str, nickname: str = "") -> ProfileSummary:
        return self.store.get_profile(scope, user_id, nickname)

    def owned(self, scope: str, user_id: str):
        return self.store.owned_partners(scope, user_id)

    def rule_text(self) -> str:
        rules = self.store.rules
        effective = self.effective_weights()
        total = sum(effective.values()) or 1
        weights = "｜".join(
            f"{rarity} {effective[rarity] / total * 100:g}%"
            for rarity in ("SSR", "SR", "R", "C")
            if rarity in effective
        )
        lines = [
            f"每次召唤 {rules.draws_per_command} 位，每日 {rules.daily_limit} 次",
            f"稀有度概率：{weights}",
        ]
        if rules.ssr_pity_after:
            lines.append(f"SSR 保底：连续 {rules.ssr_pity_after} 次未出则必出")
        if rules.new_pity_after:
            lines.append(f"新伙伴保底：连续 {rules.new_pity_after} 次重复后必出新")
        lines.append(f"闪光概率 {rules.shiny_chance_percent:g}%")
        return "\n".join(lines)

    def exchange_hint(self, partner: Partner) -> str:
        cost = self.store.rules.spark_cost_for(partner.rarity)
        return f"伙伴兑换 {partner.name}（{cost} 星尘）"
