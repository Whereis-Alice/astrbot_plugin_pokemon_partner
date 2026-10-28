"""双方确认的收藏对战；挑战和战绩与收藏共用原子存档事务。"""

from __future__ import annotations

import secrets
import time
import uuid
import unicodedata
from copy import deepcopy
from typing import Any, Mapping

try:
    from .gacha import GachaError, OwnedPartner
    from .gacha_store import GachaStore
    from .duel_commands import round_limit
except ImportError:
    from gacha import GachaError, OwnedPartner
    from gacha_store import GachaStore
    from duel_commands import round_limit


CHALLENGE_TTL = 300
RUNNING_TTL = 300
MAX_HISTORY = 100


class OwnedDuelService:
    """冻结选中的收藏键，接受后预留一次对局，成功后一次记录双方结果。"""

    def __init__(self, store: GachaStore) -> None:
        self.store = store

    @staticmethod
    def _scope(scope: str) -> str:
        value = str(scope or "").strip()
        if not value.startswith("group:") or not value[6:]:
            raise GachaError("收藏对战只能在群聊中进行。")
        return value

    @staticmethod
    def _user(user_id: str) -> str:
        if isinstance(user_id, bool):
            raise GachaError("训练家 ID 无效。")
        value = str(user_id or "").strip()
        if not value:
            raise GachaError("没有识别到训练家，请 @ 一位群友。")
        return value

    @staticmethod
    def _index(index: int | str) -> int:
        """Validate a National Pokédex number; never treat it as a list position."""
        if isinstance(index, bool) or not str(index).strip().isdecimal():
            raise GachaError("请填写正整数图鉴编号，例如 25，或自己的宝可梦名字。")
        value = int(index)
        if value < 1:
            raise GachaError("全国图鉴编号从 1 开始。")
        return value

    @staticmethod
    def _now(now: float | None) -> float:
        return time.time() if now is None else float(now)

    def _data(self, scope: str, *, create: bool = False) -> dict:
        scopes = self.store.state.get("scopes", {})
        if not isinstance(scopes, dict):
            raise GachaError("收藏存档结构不正确，请联系管理员。")
        if create:
            scope_data = scopes.setdefault(scope, {})
            if not isinstance(scope_data, dict):
                raise GachaError("群收藏存档结构不正确，请联系管理员。")
            data = scope_data.setdefault("duels", {"pending": {}, "stats": {}, "records": []})
            if not isinstance(data, dict):
                raise GachaError("对战存档结构不正确，请联系管理员。")
            for key, value in (("pending", {}), ("stats", {}), ("records", [])):
                if not isinstance(data.get(key), type(value)):
                    raise GachaError("对战存档结构不正确，请联系管理员。")
            return data
        scope_data = scopes.get(scope, {})
        data = scope_data.get("duels", {}) if isinstance(scope_data, dict) else {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _prune(data: dict, now: float) -> None:
        pending = data.get("pending", {})
        if not isinstance(pending, dict):
            return
        for match_id, match in list(pending.items()):
            if not isinstance(match, dict):
                pending.pop(match_id, None)
                continue
            deadline = match.get("running_until") if match.get("status") == "running" else match.get("expires_at")
            if not isinstance(deadline, (int, float)) or deadline <= now:
                pending.pop(match_id, None)

    def _owned(self, scope: str, user_id: str) -> list[OwnedPartner]:
        """事务内读取；与 GachaStore.owned_partners 使用相同收藏项和排序。"""
        profile = self.store._profile(scope, user_id)
        raw = profile.get("owned", {})
        entries = []
        for key, (normal, shiny) in self.store._owned(profile).items():
            partner = self.store.by_key.get(key)
            if partner is None:
                continue
            details = raw.get(key, {}) if isinstance(raw, Mapping) else {}
            if not isinstance(details, Mapping):
                details = {}
            entries.append(OwnedPartner(partner, normal, shiny,
                                        str(details.get("first_normal") or ""),
                                        str(details.get("first_shiny") or "")))
        entries.sort(key=lambda item: (item.partner.dex, item.partner.name))
        return entries

    @staticmethod
    def _normal_name(value: Any) -> str:
        return "".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())

    def _resolve_owned(self, owned: list[OwnedPartner], selection: int | str) -> tuple[int, OwnedPartner]:
        if isinstance(selection, bool):
            raise GachaError("请选择自己已拥有的宝可梦图鉴编号或名字。")
        text = str(selection).strip()
        number = text[1:] if text.startswith(("#", "＃")) else text
        # NFKC name matching would turn a superscript such as ² into 2 and
        # accidentally match a numeric name alias (for example Porygon2).
        if number.isdigit() and not number.isdecimal():
            raise GachaError("请填写正整数图鉴编号，例如 25，或自己的宝可梦名字。")
        if number.isdecimal():
            dex = self._index(number)
            matches = [(item.partner.dex, item) for item in owned if item.partner.dex == dex]
            if not matches:
                raise GachaError(f"你尚未拥有图鉴编号 {dex} 的宝可梦；也可以直接填写已拥有的宝可梦名字。")
            if len(matches) == 1:
                return matches[0]
            defaults = [match for match in matches if not str(match[1].partner.row.get("form_name") or "").strip()]
            if len(defaults) == 1:
                return defaults[0]
            choices = "；".join(f"图鉴编号 {number} · {item.partner.name}" for number, item in matches[:8])
            raise GachaError(f"图鉴编号 {dex} 对应多个已拥有的形态，请直接填写完整宝可梦名字：{choices}")
        needle = self._normal_name(text)
        if not needle:
            raise GachaError("请填写自己已拥有的宝可梦图鉴编号或名字。")
        indexed = []
        for item in owned:
            row = item.partner.row
            names = {self._normal_name(item.partner.name), *(self._normal_name(row.get(column)) for column in
                     ("name_zh", "name_en", "name_ja", "name_jp", "form_name"))}
            names.discard("")
            indexed.append((item.partner.dex, item, names))
        # 完整收藏名称最明确，默认形态与特殊形态共用语言别名时优先它。
        matches = [(dex, item) for dex, item, _ in indexed if needle == self._normal_name(item.partner.name)]
        if not matches:
            matches = [(dex, item) for dex, item, names in indexed if needle in names]
        if not matches:
            matches = [(dex, item) for dex, item, names in indexed if any(needle in name for name in names)]
        if not matches:
            raise GachaError(f"你尚未拥有「{text}」，只能选择自己已抽到的宝可梦。")
        if len(matches) > 1:
            choices = "；".join(f"图鉴编号 {dex} · {item.partner.name}" for dex, item in matches[:8])
            raise GachaError(f"「{text}」对应多个已拥有的宝可梦，请用完整宝可梦名字选择：{choices}")
        return matches[0]

    def _selection(self, scope: str, user_id: str, index: int | str, shiny: bool, nickname: str = "") -> dict:
        if not isinstance(shiny, bool):
            raise GachaError("闪光选择必须为布尔值。")
        owned = self._owned(scope, user_id)
        if not owned:
            raise GachaError("这位训练家还没有收藏宝可梦，先发送「今日宝可梦」吧。")
        dex, selected = self._resolve_owned(owned, index)
        if shiny and selected.shiny_count < 1:
            raise GachaError("你还没有这只宝可梦的闪光个体，不能选择闪光。")
        use_shiny = shiny or selected.normal_count < 1
        profile = self.store._profile(scope, user_id)
        name = str(nickname or profile.get("nickname") or user_id)[:48]
        return {
            "user_id": user_id, "nickname": name, "partner_key": selected.partner.key,
            # Keep the snapshot shape. Existing snapshots retain their old
            # index; ownership and completion always use the frozen key.
            "index": dex, "name": selected.partner.name, "dex": selected.partner.dex_raw,
            "rarity": selected.partner.rarity, "shiny": use_shiny,
        }

    def _check_selection(self, scope: str, selection: Mapping[str, Any]) -> None:
        user_id, key = str(selection["user_id"]), str(selection["partner_key"])
        profile = self.store._profile(scope, user_id)
        counts = self.store._owned(profile).get(key, (0, 0))
        if key not in self.store.by_key or counts[1 if selection.get("shiny") else 0] < 1:
            raise GachaError("对战中选定的宝可梦已不在收藏中，请重新发起挑战。")

    def _public(self, match: Mapping[str, Any]) -> dict:
        result = deepcopy(dict(match))
        for side in ("challenger", "target"):
            selected = result.get(side, {})
            key = selected.get("partner_key")
            if key in self.store.by_key:
                selected["row"] = dict(self.store.by_key[key].row)
        return result

    @staticmethod
    def _busy(data: dict, user_id: str) -> bool:
        return any(user_id in {m["challenger"]["user_id"], m["target"]["user_id"]}
                   for m in data.get("pending", {}).values())

    def challenge(self, scope: str, challenger_id: str, target_id: str, index: int | str, *,
                  challenger_name: str = "", target_name: str = "", shiny: bool = False,
                  max_turns: int = 50, now: float | None = None) -> dict:
        scope, challenger_id, target_id = self._scope(scope), self._user(challenger_id), self._user(target_id)
        if challenger_id == target_id:
            raise GachaError("不能向自己发起挑战，请 @ 一位群友。")
        timestamp = self._now(now)
        max_turns = round_limit(max_turns)
        with self.store._transaction():
            # 所有验证通过才创建挑战，失败不会消耗收藏、星尘或战绩。
            challenger = self._selection(scope, challenger_id, index, shiny, challenger_name)
            target_owned = self._owned(scope, target_id)
            if not target_owned:
                raise GachaError("这位群友还没有收藏宝可梦，暂时无法接受挑战。")
            data = self._data(scope, create=True)
            self._prune(data, timestamp)
            if self._busy(data, challenger_id) or self._busy(data, target_id):
                raise GachaError("双方中已有训练家正在等待或进行对战，请先接受、拒绝或取消原挑战。")
            target_profile = self.store._profile(scope, target_id)
            target = {"user_id": target_id, "nickname": str(target_name or target_profile.get("nickname") or target_id)[:48]}
            match_id = uuid.uuid4().hex
            match = {
                "match_id": match_id, "status": "waiting", "challenger": challenger, "target": target,
                "seed": secrets.randbits(32), "level": 50, "max_turns": max_turns,
                "created_at": timestamp, "expires_at": timestamp + CHALLENGE_TTL,
            }
            data["pending"][match_id] = match
            self.store._save()
            return self._public(match)

    def accept(self, scope: str, target_id: str, index: int | str, *, nickname: str = "",
               shiny: bool = False, now: float | None = None) -> dict:
        scope, target_id = self._scope(scope), self._user(target_id)
        timestamp = self._now(now)
        with self.store._transaction():
            data = self._data(scope)
            self._prune(data, timestamp)
            matches = [m for m in data.get("pending", {}).values() if m["target"]["user_id"] == target_id]
            if not matches:
                raise GachaError("没有等待你接受的挑战，挑战可能已取消或超过 5 分钟。")
            match = matches[0]
            if match["status"] != "waiting":
                raise GachaError("这场对战已经开始，请勿重复接受。")
            self._check_selection(scope, match["challenger"])
            selected = self._selection(scope, target_id, index, shiny, nickname)
            match["target"] = selected
            match["status"] = "running"
            match["accepted_at"] = timestamp
            match["running_until"] = timestamp + RUNNING_TTL
            self.store._save()
            return self._public(match)

    def complete(self, scope: str, match_id: str, result: Mapping[str, Any], *, now: float | None = None) -> dict:
        scope = self._scope(scope)
        timestamp = self._now(now)
        with self.store._transaction():
            data = self._data(scope)
            for record in data.get("records", []):
                if record.get("match_id") == match_id:
                    return deepcopy(record)
            self._prune(data, timestamp)
            match = data.get("pending", {}).get(str(match_id))
            if not match or match.get("status") != "running":
                raise GachaError("对战预留已失效，这次结果不会计入战绩，请重新挑战。")
            self._check_selection(scope, match["challenger"])
            self._check_selection(scope, match["target"])
            setup = result.get("setup", []) if isinstance(result, Mapping) else []
            if len(setup) != 2 or not all(isinstance(side, Mapping) and side.get("name") for side in setup):
                raise GachaError("对战结果缺少双方快照，未记录战绩。")
            labels = [str(side["name"]) for side in setup]
            winner = str(result.get("winner") or "")
            if winner != "平局" and winner not in labels:
                raise GachaError("对战胜者与双方快照不一致，未记录战绩。")
            turns = result.get("turns")
            if isinstance(turns, bool) or not isinstance(turns, int) or not 0 <= turns <= match.get("max_turns", 50):
                raise GachaError("对战回合数无效，未记录战绩。")
            winner_id = "" if winner == "平局" else match[("challenger", "target")[labels.index(winner)]]["user_id"]
            for side in ("challenger", "target"):
                participant = match[side]
                user_id = participant["user_id"]
                stat = data["stats"].setdefault(user_id, {"wins": 0, "losses": 0, "draws": 0, "total": 0})
                outcome = "draws" if not winner_id else "wins" if user_id == winner_id else "losses"
                stat[outcome] += 1
                stat["total"] += 1
                stat.update(nickname=participant["nickname"], last_at=timestamp)
            record = {
                "match_id": match["match_id"], "seed": match["seed"], "completed_at": timestamp,
                "level": match.get("level", 50),
                "max_turns": match.get("max_turns", 50),
                "challenger": deepcopy(match["challenger"]), "target": deepcopy(match["target"]),
                "winner_id": winner_id, "winner": winner, "turns": turns,
                "reason": str(result.get("reason") or "")[:120],
                "final": deepcopy(dict(result.get("final", {}))),
            }
            data["records"].append(record)
            data["records"] = data["records"][-MAX_HISTORY:]
            data["pending"].pop(match["match_id"], None)
            self.store._save()
            return deepcopy(record)

    def abort(self, scope: str, match_id: str) -> bool:
        """模拟或提交失败后释放预留，不产生胜负记录。"""
        scope = self._scope(scope)
        with self.store._transaction():
            data = self._data(scope)
            match = data.get("pending", {}).get(str(match_id))
            if not match or match.get("status") != "running":
                return False
            data["pending"].pop(str(match_id))
            self.store._save()
            return True

    def _withdraw(self, scope: str, user_id: str, side: str, now: float | None) -> dict:
        scope, user_id = self._scope(scope), self._user(user_id)
        with self.store._transaction():
            data = self._data(scope)
            self._prune(data, self._now(now))
            match = next((m for m in data.get("pending", {}).values() if m[side]["user_id"] == user_id), None)
            if not match:
                raise GachaError("没有可以取消或拒绝的有效挑战。")
            if match.get("status") != "waiting":
                raise GachaError("对战已经开始，不能再取消或拒绝。")
            data["pending"].pop(match["match_id"])
            self.store._save()
            return self._public(match)

    def cancel(self, scope: str, user_id: str, *, now: float | None = None) -> dict:
        return self._withdraw(scope, user_id, "challenger", now)

    def reject(self, scope: str, user_id: str, *, now: float | None = None) -> dict:
        return self._withdraw(scope, user_id, "target", now)

    def _snapshot_data(self, scope: str) -> dict:
        snapshot = self.store.snapshot()
        value = snapshot.get("scopes", {}).get(self._scope(scope), {})
        data = value.get("duels", {}) if isinstance(value, Mapping) else {}
        return data if isinstance(data, dict) else {}

    def profile(self, scope: str, user_id: str) -> dict:
        user_id = self._user(user_id)
        data = self._snapshot_data(scope)
        stats = deepcopy(data.get("stats", {}).get(user_id, {}))
        stats = {**{"wins": 0, "losses": 0, "draws": 0, "total": 0, "nickname": user_id}, **stats}
        stats["user_id"] = user_id
        stats["points"] = stats["wins"] * 3 + stats["draws"]
        stats["win_rate"] = round(stats["wins"] / stats["total"] * 100, 1) if stats["total"] else 0.0
        return stats

    def history(self, scope: str, user_id: str = "", *, limit: int = 10) -> list[dict]:
        data = self._snapshot_data(scope)
        records = data.get("records", [])
        if user_id:
            user_id = self._user(user_id)
            records = [record for record in records if user_id in {
                record["challenger"]["user_id"], record["target"]["user_id"],
            }]
        return deepcopy(list(reversed(records))[:max(1, min(20, int(limit)))])

    def ranking(self, scope: str, *, limit: int = 10) -> list[dict]:
        data = self._snapshot_data(scope)
        rows = []
        for user_id, source in data.get("stats", {}).items():
            row = deepcopy(source)
            row["user_id"] = user_id
            row["points"] = row["wins"] * 3 + row["draws"]
            row["win_rate"] = round(row["wins"] / row["total"] * 100, 1) if row["total"] else 0.0
            rows.append(row)
        rows.sort(key=lambda row: (-row["points"], -row["win_rate"], -row["wins"], row["user_id"]))
        return rows[:max(1, min(20, int(limit)))]
