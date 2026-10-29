"""抽卡状态存储（JSON 快照 + 原子写入）。

与 :mod:`gacha` 的规则/数据类分离，方便以后替换为 SQLite 或接入 WebUI。
所有公开方法都是线程安全的；调用方可以直接在 ``asyncio.to_thread`` 里调用。
"""

from __future__ import annotations

import json
import logging
import os
import random
import shutil
import threading
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None

try:
    import msvcrt
except ImportError:  # POSIX
    msvcrt = None

try:
    from .gacha import (
        ALL_COLLECTED_BONUS,
        ALL_COLLECTED_DUST,
        MAX_DAILY_LIMIT,
        MAX_DRAWS_PER_COMMAND,
        MILESTONE_REWARDS,
        RARITIES,
        DrawItem,
        DrawOutcome,
        ExchangeResult,
        GachaError,
        GachaRules,
        MilestoneReward,
        OwnedPartner,
        Partner,
        ProfileSummary,
    )
except ImportError:  # 兼容以普通模块方式加载
    from gacha import (
        ALL_COLLECTED_BONUS,
        ALL_COLLECTED_DUST,
        MAX_DAILY_LIMIT,
        MAX_DRAWS_PER_COMMAND,
        MILESTONE_REWARDS,
        RARITIES,
        DrawItem,
        DrawOutcome,
        ExchangeResult,
        GachaError,
        GachaRules,
        MilestoneReward,
        OwnedPartner,
        Partner,
        ProfileSummary,
    )

STATE_VERSION = 2

logger = logging.getLogger(__name__)


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


def today_key() -> str:
    """按服务器本地日期跨天重置。"""
    return datetime.now().astimezone().date().isoformat()


class GachaStore:
    """线程安全的抽卡状态存储。"""

    def __init__(
        self,
        state_dir: str | Path,
        partners: Iterable[Partner],
        rules: GachaRules,
        rng: random.Random | None = None,
    ) -> None:
        self.state_dir = Path(state_dir)
        self.state_path = self.state_dir / "state.json"
        self.lock_path = self.state_dir / "state.lock"
        self.rules = rules
        self.rng = rng or random.Random()
        self._lock = threading.RLock()
        self._transaction_depth = 0
        self.partners = tuple(partners)
        if not self.partners:
            raise GachaError("抽卡池为空，请检查图鉴数据。")
        self.by_dex: dict[int, list[Partner]] = {}
        self.by_key: dict[str, Partner] = {}
        self.by_rarity: dict[str, list[Partner]] = {r: [] for r in RARITIES}
        self._name_buckets: dict[str, list[Partner]] = {}
        for partner in self.partners:
            self.by_key[partner.key] = partner
            self.by_dex.setdefault(partner.dex, []).append(partner)
            self.by_rarity.setdefault(partner.rarity, []).append(partner)
            for value in (
                partner.name,
                partner.row.get("name_zh"),
                partner.row.get("name_en"),
                partner.row.get("name_ja"),
                partner.dex_raw,
                partner.dex,
            ):
                key = _normalise(value)
                if key:
                    bucket = self._name_buckets.setdefault(key, [])
                    if partner not in bucket:
                        bucket.append(partner)
        self._load_failed = False
        self._restored_from_backup = False
        self.state = self._load()

    # ---------- 持久化 ----------
    def _load(self) -> dict[str, Any]:
        self._load_failed = False
        for path in (self.state_path, self.state_path.with_suffix(".json.bak")):
            if not path.is_file():
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError) as exc:
                logger.warning("宝可梦数据文件无法读取，尝试下一份备份: %s (%s)", path, exc)
                continue
            if (
                isinstance(raw, dict)
                and raw.get("version") == STATE_VERSION
                and isinstance(raw.get("scopes"), dict)
            ):
                if path is not self.state_path:
                    self._restored_from_backup = True
                    logger.warning(
                        "宝可梦主数据损坏或缺失，已从备份恢复: %s", path
                    )
                    self._snapshot_corrupt_state()
                return raw
            logger.warning("宝可梦数据文件版本不匹配，已跳过: %s", path)
        if self.state_path.exists() or self.state_path.with_suffix(".json.bak").exists():
            logger.error(
                "宝可梦数据无法恢复，将以空数据启动。原文件保留在 %s，"
                "请人工检查后再继续使用，避免覆盖。",
                self.state_path,
            )
            self._load_failed = True
        return {"version": STATE_VERSION, "scopes": {}}

    def _snapshot_corrupt_state(self) -> None:
        """把损坏的主文件另存一份，避免后续写入抹掉现场。"""
        try:
            if not self.state_path.is_file():
                return
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            target = self.state_path.with_name(f"state.corrupt-{stamp}.json")
            shutil.copy2(self.state_path, target)
            logger.warning("已保留损坏的宝可梦数据快照: %s", target)
        except OSError as exc:
            logger.warning("保留损坏数据快照失败: %s", exc)

    def _save(self) -> None:
        if self._transaction_depth:
            self._write_snapshot()
        else:
            with self._lock, self._file_lock():
                self._write_snapshot()

    def _write_snapshot(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        if getattr(self, "_load_failed", False):
            # 主文件与备份都不可读时拒绝写入，避免用空数据覆盖掉仅存的现场。
            raise GachaError(
                "宝可梦数据文件已损坏，为避免覆盖原数据已停止写入。"
                f"请备份并检查 {self.state_path} 后重启插件。"
            )
        payload = json.dumps(self.state, ensure_ascii=False, indent=2, sort_keys=True)
        tmp = self.state_path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if self.state_path.is_file() and self._is_readable_state(self.state_path):
            # 保留主文件直到新快照提交成功；避免 replace 失败后主文件消失。
            backup_tmp = self.state_path.with_suffix(".json.bak.tmp")
            shutil.copy2(self.state_path, backup_tmp)
            os.replace(backup_tmp, self.state_path.with_suffix(".json.bak"))
        os.replace(tmp, self.state_path)

    @contextmanager
    def _transaction(self):
        """整段读改写共用文件锁；重载期间多个实例也不会丢抽卡或复活删除数据。"""
        with self._lock:
            if self._transaction_depth:
                yield
                return
            with self._file_lock():
                self.state = self._load()
                before = deepcopy(self.state)
                rng_state = self.rng.getstate()
                self._transaction_depth += 1
                try:
                    yield
                except Exception:
                    self.state = before
                    self.rng.setstate(rng_state)
                    raise
                finally:
                    self._transaction_depth -= 1

    def snapshot(self) -> dict[str, Any]:
        """返回一致的只读快照，WebUI 不遍历工作线程正在修改的字典。"""
        with self._lock, self._file_lock():
            self.state = self._load()
            return deepcopy(self.state)

    def _file_lock(self):
        """跨进程/跨实例互斥锁。拿不到锁时拒绝写入以保护资料。"""
        return _CrossProcessLock(self.lock_path)

    @staticmethod
    def _is_readable_state(path: Path) -> bool:
        """只有能解析的旧文件才值得保留为备份。"""
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return False
        return (
            isinstance(raw, dict)
            and raw.get("version") == STATE_VERSION
            and isinstance(raw.get("scopes"), dict)
        )

    def reload(self) -> None:
        with self._lock, self._file_lock():
            self.state = self._load()

    # ---------- 资料读写 ----------
    def _profile(self, scope: str, user_id: str, nickname: str = "") -> dict[str, Any]:
        scopes = self.state.setdefault("scopes", {})
        if not isinstance(scopes, dict):
            scopes = {}
            self.state["scopes"] = scopes
        scope_data = scopes.setdefault(str(scope), {})
        if not isinstance(scope_data, dict):
            scope_data = {}
            scopes[str(scope)] = scope_data
        users = scope_data.setdefault("users", {})
        if not isinstance(users, dict):
            users = {}
            scope_data["users"] = users
        profile = users.setdefault(str(user_id), {})
        if not isinstance(profile, dict):
            profile = {}
            users[str(user_id)] = profile
        for key in ("owned", "counts", "bonus", "milestones"):
            if not isinstance(profile.get(key), dict):
                profile[key] = {}
        profile["dust"] = max(0, _as_int(profile.get("dust"), 0))
        profile["pity_ssr"] = max(0, _as_int(profile.get("pity_ssr"), 0))
        profile["pity_new"] = max(0, _as_int(profile.get("pity_new"), 0))
        profile["total_draws"] = max(0, _as_int(profile.get("total_draws"), 0))
        if nickname:
            profile["nickname"] = str(nickname)[:48]
        return profile

    @staticmethod
    def _prune(profile: dict[str, Any], keep_days: int = 60) -> None:
        cutoff = (date.today() - timedelta(days=keep_days)).isoformat()
        for key in ("counts", "bonus"):
            data = profile.get(key)
            if not isinstance(data, dict):
                continue
            for day in [str(d) for d in data if str(d) < cutoff]:
                data.pop(day, None)

    @staticmethod
    def _used(profile: Mapping[str, Any], today: str) -> int:
        counts = profile.get("counts", {})
        return max(0, _as_int(counts.get(today), 0)) if isinstance(counts, Mapping) else 0

    @staticmethod
    def _bonus(profile: Mapping[str, Any], today: str) -> int:
        bonus = profile.get("bonus", {})
        return max(0, _as_int(bonus.get(today), 0)) if isinstance(bonus, Mapping) else 0

    @staticmethod
    def _owned(profile: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
        out: dict[str, tuple[int, int]] = {}
        owned = profile.get("owned", {})
        if not isinstance(owned, Mapping):
            return out
        for key, value in owned.items():
            if not isinstance(value, Mapping):
                continue
            normal = max(0, _as_int(value.get("normal"), 0))
            shiny = max(0, _as_int(value.get("shiny"), 0))
            if normal or shiny:
                out[str(key)] = (normal, shiny)
        return out

    def _active_owned(self, profile: Mapping[str, Any]) -> dict[str, tuple[int, int]]:
        """Count the current pool while retaining temporarily excluded forms on disk."""
        return {key: counts for key, counts in self._owned(profile).items() if key in self.by_key}

    def _grant_milestones(
        self, profile: dict[str, Any], today: str
    ) -> list[MilestoneReward]:
        collected = len(self._active_owned(profile))
        claimed = profile.setdefault("milestones", {})
        if not isinstance(claimed, dict):
            claimed = {}
            profile["milestones"] = claimed
        rewards: list[MilestoneReward] = []
        for threshold in sorted(MILESTONE_REWARDS):
            if collected < threshold or str(threshold) in claimed:
                continue
            dust, bonus = MILESTONE_REWARDS[threshold]
            claimed[str(threshold)] = today
            profile["dust"] = max(0, _as_int(profile.get("dust"), 0)) + dust
            if bonus:
                days = profile.setdefault("bonus", {})
                if isinstance(days, dict):
                    days[today] = max(0, _as_int(days.get(today), 0)) + bonus
            rewards.append(MilestoneReward(threshold, dust, bonus))
        if collected >= len(self.partners) and "all" not in claimed:
            claimed["all"] = today
            profile["dust"] = (
                max(0, _as_int(profile.get("dust"), 0)) + ALL_COLLECTED_DUST
            )
            days = profile.setdefault("bonus", {})
            if isinstance(days, dict):
                days[today] = max(0, _as_int(days.get(today), 0)) + ALL_COLLECTED_BONUS
            rewards.append(
                MilestoneReward(
                    len(self.partners), ALL_COLLECTED_DUST, ALL_COLLECTED_BONUS
                )
            )
        return rewards

    # ---------- 查询 ----------
    def get_profile(
        self,
        scope: str,
        user_id: str,
        nickname: str = "",
        *,
        today: str | None = None,
    ) -> ProfileSummary:
        with self._lock, self._file_lock():
            self.state = self._load()
            profile = self._profile(scope, user_id, nickname)
            today = today or today_key()
            used = self._used(profile, today)
            limit = self.rules.daily_limit + self._bonus(profile, today)
            owned = self._active_owned(profile)
            return ProfileSummary(
                nickname=str(profile.get("nickname") or nickname or "训练家"),
                collected=len(owned),
                total=len(self.partners),
                shiny_count=sum(1 for v in owned.values() if v[1] > 0),
                dust=max(0, _as_int(profile.get("dust"), 0)),
                used_today=used,
                daily_limit=limit,
                remaining=max(0, limit - used),
                pity_ssr=max(0, _as_int(profile.get("pity_ssr"), 0)),
                pity_new=max(0, _as_int(profile.get("pity_new"), 0)),
                total_draws=max(0, _as_int(profile.get("total_draws"), 0)),
                next_milestone=next(
                    (t for t in sorted(MILESTONE_REWARDS)
                     if t > len(owned) and str(t) not in profile.get("milestones", {})), None
                ),
            )

    def owned_partners(self, scope: str, user_id: str) -> list[OwnedPartner]:
        with self._lock, self._file_lock():
            self.state = self._load()
            profile = self._profile(scope, user_id)
            owned = profile.get("owned", {})
            out: list[OwnedPartner] = []
            for key, (normal, shiny) in self._owned(profile).items():
                partner = self.by_key.get(key)
                if partner is None:
                    continue
                raw = owned.get(key, {}) if isinstance(owned, Mapping) else {}
                if not isinstance(raw, Mapping):
                    raw = {}
                out.append(
                    OwnedPartner(
                        partner=partner,
                        normal_count=normal,
                        shiny_count=shiny,
                        first_normal=str(raw.get("first_normal") or ""),
                        first_shiny=str(raw.get("first_shiny") or ""),
                    )
                )
            out.sort(key=lambda item: (item.partner.dex, item.partner.name))
            return out

    def find_partner(self, keyword: str) -> Partner | None:
        target = _normalise(keyword)
        if not target:
            return None
        exact = self._name_buckets.get(target, [])
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            default = [p for p in exact if p.key == str(p.dex)]
            return default[0] if len(default) == 1 else None
        unique = {
            partner.key: partner
            for key, items in self._name_buckets.items()
            if target in key
            for partner in items
        }
        return next(iter(unique.values())) if len(unique) == 1 else None

    def suggestion_for(self, keyword: str, limit: int = 5) -> list[str]:
        target = _normalise(keyword)
        if not target:
            return []
        hits = {
            partner.name
            for key, items in self._name_buckets.items()
            if target in key or key in target
            for partner in items
        }
        return sorted(hits)[:limit]

    def _ranking_rows(self, scope: str) -> list[tuple[str, str, int, int, int]]:
        scope_data = self.state.get("scopes", {}).get(str(scope), {})
        users = scope_data.get("users", {}) if isinstance(scope_data, Mapping) else {}
        rows = []
        for user_id, profile in users.items():
            if not isinstance(profile, Mapping):
                continue
            owned = self._active_owned(profile)
            if not owned:
                continue
            rows.append((
                str(user_id), str(profile.get("nickname") or user_id), len(owned),
                sum(1 for count in owned.values() if count[1] > 0),
                max(0, _as_int(profile.get("total_draws"), 0)),
            ))
        return sorted(rows, key=lambda item: (-item[2], -item[3], -item[4], item[1], item[0]))

    def leaderboard(self, scope: str, limit: int = 10) -> list[tuple[int, str, int, int]]:
        """``[(名次, 昵称, 宝可梦数, 闪光数)]``。"""
        with self._lock, self._file_lock():
            self.state = self._load()
            rows = self._ranking_rows(scope)
            return [
                (index + 1, nickname, collected, shiny)
                for index, (_user_id, nickname, collected, shiny, _draws) in enumerate(
                    rows[:max(1, min(50, int(limit)))]
                )
            ]

    def user_rank(self, scope: str, user_id: str) -> int | None:
        with self._lock, self._file_lock():
            self.state = self._load()
            return next((
                index + 1 for index, row in enumerate(self._ranking_rows(scope))
                if row[0] == str(user_id)
            ), None)

    # ---------- 抽卡 ----------
    def effective_weights(self) -> dict[str, int]:
        """实际可用的稀有度权重。

        若配置给了某档权重但该档卡池为空，强行抽取只能回退到全池，会让展示概率
        与实际结果不符。这里直接剔除空档位后再归一化，保底也只针对可用档位。
        """
        usable = {
            rarity: weight
            for rarity, weight in zip(RARITIES, self.rules.weight_list())
            if weight > 0 and self.by_rarity.get(rarity)
        }
        if usable:
            return usable
        # 所有配置档位都为空时退化为全池等概率，至少保证能抽。
        return {
            rarity: 1 for rarity in RARITIES if self.by_rarity.get(rarity)
        } or {"C": 1}

    def pity_rarity(self) -> str | None:
        """当前是否触发 SSR 保底；卡池没有 SSR 时返回 ``None``。"""
        after = self.rules.ssr_pity_after
        if after <= 0 or not self.by_rarity.get("SSR"):
            return None
        return "SSR"

    def _ssr_due(self, profile: Mapping[str, Any]) -> bool:
        pity_target = self.pity_rarity()
        pity = max(0, _as_int(profile.get("pity_ssr"), 0))
        return bool(
            pity_target
            and self.rules.ssr_pity_after > 0
            and pity >= self.rules.ssr_pity_after - 1
        )

    def _roll_rarity(self, profile: Mapping[str, Any]) -> str:
        if self._ssr_due(profile):
            return "SSR"
        weights = self.effective_weights()
        rarities = list(weights)
        return self.rng.choices(
            rarities, weights=[weights[r] for r in rarities], k=1
        )[0]

    def _choose(
        self, rarity: str, *, force_new: bool, owned: Mapping[str, tuple[int, int]],
        preserve_rarity: bool = False,
    ) -> Partner:
        pool = self.by_rarity.get(rarity) or list(self.partners)
        if force_new or not self.rules.allow_duplicate_draw:
            new_pool = [p for p in pool if p.key not in owned]
            if not new_pool and not preserve_rarity:
                new_pool = [p for p in self.partners if p.key not in owned]
            if new_pool:
                return self.rng.choice(new_pool)
        return self.rng.choice(pool)

    def _draw_one(
        self, profile: dict[str, Any], today: str
    ) -> tuple[DrawItem, list[MilestoneReward]]:
        force_new = (
            self.rules.new_pity_after > 0
            and max(0, _as_int(profile.get("pity_new"), 0))
            >= self.rules.new_pity_after
        )
        rarity = self._roll_rarity(profile)
        # When both guarantees are due, an available new SSR satisfies both.
        # Otherwise keep the SSR guarantee and retain the new-pity counter.
        partner = self._choose(rarity, force_new=force_new, owned=self._owned(profile),
                               preserve_rarity=self._ssr_due(profile))
        shiny = (
            self.rules.shiny_chance_percent > 0
            and self.rng.random() * 100.0 < self.rules.shiny_chance_percent
        )
        owned_raw = profile.setdefault("owned", {})
        if not isinstance(owned_raw, dict):
            owned_raw = {}
            profile["owned"] = owned_raw
        entry = owned_raw.get(partner.key)
        if not isinstance(entry, dict):
            entry = {}
            owned_raw[partner.key] = entry
        variant = "shiny" if shiny else "normal"
        before = max(0, _as_int(entry.get(variant), 0))
        is_new = before <= 0
        entry[variant] = before + 1
        if is_new:
            entry[f"first_{variant}"] = today
        dust = 0 if is_new else self.rules.dust_for(partner.rarity, shiny)
        if dust:
            profile["dust"] = max(0, _as_int(profile.get("dust"), 0)) + dust
        # 卡池没有 SSR 时保底无法兑现，不要累积计数，否则补上数据后会连续爆发。
        if partner.rarity == "SSR" or not self.by_rarity.get("SSR"):
            profile["pity_ssr"] = 0
        else:
            profile["pity_ssr"] = max(0, _as_int(profile.get("pity_ssr"), 0)) + 1
        profile["pity_new"] = (
            0 if is_new else max(0, _as_int(profile.get("pity_new"), 0)) + 1
        )
        milestones = self._grant_milestones(profile, today)
        return (
            DrawItem(
                key=partner.key,
                dex=partner.dex,
                dex_raw=partner.dex_raw,
                name=partner.name,
                types=partner.types,
                bst=partner.bst,
                rarity=partner.rarity,
                shiny=shiny,
                is_new=is_new,
                dust=dust,
                owned_count=before + 1,
                row=partner.row,
            ),
            milestones,
        )

    def draw(
        self,
        scope: str,
        user_id: str,
        nickname: str = "",
        count: int | None = None,
        *,
        now: float | None = None,
        today: str | None = None,
    ) -> DrawOutcome:
        requested = _as_int(
            count if count is not None else self.rules.draws_per_command,
            self.rules.draws_per_command,
            1,
            MAX_DRAWS_PER_COMMAND,
        )
        now = time.time() if now is None else float(now)
        today = today or today_key()
        with self._transaction():
            profile = self._profile(scope, user_id, nickname)
            last = float(profile.get("last_draw_ts") or 0.0)
            if self.rules.cooldown_seconds > 0 and last > 0:
                wait = self.rules.cooldown_seconds - (now - last)
                if wait > 0:
                    raise GachaError(f"召唤太快啦，请等待 {wait:.1f} 秒后再试。")
            used = self._used(profile, today)
            limit = self.rules.daily_limit + self._bonus(profile, today)
            remaining = max(0, limit - used)
            if remaining <= 0:
                if limit <= 0:
                    raise GachaError(
                        "当前没有可用的召唤次数。请联系管理员开放每日次数，"
                        "或等待收集里程碑发放额外次数。"
                    )
                raise GachaError(f"今天的 {limit} 次今日宝可梦已经抽完了，明天再来吧。")
            actual = min(requested, remaining)
            items: list[DrawItem] = []
            milestones: list[MilestoneReward] = []
            for _ in range(actual):
                item, gained = self._draw_one(profile, today)
                items.append(item)
                milestones.extend(gained)
            counts = profile.setdefault("counts", {})
            if isinstance(counts, dict):
                counts[today] = used + actual
            profile["total_draws"] = (
                max(0, _as_int(profile.get("total_draws"), 0)) + actual
            )
            profile["last_draw_ts"] = now
            self._prune(profile)
            limit = self.rules.daily_limit + self._bonus(profile, today)
            self._save()
            return DrawOutcome(
                items=tuple(items),
                requested=requested,
                used_today=used + actual,
                daily_limit=limit,
                remaining=max(0, limit - used - actual),
                dust=max(0, _as_int(profile.get("dust"), 0)),
                pity_ssr=max(0, _as_int(profile.get("pity_ssr"), 0)),
                pity_new=max(0, _as_int(profile.get("pity_new"), 0)),
                capped=actual < requested,
                milestones=tuple(milestones),
            )

    # ---------- 兑换 / 运营 ----------
    def exchange(
        self,
        scope: str,
        user_id: str,
        keyword: str,
        *,
        shiny: bool = False,
        nickname: str = "",
        today: str | None = None,
    ) -> ExchangeResult:
        partner = self.find_partner(keyword)
        if partner is None:
            hint = self.suggestion_for(keyword)
            extra = f"你要找的是：{'、'.join(hint)}" if hint else ""
            raise GachaError(f"没有找到可兑换的宝可梦「{keyword}」。{extra}".strip())
        today = today or today_key()
        with self._transaction():
            profile = self._profile(scope, user_id, nickname)
            owned = profile.setdefault("owned", {})
            if not isinstance(owned, dict):
                owned = {}
                profile["owned"] = owned
            entry = owned.get(partner.key)
            if not isinstance(entry, dict):
                entry = {}
            variant = "shiny" if shiny else "normal"
            if max(0, _as_int(entry.get(variant), 0)) > 0:
                label = "闪光" if shiny else ""
                raise GachaError(f"你已经拥有{label}{partner.name}了。")
            cost = self.rules.spark_cost_for(partner.rarity, shiny)
            dust = max(0, _as_int(profile.get("dust"), 0))
            if dust < cost:
                raise GachaError(f"星尘不足：兑换需要 {cost}，当前只有 {dust}。")
            profile["dust"] = dust - cost
            entry[variant] = 1
            entry[f"first_{variant}"] = today
            entry.setdefault("normal", 0)
            owned[partner.key] = entry
            milestones = self._grant_milestones(profile, today)
            self._save()
            return ExchangeResult(
                partner=partner,
                shiny=shiny,
                cost=cost,
                dust=max(0, _as_int(profile.get("dust"), 0)),
                milestones=tuple(milestones),
            )

    def add_bonus(
        self, scope: str, user_id: str, amount: int = 1, *, today: str | None = None
    ) -> int:
        amount = _as_int(amount, 1, 1, MAX_DAILY_LIMIT)
        today = today or today_key()
        with self._transaction():
            profile = self._profile(scope, user_id)
            bonus = profile.setdefault("bonus", {})
            if not isinstance(bonus, dict):
                bonus = {}
                profile["bonus"] = bonus
            bonus[today] = max(0, _as_int(bonus.get(today), 0)) + amount
            self._save()
            return bonus[today]

    def reset_today(self, scope: str, user_id: str, *, today: str | None = None) -> None:
        today = today or today_key()
        with self._transaction():
            profile = self._profile(scope, user_id)
            counts = profile.setdefault("counts", {})
            if isinstance(counts, dict):
                counts.pop(today, None)
            self._save()

    def reset_scope_today(self, scope: str, *, today: str | None = None) -> int:
        today = today or today_key()
        with self._transaction():
            scope_data = self.state.get("scopes", {}).get(str(scope), {})
            users = scope_data.get("users", {}) if isinstance(scope_data, Mapping) else {}
            changed = 0
            for profile in users.values():
                if not isinstance(profile, dict):
                    continue
                counts = profile.get("counts")
                if isinstance(counts, dict) and today in counts:
                    counts.pop(today)
                    changed += 1
            if changed:
                self._save()
            return changed

    def set_dust(self, scope: str, user_id: str, amount: int) -> int:
        with self._transaction():
            profile = self._profile(scope, user_id)
            profile["dust"] = max(0, _as_int(amount, 0))
            self._save()
            return profile["dust"]

    def update_member(self, scope: str, user_id: str, field: str, value: Any) -> Any:
        """管理字段也通过与抽卡相同的文件事务提交。"""
        if field not in {"nickname", "pity_ssr", "pity_new", "total_draws"}:
            raise ValueError(f"不支持的字段: {field}")
        normalised = str(value or "")[:48] if field == "nickname" else max(0, int(value or 0))
        with self._transaction():
            profile = self._profile(scope, user_id)
            profile[field] = normalised
            self._save()
        return normalised

    def delete_scope(self, scope: str, user_id: str = "") -> int:
        """删除指定成员或整个会话，不把磁盘旧资料重新合并回来。"""
        with self._transaction():
            scopes = self.state.get("scopes", {})
            data = scopes.get(str(scope))
            users = data.get("users", {}) if isinstance(data, dict) else {}
            if not isinstance(users, dict):
                return 0
            if user_id:
                removed = int(users.pop(str(user_id), None) is not None)
                if removed:
                    duels = data.get("duels", {})
                    pending = duels.get("pending", {}) if isinstance(duels, dict) else {}
                    if isinstance(pending, dict):
                        for match_id, match in list(pending.items()):
                            if not isinstance(match, Mapping):
                                continue
                            participants = (match.get("challenger", {}), match.get("target", {}))
                            if any(isinstance(side, Mapping) and str(side.get("user_id", "")) == str(user_id)
                                   for side in participants):
                                pending.pop(match_id)
            else:
                removed = len(users)
                users.clear()
            if not users:
                scopes.pop(str(scope), None)
            if removed:
                self._save()
            return removed


def _normalise(value: Any) -> str:
    """名称匹配：去空白、统一大小写、去掉常见标点。"""
    import re

    text = str(value or "").strip().casefold()
    return re.sub(r"[\s·・\-_—–（）()【】\[\]「」『』]", "", text)


class _CrossProcessLock:
    """跨平台文件锁：POSIX 用 ``fcntl``，Windows 用 ``msvcrt``。

    单进程内的并发已经由 ``RLock`` 保证；文件锁只用于防止多个插件实例
    （例如热重载瞬间新旧实例并存）互相覆盖状态文件。
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle = None

    def __enter__(self) -> "_CrossProcessLock":
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = open(self.path, "a+b")
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX)
            elif msvcrt is not None:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_LOCK, 1)
        except OSError as exc:
            logger.error("宝可梦数据文件锁不可用，已停止读写: %s", exc)
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None
            raise GachaError("宝可梦资料暂时无法加锁，请稍后重试，数据尚未写入。") from exc
        return self

    def __exit__(self, *_exc: object) -> None:
        if self._handle is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
            elif msvcrt is not None:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        finally:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
