"""AstrBot Dashboard 插件页与 Web API。

设计原则：

* 所有写操作都走 :class:`~gacha_store.GachaStore`，与群聊共用同一把锁和同一份数据；
* 路由统一挂在 ``/<PLUGIN_ID>/`` 下，继承 Dashboard 登录鉴权，不另开端口；
* 前端只调 JSON API，页面本身是零构建的原生 ES module；
* 任何异常都转成带 ``ok:false`` 的 JSON，避免 Dashboard 直接显示 500 栈。
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
from pathlib import Path
from typing import Any, Mapping

from astrbot.api import logger

try:
    from .gacha import ConfigError, GachaRules
    from .gacha_store import today_key
except ImportError:  # 兼容插件以顶层模块加载
    from gacha import ConfigError, GachaRules
    from gacha_store import today_key

try:  # AstrBot 的 Web 层基于 Quart
    from quart import jsonify, request
except ImportError:  # 脱离 AstrBot 环境（测试）时的最小替身
    class _Req:  # pragma: no cover
        args: dict[str, Any] = {}
        json: Any = None

        async def get_json(self, silent: bool = True):
            return None

    def jsonify(payload: Any):  # noqa: D103
        return payload

    request = _Req()

PLUGIN_ID = "astrbot_plugin_pokemon_partner"
MAX_PAGE_SIZE = 100


def _json(payload: Any, status: int = 200):
    try:
        response = jsonify(payload)
        response.status_code = status
        return response
    except Exception:  # jsonify 替身返回普通 dict
        return payload


def _fail(message: str, status: int = 400):
    return _json({"ok": False, "error": message}, status)


def _query(name: str, default: str = "") -> str:
    try:
        value = request.args.get(name, default)
    except Exception:
        return default
    return str(value if value is not None else default).strip()


def _query_int(name: str, default: int, lo: int = 0, hi: int = MAX_PAGE_SIZE) -> int:
    try:
        value = int(_query(name, str(default)) or default)
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


class PokemonPartnerWebUI:
    """注册 Web API；构造失败不应影响聊天功能。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin

    # ---------- 注册 ----------
    def register(self) -> None:
        context = getattr(self.plugin, "context", None)
        register = getattr(context, "register_web_api", None)
        if not callable(register):
            logger.warning("[%s] 当前 AstrBot 不支持 register_web_api，跳过 WebUI", PLUGIN_ID)
            return
        routes = (
            ("/overview", self.api_overview, ["GET"], "宝可梦数据概览"),
            ("/dex", self.api_dex_list, ["GET"], "宝可梦图鉴列表"),
            ("/dex/detail", self.api_dex_detail, ["GET"], "宝可梦图鉴详情"),
            ("/sessions", self.api_sessions, ["GET"], "会话与成员列表"),
            ("/members", self.api_members, ["GET"], "成员宝可梦详情"),
            ("/rules", self.api_rules, ["GET"], "抽卡规则与实际生效值"),
            ("/members/update", self.api_member_update, ["POST"], "修改成员数据"),
            ("/sessions/reset", self.api_session_reset, ["POST"], "重置会话次数"),
            ("/sessions/delete", self.api_session_delete, ["POST"], "删除会话数据"),
            ("/settings", self.api_settings, ["GET"], "读取插件配置"),
            ("/settings/save", self.api_settings_save, ["POST"], "保存插件配置"),
        )
        for path, handler, methods, desc in routes:
            register(f"/{PLUGIN_ID}{path}", handler, methods, desc)

    # ---------- 通用 ----------
    def _service(self):
        service = getattr(self.plugin, "partner_service", None)
        if service is None:
            raise RuntimeError(
                getattr(self.plugin, "_partner_init_error", "")
                or "宝可梦系统未启用（配置 gacha_enabled=false）"
            )
        return service

    def _store(self):
        return self._service().store

    async def _artwork_data_url(self, row: Mapping[str, Any]) -> str:
        """返回详情面板可直接使用的 data URL；无图或读取失败时返回空串。"""
        artwork = getattr(self.plugin, "artwork", None)
        if artwork is None:
            return ""
        try:
            path = artwork.path_for(dict(row))
            if path is None and getattr(artwork, "allow_download", False):
                timeout = min(
                    5.0, max(1.0, float(getattr(artwork, "timeout", 5.0)))
                )
                try:
                    await asyncio.wait_for(
                        artwork.prefetch([dict(row)], limit=1), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.debug("[%s] WebUI 立绘下载超时: %s", PLUGIN_ID, row.get("id"))
                path = artwork.path_for(dict(row))
            if path is None:
                return ""
            data = await asyncio.to_thread(Path(path).read_bytes)
            if not data or len(data) > 8 * 1024 * 1024:
                return ""
            mime = mimetypes.guess_type(Path(path).name)[0] or "image/png"
            if not mime.startswith("image/"):
                return ""
            encoded = base64.b64encode(data).decode("ascii")
            return f"data:{mime};base64,{encoded}"
        except Exception as exc:
            logger.debug("[%s] WebUI 立绘读取失败: %s", PLUGIN_ID, exc)
            return ""

    async def _body(self) -> Mapping[str, Any]:
        try:
            data = await request.get_json(silent=True)
        except Exception:
            data = None
        return data if isinstance(data, Mapping) else {}

    # ---------- 概览 ----------
    async def api_overview(self):
        try:
            service = self._service()
            store = service.store
            counts = service.pool_counts()
            state = store.state
            scopes = state.get("scopes", {}) if isinstance(state, Mapping) else {}
            total_users = 0
            total_owned = 0
            total_shiny = 0
            total_draws = 0
            active_today = 0

            today = today_key()
            for scope_data in scopes.values():
                users = (
                    scope_data.get("users")
                    if isinstance(scope_data, Mapping)
                    else None
                )
                if not isinstance(users, Mapping):
                    continue
                for profile in users.values():
                    if not isinstance(profile, Mapping):
                        continue
                    owned = store._owned(profile)
                    if not owned:
                        continue
                    total_users += 1
                    total_owned += len(owned)
                    total_shiny += sum(1 for v in owned.values() if v[1] > 0)
                    total_draws += max(0, int(profile.get("total_draws") or 0))
                    if store._used(profile, today) > 0:
                        active_today += 1
            rules = store.rules
            return _json(
                {
                    "ok": True,
                    "pool": counts,
                    "rules": {
                        "draws_per_command": rules.draws_per_command,
                        "daily_limit": rules.daily_limit,
                        "ssr_pity_after": rules.ssr_pity_after,
                        "new_pity_after": rules.new_pity_after,
                        "shiny_chance_percent": rules.shiny_chance_percent,
                        "include_alternate_forms": rules.include_alternate_forms,
                        "effective_weights": service.effective_weights(),
                    },
                    "stats": {
                        "trainers": total_users,
                        "collected": total_owned,
                        "shiny": total_shiny,
                        "draws": total_draws,
                        "active_today": active_today,
                        "sessions": len([s for s in scopes if s]),
                    },
                    "health": {
                        "state_path": str(store.state_path),
                        "state_bytes": (
                            store.state_path.stat().st_size
                            if store.state_path.is_file()
                            else 0
                        ),
                        "restored_from_backup": bool(
                            getattr(store, "_restored_from_backup", False)
                        ),
                        "load_failed": bool(getattr(store, "_load_failed", False)),
                    },
                }
            )
        except Exception as exc:
            logger.warning("[%s] overview 失败: %s", PLUGIN_ID, exc)
            return _fail(str(exc), 500)

    # ---------- 图鉴 ----------
    async def api_dex_list(self):
        try:
            db = self.plugin.db
            keyword = _query("q")
            types = _query("type")
            generation = _query("gen")
            rarity = _query("rarity")
            forms = _query("forms") == "1"
            page = _query_int("page", 1, 1, 100000)
            size = _query_int("size", 24, 1, MAX_PAGE_SIZE)
            rules = self._service().store.rules

            rows = []
            for row in db.table("pokemon"):
                if row.get("form_name") and not forms:
                    continue
                if generation and str(row.get("generation") or "") != generation:
                    continue
                if types and types not in (row.get("types") or "").split("/"):
                    continue
                if keyword:
                    haystack = " ".join(
                        str(row.get(key) or "")
                        for key in ("id_raw", "name_zh", "name_en", "name_ja", "form_name")
                    ).lower()
                    if keyword.lower() not in haystack:
                        continue
                parsed = db.first_stats(row)
                bst = int(parsed[1]) if parsed else 0
                item_rarity = rules.rarity_for_bst(bst) if bst else ""
                if rarity and item_rarity != rarity:
                    continue
                rows.append(
                    {
                        "id": row.get("id") or "",
                        "id_raw": row.get("id_raw") or "",
                        "name_zh": row.get("name_zh") or "",
                        "name_en": row.get("name_en") or "",
                        "form_name": row.get("form_name") or "",
                        "generation": row.get("generation") or "",
                        "types": [t for t in (row.get("types") or "").split("/") if t],
                        "category": row.get("category") or "",
                        "bst": bst,
                        "rarity": item_rarity,
                        "abilities": row.get("abilities") or "",
                    }
                )
            rows.sort(key=lambda x: (int(x["id"]) if str(x["id"]).isdigit() else 9999))
            total = len(rows)
            start = (page - 1) * size
            return _json(
                {
                    "ok": True,
                    "total": total,
                    "page": page,
                    "size": size,
                    "items": rows[start : start + size],
                }
            )
        except Exception as exc:
            logger.warning("[%s] dex list 失败: %s", PLUGIN_ID, exc)
            return _fail(str(exc), 500)

    async def api_dex_detail(self):
        try:
            db = self.plugin.db
            keyword = _query("key")
            if not keyword:
                return _fail("缺少 key 参数")
            rows = db.query("pokemon", keyword)
            if not rows:
                return _fail(f"没有查到「{keyword}」", 404)
            row = db.pick_best(rows, keyword)
            parsed = db.first_stats(row)
            stats = dict(parsed[0]) if parsed else {}
            bst = int(parsed[1]) if parsed else 0
            learn = db.learnset(row)
            rules = self._service().store.rules
            artwork = await self._artwork_data_url(row)
            return _json(
                {
                    "ok": True,
                    "row": {
                        key: ("" if row.get(key) is None else row.get(key))
                        for key in row
                        if key not in {"profile", "prototype", "detail", "etymology", "dex_entries"}
                    },
                    "stats": stats,
                    "bst": bst,
                    "rarity": rules.rarity_for_bst(bst) if bst else "",
                    "learnset": {
                        "level_up": learn["level_up"],
                        "machine": [n for n, _ in learn["machine"]],
                        "egg": [n for n, _ in learn["egg"]],
                    },
                    "profile": row.get("profile") or "",
                    "dex_entries": row.get("dex_entries") or "",
                    "artwork": artwork,
                }
            )
        except Exception as exc:
            logger.warning("[%s] dex detail 失败: %s", PLUGIN_ID, exc)
            return _fail(str(exc), 500)

    # ---------- 会话 / 成员 ----------
    async def api_sessions(self):
        try:
            store = self._store()
            scopes = store.state.get("scopes", {})
            out = []
            for scope, data in scopes.items():
                if not isinstance(data, Mapping):
                    continue
                users = data.get("users", {})
                if not isinstance(users, Mapping):
                    continue
                members = 0
                collected = 0
                for profile in users.values():
                    if not isinstance(profile, Mapping):
                        continue
                    owned = store._owned(profile)
                    if owned:
                        members += 1
                        collected += len(owned)
                out.append(
                    {
                        "scope": str(scope),
                        "kind": "group" if str(scope).startswith("group:") else "private",
                        "members": members,
                        "collected": collected,
                    }
                )
            out.sort(key=lambda x: (-x["collected"], x["scope"]))
            return _json({"ok": True, "items": out})
        except Exception as exc:
            return _fail(str(exc), 500)

    async def api_members(self):
        try:
            store = self._store()
            scope = _query("scope")
            keyword = _query("q")
            page = _query_int("page", 1, 1, 100000)
            size = _query_int("size", 20, 1, MAX_PAGE_SIZE)
            scopes = store.state.get("scopes", {})
            if not isinstance(scopes, Mapping):
                return _json({"ok": True, "total": 0, "items": []})

            today = today_key()
            items = []
            for scope_name, data in scopes.items():
                if scope and scope != str(scope_name):
                    continue
                users = data.get("users", {}) if isinstance(data, Mapping) else {}
                if not isinstance(users, Mapping):
                    continue
                for user_id, profile in users.items():
                    if not isinstance(profile, Mapping):
                        continue
                    owned = store._owned(profile)
                    name = str(profile.get("nickname") or user_id)
                    if keyword and keyword.lower() not in f"{name} {user_id}".lower():
                        continue
                    if not owned and not keyword:
                        continue
                    items.append(
                        {
                            "scope": str(scope_name),
                            "user_id": str(user_id),
                            "nickname": name,
                            "collected": len(owned),
                            "shiny": sum(1 for v in owned.values() if v[1] > 0),
                            "dust": max(0, int(profile.get("dust") or 0)),
                            "used_today": store._used(profile, today),
                            "bonus_today": store._bonus(profile, today),
                            "pity_ssr": max(0, int(profile.get("pity_ssr") or 0)),
                            "pity_new": max(0, int(profile.get("pity_new") or 0)),
                            "total_draws": max(0, int(profile.get("total_draws") or 0)),
                        }
                    )
            items.sort(key=lambda x: (-x["collected"], x["nickname"]))
            total = len(items)
            start = (page - 1) * size
            return _json(
                {
                    "ok": True,
                    "total": total,
                    "page": page,
                    "size": size,
                    "items": items[start : start + size],
                }
            )
        except Exception as exc:
            return _fail(str(exc), 500)

    async def api_member_update(self):
        try:
            store = self._store()
            body = await self._body()
            scope = str(body.get("scope") or "").strip()
            user_id = str(body.get("user_id") or "").strip()
            if not scope or not user_id:
                return _fail("缺少 scope 或 user_id")
            field = str(body.get("field") or "")
            action = str(body.get("action") or "set")

            def work() -> dict:
                if action == "add_bonus":
                    amount = max(1, min(100, int(body.get("value") or 1)))
                    bonus = store.add_bonus(scope, user_id, amount)
                    return {"bonus_today": bonus}
                if action == "reset":
                    store.reset_today(scope, user_id)
                    return {}
                if field == "dust":
                    dust = store.set_dust(scope, user_id, int(body.get("value") or 0))
                    return {"dust": dust}
                if field == "nickname":
                    with store._lock:
                        profile = store._profile(scope, user_id)
                        profile["nickname"] = str(body.get("value") or "")[:48]
                        store._save()
                    return {"nickname": str(body.get("value") or "")[:48]}
                if field in {"pity_ssr", "pity_new", "total_draws"}:
                    value = max(0, int(body.get("value") or 0))
                    with store._lock:
                        profile = store._profile(scope, user_id)
                        profile[field] = value
                        store._save()
                    return {field: value}
                raise ValueError(f"不支持的字段或操作: {field or action}")

            result = await asyncio.to_thread(work)
            return _json({"ok": True, "result": result})
        except Exception as exc:
            logger.warning("[%s] member update 失败: %s", PLUGIN_ID, exc)
            return _fail(str(exc))

    async def api_session_reset(self):
        try:
            store = self._store()
            body = await self._body()
            scope = str(body.get("scope") or "").strip()
            if not scope:
                return _fail("缺少 scope")
            changed = await asyncio.to_thread(store.reset_scope_today, scope)
            return _json({"ok": True, "changed": changed})
        except Exception as exc:
            return _fail(str(exc))

    async def api_session_delete(self):
        try:
            store = self._store()
            body = await self._body()
            scope = str(body.get("scope") or "").strip()
            user_id = str(body.get("user_id") or "").strip()
            if not scope:
                return _fail("缺少 scope")
            with store._lock:
                scopes = store.state.get("scopes", {})
                data = scopes.get(scope) if isinstance(scopes, Mapping) else None
                if not isinstance(data, dict):
                    return _json({"ok": True, "removed": 0})
                users = data.get("users", {})
                if not isinstance(users, dict):
                    return _json({"ok": True, "removed": 0})
                if user_id:
                    removed = 1 if users.pop(user_id, None) is not None else 0
                else:
                    removed = len(users)
                    users.clear()
                if not users:
                    scopes.pop(scope, None)
                store._save()
            return _json({"ok": True, "removed": removed})
        except Exception as exc:
            return _fail(str(exc))

    # ---------- 规则 / 设置 ----------
    async def api_rules(self):
        try:
            service = self._service()
            store = service.store
            rules = store.rules
            total = sum(rules.weight_list()) or 1
            effective = service.effective_weights()
            effective_total = sum(effective.values()) or 1
            return _json(
                {
                    "ok": True,
                    "configured": {
                        rarity: {
                            "weight": rules.weights.get(rarity, 0),
                            "percent": round(
                                rules.weights.get(rarity, 0) / total * 100, 2
                            ),
                            "pool": service.pool_counts().get(rarity, 0),
                        }
                        for rarity in ("C", "R", "SR", "SSR")
                    },
                    "effective": {
                        rarity: {
                            "weight": weight,
                            "percent": round(weight / effective_total * 100, 2),
                        }
                        for rarity, weight in effective.items()
                    },
                    "thresholds": {
                        "rare_min_bst": rules.rare_min_bst,
                        "super_rare_min_bst": rules.super_rare_min_bst,
                        "ultra_rare_min_bst": rules.ultra_rare_min_bst,
                    },
                    "economy": {
                        "duplicate_dust": dict(rules.duplicate_dust),
                        "spark_costs": dict(rules.spark_costs),
                        "shiny_chance_percent": rules.shiny_chance_percent,
                        "shiny_dust_multiplier": rules.shiny_dust_multiplier,
                        "shiny_spark_multiplier": rules.shiny_spark_multiplier,
                    },
                    "limits": {
                        "draws_per_command": rules.draws_per_command,
                        "daily_limit": rules.daily_limit,
                        "cooldown_seconds": rules.cooldown_seconds,
                        "ssr_pity_after": rules.ssr_pity_after,
                        "new_pity_after": rules.new_pity_after,
                        "allow_duplicate_draw": rules.allow_duplicate_draw,
                    },
                }
            )
        except Exception as exc:
            return _fail(str(exc), 500)

    async def api_settings(self):
        try:
            config = dict(getattr(self.plugin, "config", {}) or {})
            return _json({"ok": True, "config": config})
        except Exception as exc:
            return _fail(str(exc), 500)

    async def api_settings_save(self):
        try:
            body = await self._body()
            patch = body.get("config")
            if not isinstance(patch, Mapping):
                return _fail("config 必须是对象")
            config = getattr(self.plugin, "config", None)
            if config is None:
                return _fail("插件配置不可写")
            merged = dict(config)
            merged.update(dict(patch))
            # 先校验，避免把插件写到无法启动。
            GachaRules.from_config(merged)
            await asyncio.to_thread(self._apply_settings, merged)
            return _json({"ok": True})
        except ConfigError as exc:
            return _fail(f"配置未通过校验：{exc}")
        except Exception as exc:
            logger.warning("[%s] settings save 失败: %s", PLUGIN_ID, exc)
            return _fail(str(exc))

    def _apply_settings(self, merged: Mapping[str, Any]) -> None:
        config = self.plugin.config
        before = dict(config)
        save = getattr(config, "save_config", None)
        try:
            config.clear()
            config.update(dict(merged))
            if callable(save):
                save()
            # 重新初始化成功后才算保存成功；失败会恢复配置与旧运行实例。
            self.plugin._init_partners()
            if merged.get("gacha_enabled", True) and self.plugin.partner_service is None:
                raise RuntimeError(
                    getattr(self.plugin, "_partner_init_error", "")
                    or "宝可梦系统重新初始化失败"
                )
        except Exception:
            config.clear()
            config.update(before)
            if callable(save):
                try:
                    save()
                except Exception as rollback_exc:
                    logger.error(
                        "[%s] 配置回滚写入失败: %s", PLUGIN_ID, rollback_exc
                    )
            try:
                self.plugin._init_partners()
            except Exception as rollback_exc:
                logger.error(
                    "[%s] 配置回滚后重载失败: %s", PLUGIN_ID, rollback_exc
                )
            raise
