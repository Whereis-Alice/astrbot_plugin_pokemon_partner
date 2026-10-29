"""astrbot_plugin_pokemon_partner — 宝可梦图鉴插件

本地中文图鉴库（pokemon-dataset-zh 生成，https://github.com/42arch/pokemon-dataset-zh）
+ Pillow 图片卡片为主，PokéAPI 官方数据作为补充（可在配置中关闭）。
属性相性优先使用本地 types.tsv 基准属性表。

设计原则：LLM 工具保持原子化（供模型组合调用）；聊天指令只保留粗粒度
功能模块，不镜像每个原子能力（跨表搜索等原子能力仅存在于工具层）。
宝可梦抽取的逻辑在 gacha.py，渲染在 partner_cards.py，入口只做编排。

指令：
  图鉴帮助                 总帮助
 今日宝可梦             每日抽取（数量、概率、保底均可配置）
  我的宝可梦 [页码]        我的收藏
  收集资料                 次数/星尘/保底进度
  兑换宝可梦 <名称> [闪光] 星尘定向兑换
  收集排行                 本群收集排行
  宝可梦 <编号或名称>    图鉴详情（图片卡片，含冠军赛名次）
  宝可梦 <名> 招式表/能力值  图鉴子视图
  特性/招式/道具/性格 <名称> 数据查询
  相性 <属性1> [属性2]   属性相性（相性 打 <属性> = 进攻方向）
  克制 <宝可梦> [B]      克制推荐 / 多维度对比
  对战/队伍分析/筛选     组合分析
  宝冠军 <关键词>         冠军赛一站式查询
  许愿星 <…>             许愿星基拉祈 RNG 筛选
"""

import asyncio
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import ClassVar

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star, register

try:
    from .battle_report import build_battle_pokemon, build_battle_report, format_battle_report, format_battle_report_data, parse_battle_options, parse_battle_max_turns, split_battle_text
    from .battle_engine import (
        NATURES,
        STAT_KEYS,
        BattlePokemon,
        BattleSimulator,
        calc_stats,
        make_move,
    )
    from .card_renderer import CardRenderer
    from .daily_draw_trigger import DailyDrawTrigger
    from .champions_db import (
        OPTION_LABELS,
        ChampionsDB,
        fmt_label,
        source_label,
    )
    from .pokeapi_client import PokeAPIClient
    from .pokedex_db import TABLES, PokedexDB
    from .gacha import ConfigError, GachaError, GachaRules, rarity_label
    from .artwork import ArtworkCache
    from .partner_cards import (
        render_collection_card,
        render_draw_card,
        render_profile_card,
        render_rarity_legend,
    )
    from .partner_service import PartnerService
    from .owned_duels import OwnedDuelService
    from .webui import PokemonPartnerWebUI
    from .wishmaker_card import build_wishmaker_markdown
except ImportError:  # 兼容以普通模块方式加载插件
    from battle_report import build_battle_pokemon, build_battle_report, format_battle_report, format_battle_report_data, parse_battle_options, parse_battle_max_turns, split_battle_text
    from battle_engine import (
        NATURES,
        STAT_KEYS,
        BattlePokemon,
        BattleSimulator,
        calc_stats,
        make_move,
    )
    from card_renderer import CardRenderer
    from daily_draw_trigger import DailyDrawTrigger
    from champions_db import (
        OPTION_LABELS,
        ChampionsDB,
        fmt_label,
        source_label,
    )
    from pokeapi_client import PokeAPIClient
    from pokedex_db import TABLES, PokedexDB
    from gacha import ConfigError, GachaError, GachaRules, rarity_label
    from artwork import ArtworkCache
    from partner_cards import (
        render_collection_card,
        render_draw_card,
        render_profile_card,
        render_rarity_legend,
    )
    from partner_service import PartnerService
    from owned_duels import OwnedDuelService
    from webui import PokemonPartnerWebUI
    from wishmaker_card import build_wishmaker_markdown

PLUGIN_ID = "astrbot_plugin_pokemon_partner"

STAT_COND_RE = re.compile(
    r"^(HP|攻击|防御|特攻|特防|速度|总和)\s*(>=|<=|>|<|=)\s*(\d{1,4})$"
)


def parse_stat_conditions(text: str) -> list[tuple[str, str, int]] | None:
    """解析种族值筛选条件，如 "速度>100 特攻>=120 总和<=500"；全部合法才返回列表。"""
    tokens = [t for t in text.replace("，", " ").replace("、", " ").split() if t]
    if not tokens:
        return None
    conds = []
    for tok in tokens:
        m = STAT_COND_RE.match(tok.strip())
        if not m:
            return None
        conds.append((m.group(1), m.group(2), int(m.group(3))))
    return conds


POKEMON_FIELDS = (
    "id_raw",
    "form_name",
    "name_zh",
    "name_en",
    "generation",
    "types",
    "category",
    "height",
    "weight",
    "abilities",
    "hidden_abilities",
    "stats",
    "weak_to",
    "resist_to",
    "immune_to",
    "evolution",
    "dex_entries",
)

WISHMAKER_NATURES = (
    "勤奋 怕寂寞 勇敢 固执 顽皮 大胆 坦率 悠闲 淘气 乐天 "
    "胆小 急躁 认真 爽朗 天真 内敛 慢吞吞 温和 害羞 马虎 "
    "沉着 温柔 自大 慎重 浮躁"
)

# 性格英文名（与 battle_engine.NATURES 的中文名一一对应，官方译名）
NATURE_EN = {
    "勤奋": "hardy", "怕寂寞": "lonely", "勇敢": "brave", "固执": "adamant",
    "顽皮": "naughty", "大胆": "bold", "坦率": "docile", "悠闲": "relaxed",
    "淘气": "impish", "乐天": "lax", "胆小": "timid", "急躁": "hasty",
    "认真": "serious", "爽朗": "jolly", "天真": "naive", "内敛": "modest",
    "慢吞吞": "mild", "温和": "quiet", "害羞": "bashful", "马虎": "rash",
    "沉着": "calm", "温柔": "gentle", "自大": "sassy", "慎重": "careful",
    "浮躁": "quirky",
}
# 树果口味对应的能力项：性格提升项=喜欢的口味，降低项=讨厌的口味
NATURE_FLAVOR = {"攻击": "辣", "防御": "酸", "速度": "甜", "特攻": "涩", "特防": "苦"}


def nature_effect_text(nature: str) -> str:
    """一行性格说明：能力修正 + 口味偏好，如"固执：攻击+10%、特攻-10%（喜欢辣味、讨厌涩味）"。"""
    pair = NATURES[nature]
    en = NATURE_EN.get(nature, nature)
    if pair is None:
        return f"{nature}（{en}）：无能力修正（中性性格）"
    boost, lower = pair
    like, hate = NATURE_FLAVOR[boost], NATURE_FLAVOR[lower]
    return (
        f"{nature}（{en}）：{boost}+10%、{lower}-10%"
        f"（喜欢{like}味、讨厌{hate}味）"
    )


def nature_table_text() -> str:
    """全部 25 种性格按提升项分组的速查表（保持官方表顺序）。"""
    groups: dict[str, list[str]] = {
        "攻击": [], "防御": [], "速度": [], "特攻": [], "特防": [], "中性": []
    }
    for name, pair in NATURES.items():
        if pair is None:
            groups["中性"].append(name)
        else:
            boost, lower = pair
            groups[boost].append(f"{name}（↓{lower}）")
    lines = ["性格速查（↑=该项能力值 +10%，↓=该项 -10%）："]
    for key in ("攻击", "防御", "速度", "特攻", "特防", "中性"):
        label = "中性（无修正）" if key == "中性" else f"{key}↑"
        lines.append(f"{label}：{'、'.join(groups[key])}")
    return "\n".join(lines)


@register(
    PLUGIN_ID,
    "Whereis-Alice",
    "宝可梦图鉴：抽卡收藏 + 中文图鉴查询 + 对战分析 + 冠军赛数据",
    "v1.3.3",
)
class PokemonPartnerPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig = None):
        super().__init__(context)
        self.config = config if config is not None else {}
        images_dir = self.config.get("images_dir") or ""
        self.db = PokedexDB(Path(__file__).resolve().parent / "data")
        self.renderer = CardRenderer(images_dir or None)
        self.api = PokeAPIClient(timeout=int(self.config.get("pokeapi_timeout", 15)))
        # 冠军赛季数据：只读 champions.db（由独立脚本 champions_fetch.py 抓取）
        self.champions = ChampionsDB(self.config.get("champions_db_path") or None)
        self.partner_service: PartnerService | None = None
        self.artwork: ArtworkCache | None = None
        self._artwork_task: asyncio.Task | None = None
        self._partner_init_error = ""
        self._init_partners()
        self.webui: PokemonPartnerWebUI | None = None
        try:
            self.webui = PokemonPartnerWebUI(self)
            self.webui.register()
        except Exception as exc:
            self.webui = None
            logger.warning(f"[{PLUGIN_ID}] WebUI 注册失败，聊天功能继续运行: {exc}")

    async def terminate(self):
        task = self._artwork_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self.api.close()

    # ---------- 宝可梦系统 ----------
    def _init_partners(self) -> None:
        """构建抽卡服务。配置错误不应阻断图鉴功能。"""
        self._partner_init_error = ""
        if not self.config.get("gacha_enabled", True):
            self.partner_service = None
            self.artwork = None
            return
        try:
            rules = GachaRules.from_config(self.config)
            data_dir = self._partner_state_dir()
            artwork = ArtworkCache(
                data_dir / "artwork",
                images_dir=self.config.get("images_dir") or None,
                db=self.db,
                allow_download=self.config.get("fallback_web_image", True),
                timeout=float(self.config.get("pokeapi_timeout", 15)),
            )
            partner_service = PartnerService.create(
                self.db,
                data_dir / "gacha",
                rules,
                artwork_loader=artwork.load,
            )
        except ConfigError as exc:
            self.partner_service = None
            self.artwork = None
            self._partner_init_error = f"抽卡配置有误，已停用宝可梦系统：{exc}"
            logger.error(f"[{PLUGIN_ID}] {self._partner_init_error}")
        except Exception as exc:
            self.partner_service = None
            self.artwork = None
            self._partner_init_error = f"宝可梦系统初始化失败：{exc}"
            logger.error(f"[{PLUGIN_ID}] 宝可梦系统初始化失败: {exc}")
        else:
            self.artwork = artwork
            self.partner_service = partner_service

    def _partner_state_dir(self) -> Path:
        """数据目录：优先 AstrBot 标准 plugin_data；独立运行时回退到插件目录。"""
        try:
            from astrbot.core.star.star_tools import StarTools

            return StarTools.get_data_dir(PLUGIN_ID)
        except Exception:
            path = Path(__file__).resolve().parent / "data" / "partner_state"
            path.mkdir(parents=True, exist_ok=True)
            return path

    @staticmethod
    def _scope(event: AstrMessageEvent) -> tuple[str, str, str]:
        """返回 ``(scope, user_id, nickname)``。

        群聊按平台会话 ID 隔离，私聊按用户隔离。部分适配器不会填充
        ``message_obj.group_id``；此时必须回退到会话 ID，不能误判成私聊。
        """
        user_id = str(event.get_sender_id() or "")
        session_id = str(event.get_session_id() or "")
        group_id = str(event.get_group_id() or "")
        if not user_id:
            user_id = session_id or "unknown"

        is_private = bool(event.is_private_chat())
        if is_private:
            scope = f"private:{user_id}"
        else:
            group_key = group_id
            if not group_key:
                raw = getattr(getattr(event, "message_obj", None), "raw_message", None)
                for key in ("group_id", "group_openid", "channel_id", "chat_id", "room_id"):
                    value = (
                        raw.get(key)
                        if isinstance(raw, dict)
                        else getattr(raw, key, None)
                    )
                    if value:
                        group_key = str(value)
                        break
            if not group_key:
                group_key = session_id
            if not group_key:
                group_key = str(getattr(event, "unified_msg_origin", "") or "unknown")
            scope = f"group:{group_key}"

        nickname = event.get_sender_name() or ""
        return scope, user_id, nickname

    def _partner_or_error(self) -> PartnerService:
        if self.partner_service is None:
            hint = self._partner_init_error or "宝可梦系统已关闭（配置 gacha_enabled=false）。"
            raise GachaError(hint)
        return self.partner_service

    async def _ensure_artwork(self, rows: list[dict]) -> None:
        """抽卡前补齐立绘，最多等待几秒；失败则卡片用占位图，不影响结果。"""
        if self.artwork is None or not rows:
            return
        try:
            await asyncio.wait_for(
                self.artwork.prefetch(rows, limit=len(rows)),
                timeout=8.0,
            )
        except asyncio.TimeoutError:
            logger.warning(f"[{PLUGIN_ID}] 立绘下载超时，使用现有缓存渲染")
        except Exception as exc:
            logger.warning(f"[{PLUGIN_ID}] 立绘下载失败: {exc}")

    def _card_enabled(self) -> bool:
        """宝可梦卡片是否启用；关闭时全部回退纯文本。"""
        return bool(self.config.get("enable_image_card", True))

    @staticmethod
    def _draw_text(outcome, nickname: str, ssr_pity_after: int | None = None) -> str:
        lines = [f"【今日宝可梦】{nickname}"]
        for index, item in enumerate(outcome.items, 1):
            tags = []
            if item.shiny:
                tags.append("✦闪光")
            if item.is_new:
                tags.append("NEW")
            elif item.dust:
                tags.append(f"+{item.dust}星尘")
            tail = f"（{'、'.join(tags)}）" if tags else ""
            lines.append(
                f"{index}. #{item.dex_raw} {item.name}"
                f"｜{item.rarity}·{item.rarity_name}{tail}"
            )
        if outcome.capped:
            lines.append(
                f"注意：本次请求 {outcome.requested} 次，"
                f"但今日只剩 {len(outcome.items)} 次。"
            )
        lines.append(
            f"今日剩余 {outcome.remaining}/{outcome.daily_limit} 次"
            f"｜星尘 {outcome.dust}"
        )
        if ssr_pity_after and ssr_pity_after > 0:
            lines.append(f"SSR 保底进度 {outcome.pity_ssr}/{ssr_pity_after}")
        elif ssr_pity_after is None and outcome.pity_ssr:
            lines.append(f"SSR 保底进度 {outcome.pity_ssr}")
        for reward in outcome.milestones:
            bonus = f"，额外 {reward.bonus} 次" if reward.bonus else ""
            lines.append(
                f"🎉 收集达到 {reward.threshold} 位：获得 {reward.dust} 星尘{bonus}"
            )
        return "\n".join(lines)

    async def _send_draw_card(self, event, outcome, nickname: str):
        try:
            service = self._partner_or_error()
        except GachaError:
            # The draw was already committed. Disabling the system while its
            # response is pending must still deliver that same result.
            for part in split_battle_text(self._draw_text(outcome, nickname), 1800):
                yield event.plain_result(part)
            return
        notice = service.recovery_notice()
        text = self._draw_text(outcome, nickname, service.store.rules.ssr_pity_after)
        if notice:
            text = f"{notice}\n{text}"
        if not self._card_enabled():
            for part in split_battle_text(text, 1800):
                yield event.plain_result(part)
            return
        # 先把本批立绘取齐再渲染，否则卡片里只会是占位图。
        art_rows = []
        for item in outcome.items:
            if item.shiny:
                art_rows.append(dict(item.row, _artwork_shiny=True))
            art_rows.append(item.row)
        await self._ensure_artwork(art_rows)
        details = []
        try:
            png = await asyncio.to_thread(
                render_draw_card,
                outcome.items,
                nickname=nickname,
                remaining=outcome.remaining,
                daily_limit=outcome.daily_limit,
                dust=outcome.dust,
                pity_ssr=outcome.pity_ssr,
                ssr_pity_after=service.store.rules.ssr_pity_after,
                image_loader=service.artwork,
                style=self.config.get("daily_card_style", "classic"),
                details_out=details,
            )
        except Exception as exc:
            logger.warning(f"[{PLUGIN_ID}] 抽卡卡片渲染失败: {exc}")
            png = None
        if png is None:
            for part in split_battle_text(text, 1800):
                yield event.plain_result(part)
            return
        if any(item.is_new for item in outcome.items):
            text += "\n新宝可梦已加入图鉴。"
        if details:
            text += "\n" + "\n".join(dict.fromkeys(details))
        text += "\n弱点、抗性和免疫按属性计算，特性可能改变实际效果。"
        text += "\n重复宝可梦自动折算星尘；使用“兑换宝可梦 名称”可定向获得。"
        parts = split_battle_text(text, 1799)
        for part in parts[:-1]:
            yield event.plain_result(part)
        yield event.chain_result([Comp.Plain(parts[-1] + "\n"), Comp.Image.fromBytes(png)])

    @filter.command("图鉴帮助", alias={"图鉴菜单", "帮助菜单"})
    async def cmd_partner_help(self, event: AstrMessageEvent):
        """宝可梦系统帮助：抽卡、图鉴、兑换与查询指令总览。"""
        yield event.plain_result(self._partner_help_text())

    @filter.command("今日宝可梦", alias={"抽取宝可梦", "抽宝可梦"})
    async def cmd_partner_draw(self, event: AstrMessageEvent):
        """今日宝可梦：抽取宝可梦。每次数量与稀有度概率由插件配置决定。"""
        text = event.get_message_str().strip()
        if text.startswith("今日宝可梦") and text != "今日宝可梦":
            return
        # 私聊、空唤醒前缀等情况下，command 和 regex 可以同时命中同一事件。
        # 在任何异步操作之前标记；抽取或发卡失败也不能从另一入口重抽。
        dispatch_key = f"{PLUGIN_ID}:daily_draw_dispatched"
        if event.get_extra(dispatch_key, False):
            return
        event.set_extra(dispatch_key, True)
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            outcome = await asyncio.to_thread(
                service.store.draw, scope, user_id, nickname
            )
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] 今日宝可梦抽取失败: {exc}")
            yield event.plain_result("抽取失败，请稍后再试。")
            return
        async for result in self._send_draw_card(event, outcome, nickname or "训练家"):
            yield result

    @filter.custom_filter(DailyDrawTrigger)
    @filter.regex(r"\A今日宝可梦\Z")
    async def cmd_partner_draw_bare(self, event: AstrMessageEvent):
        """直接发送整句今日宝可梦，无需唤醒前缀。"""
        async for result in self.cmd_partner_draw(event):
            yield result

    @filter.command("抽取概率", alias={"抽取规则", "抽卡概率"})
    async def cmd_partner_odds(self, event: AstrMessageEvent):
        """查看稀有度概率、保底与每日次数。"""
        try:
            service = self._partner_or_error()
            png = (
                await asyncio.to_thread(
                    render_rarity_legend,
                    service.store.rules,
                    service.pool_counts(),
                    service.effective_weights(),
                )
                if self._card_enabled()
                else None
            )
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] 宝可梦概率卡渲染失败: {exc}")
            yield event.plain_result(service.rule_text())
            return
        if png is None:
            yield event.plain_result(service.rule_text())
            return
        yield event.chain_result([Comp.Image.fromBytes(png)])

    @filter.command("我的宝可梦", alias={"我的图鉴", "图鉴收藏"})
    async def cmd_partner_collection(self, event: AstrMessageEvent, page: str = "1"):
        """查看已收集宝可梦。我的宝可梦 <页码> 翻页。"""
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            owned = await asyncio.to_thread(service.owned, scope, user_id)
            summary = await asyncio.to_thread(service.profile, scope, user_id, nickname)
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        if not owned:
            yield event.plain_result(
                "你还没有收集到宝可梦。发送「今日宝可梦」开始今天的邂逅吧。"
            )
            return
        page_size = 24
        pages = max(1, (len(owned) + page_size - 1) // page_size)
        try:
            page_no = max(1, min(pages, int(page)))
        except (TypeError, ValueError):
            page_no = 1
        chunk = owned[(page_no - 1) * page_size : page_no * page_size]
        png = None
        if self._card_enabled():
            try:
                png = await asyncio.to_thread(
                    render_collection_card,
                    chunk,
                    nickname=nickname or summary.nickname,
                    page=page_no,
                    page_size=page_size,
                    total=summary.total,
                    collected=summary.collected,
                    image_loader=service.artwork,
                )
            except Exception as exc:
                logger.warning(f"[{PLUGIN_ID}] 图鉴卡渲染失败: {exc}")
        if png is not None:
            yield event.chain_result([Comp.Image.fromBytes(png)])
            return
        lines = [
            f"【我的宝可梦】{nickname or summary.nickname} "
            f"{summary.collected}/{summary.total}（第 {page_no}/{pages} 页）"
        ]
        lines += [
            f"#{item.partner.dex_raw} {item.partner.name}"
            f"｜{item.partner.rarity}"
            + (f"｜闪光×{item.shiny_count}" if item.shiny_count else "")
            for item in chunk
        ]
        lines.append("按全国图鉴编号排列。挑战示例：宝可梦挑战 25 @群友 20。把 25 换成本群已拥有的宝可梦图鉴编号或名字；20 是回合上限，可省略。使用闪光个体时在末尾加“闪光”。")
        yield event.plain_result("\n".join(lines))

    @filter.command("收集资料", alias={"图鉴资料", "我的资料"})
    async def cmd_partner_profile(self, event: AstrMessageEvent):
        """查看收集进度、剩余次数、星尘与保底进度。"""
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            summary = await asyncio.to_thread(
                service.profile, scope, user_id, nickname
            )
            rank = await asyncio.to_thread(service.store.user_rank, scope, user_id)
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        png = None
        if self._card_enabled():
            try:
                png = await asyncio.to_thread(
                    render_profile_card,
                    summary,
                    rank=rank,
                    rules=service.store.rules,
                )
            except Exception as exc:
                logger.warning(f"[{PLUGIN_ID}] 资料卡渲染失败: {exc}")
        if png is not None:
            yield event.chain_result([Comp.Image.fromBytes(png)])
            return
        lines = [
            f"【宝可梦资料】{summary.nickname}",
            f"已收集 {summary.collected}/{summary.total}（{summary.percent:.1f}%）",
            f"闪光宝可梦 {summary.shiny_count} 位｜星尘 {summary.dust}",
            f"今日剩余 {summary.remaining}/{summary.daily_limit} 次",
            f"SSR 保底 {summary.pity_ssr}｜连续未出新 {summary.pity_new}",
            f"本群排名：第 {rank} 名" if rank else "",
        ]
        yield event.plain_result("\n".join(line for line in lines if line))

    @filter.command("兑换宝可梦", alias={"换取宝可梦"})
    async def cmd_partner_exchange(self, event: AstrMessageEvent, keyword: str = ""):
        """用星尘定向兑换未拥有的宝可梦。兑换宝可梦 <名称> [闪光]。"""
        if not keyword:
            yield event.plain_result(
                "用法：兑换宝可梦 <宝可梦名称> [闪光]\n"
                "例如：兑换宝可梦 皮卡丘 / 兑换宝可梦 皮卡丘 闪光\n"
                "重复宝可梦会自动折算星尘；闪光兑换消耗更多星尘。"
            )
            return
        name, shiny, unknown = self._parse_exchange_args(event, keyword)
        if not name:
            hint = f"无法识别的参数：{'、'.join(unknown)}\n" if unknown else ""
            yield event.plain_result(
                hint + "用法：兑换宝可梦 <宝可梦名称> [闪光]"
            )
            return
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            result = await asyncio.to_thread(
                service.store.exchange,
                scope,
                user_id,
                name,
                shiny=shiny,
                nickname=nickname,
            )
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        lines = [
            (
                f"兑换成功：{'✦闪光 ' if result.shiny else ''}"
                f"#{result.partner.dex_raw} {result.partner.name}"
                f"（{result.partner.rarity}·{result.partner.rarity_name}）"
            ),
            f"消耗 {result.cost} 星尘，剩余 {result.dust}",
        ]
        for reward in result.milestones:
            bonus = f"，额外 {reward.bonus} 次" if reward.bonus else ""
            lines.append(
                f"🎉 收集达到 {reward.threshold} 位：获得 {reward.dust} 星尘{bonus}"
            )
        yield event.plain_result("\n".join(lines))

    @staticmethod
    def _parse_exchange_args(
        event: AstrMessageEvent, keyword: str
    ) -> tuple[str, bool, list[str]]:
        """解析兑换参数，返回 ``(名称, 是否闪光, 未识别词)``。

        AstrBot 会按空格把参数切成多个，这里改用完整消息文本：剥掉指令名与
        闪光标记后，其余内容整体作为宝可梦名称，避免名称被截断或词被静默丢弃。
        """
        shiny_words = {"闪光", "异色", "shiny"}
        raw = str(getattr(event, "message_str", "") or "").strip()
        if raw.startswith("/"):
            raw = raw[1:].strip()
        for command in ("兑换宝可梦", "换取宝可梦"):
            if raw.startswith(command):
                raw = raw[len(command) :].strip()
                break
        else:
            raw = keyword.strip()

        shiny = False
        words: list[str] = []
        for token in raw.split():
            if token.lower() in shiny_words:
                shiny = True
            else:
                words.append(token)
        if not words:
            return "", shiny, []
        # 名称本身不含空格；多于一个词通常意味着用户多写了内容，明确报出来。
        return words[0], shiny, words[1:]

    @filter.command("收集排行", alias={"图鉴排行"})
    async def cmd_partner_ranking(self, event: AstrMessageEvent):
        """查看当前会话的宝可梦收集排行。"""
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            rows = await asyncio.to_thread(service.store.leaderboard, scope, 10)
            my_rank = await asyncio.to_thread(service.store.user_rank, scope, user_id)
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        if not rows:
            yield event.plain_result("还没有人收集宝可梦，发送「今日宝可梦」抢下第一名吧。")
            return
        lines = ["【宝可梦收集排行】"]
        medals = {1: "🥇", 2: "🥈", 3: "🥉"}
        for rank, name, collected, shiny in rows:
            prefix = medals.get(rank, f"{rank}.")
            lines.append(f"{prefix} {name}｜{collected} 位｜闪光 {shiny}")
        if my_rank:
            lines.append(f"你的排名：第 {my_rank} 名")
        yield event.plain_result("\n".join(lines))

    @filter.command("重置今日宝可梦", alias={"重置宝可梦次数"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_partner_reset(self, event: AstrMessageEvent):
        """管理员重置自己今天的宝可梦抽取次数。"""
        try:
            service = self._partner_or_error()
            scope, user_id, _nickname = self._scope(event)
            await asyncio.to_thread(service.store.reset_today, scope, user_id)
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result("今天的宝可梦抽取次数已重置。")

    @filter.command("增加宝可梦次数", alias={"增加抽取次数"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_partner_add_bonus(
        self, event: AstrMessageEvent, amount: str = "1"
    ):
        """管理员给自己增加今日次数。增加宝可梦次数 <数量>。"""
        try:
            count = max(1, min(100, int(amount)))
        except (TypeError, ValueError):
            yield event.plain_result("数量需要是 1-100 的整数。")
            return
        try:
            service = self._partner_or_error()
            scope, user_id, _nickname = self._scope(event)
            total = await asyncio.to_thread(
                service.store.add_bonus, scope, user_id, count
            )
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(f"已增加 {count} 次，今日额外次数累计 {total}。")

    @filter.command("重置本群宝可梦", alias={"重置本群次数"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_partner_reset_group(self, event: AstrMessageEvent):
        """管理员重置本群所有人今天的宝可梦抽取次数。"""
        group_id = event.get_group_id()
        if not group_id:
            yield event.plain_result("该指令仅支持群聊。")
            return
        try:
            service = self._partner_or_error()
            changed = await asyncio.to_thread(
                service.store.reset_scope_today, f"group:{group_id}"
            )
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        yield event.plain_result(f"已重置本群 {changed} 位成员的今日次数。")

    @filter.command("下载立绘", alias={"下载全部立绘", "缓存立绘"})
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def cmd_download_artwork(self, event: AstrMessageEvent):
        """管理员一键下载全部宝可梦立绘到本地，之后抽卡更快。

        可重复执行：已存在的会跳过，中断后再发一次即可续传。
        """
        if self.artwork is None:
            yield event.plain_result("立绘缓存未初始化，请检查插件配置后重载。")
            return
        if not self.config.get("fallback_web_image", True):
            yield event.plain_result(
                "配置里关闭了「本地立绘缺失时使用网络官方图」，"
                "请先在设置中开启后再下载。"
            )
            return
        if getattr(self, "_artwork_task", None) is not None:
            yield event.plain_result("已经有一个下载任务在运行，请等它完成。")
            return

        artwork = self.artwork
        rows = list(self.db.table("pokemon"))
        before = artwork.cached_count()
        self._artwork_task = asyncio.create_task(
            self._run_artwork_download(event, rows, artwork)
        )
        yield event.plain_result(
            f"开始下载立绘：共 {len(rows)} 条数据，"
            f"本地已有 {before} 张，缺失的会并发下载。完成前请勿重复执行。"
        )

    async def _run_artwork_download(self, event, rows, artwork) -> None:
        """后台下载，按批次推进度，结束时汇报结果。"""
        started = asyncio.get_running_loop().time()
        try:
            result = await artwork.download_all(
                rows,
                progress=self._artwork_progress(event, started),
                chunk_size=40,
            )
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] 立绘批量下载失败: {exc}")
            await self._notify(event, f"立绘下载失败：{exc}")
            return
        finally:
            self._artwork_task = None

        elapsed = asyncio.get_running_loop().time() - started
        lines = [
            "【立绘下载完成】",
            f"卡池条目：{result['total']}",
            f"本次下载：{result['saved']} 张",
            f"已存在跳过：{result['skipped']} 张",
            f"下载失败：{result['failed']} 张",
            f"本地合计：{artwork.cached_count()} 张",
            f"耗时：{elapsed:.1f} 秒",
        ]
        if result["failed"]:
            lines.append("失败项可再次发送「下载立绘」重试，已存在的会自动跳过。")
        await self._notify(event, "\n".join(lines))

    def _artwork_progress(self, event, started: float):
        """每 200 张或每 15 秒回报一次进度，避免刷屏。"""
        state = {"last": 0.0, "last_count": 0}

        def report(done: int, total: int, saved: int) -> None:
            now = asyncio.get_running_loop().time()
            if done < total and done - state["last_count"] < 200:
                if now - state["last"] < 15:
                    return
            state["last"] = now
            state["last_count"] = done
            asyncio.create_task(
                self._notify(
                    event,
                    f"立绘下载中：{done}/{total}（成功 {saved}，"
                    f"已用 {now - started:.0f} 秒）",
                )
            )

        return report

    async def _notify(self, event, message: str) -> None:
        """主动推送到触发指令的会话。"""
        try:
            await self.context.send_message(
                event.unified_msg_origin, MessageChain().message(message)
            )
        except Exception:
            logger.debug(f"[{PLUGIN_ID}] 无法主动推送下载进度")

    def _partner_help_text(self) -> str:
        base = (
            "🎒 宝可梦图鉴\n"
            "今日宝可梦 — 每日固定次数抽取（数量与概率由配置决定）\n"
            "我的宝可梦 [页码] — 查看我在本群已获得的宝可梦\n"
            "收集资料 — 收集进度、星尘、保底与排名\n"
            "兑换宝可梦 <名称> [闪光] — 用星尘定向获得\n"
            "收集排行 — 本群收集排行\n"
            "抽取概率 — 查看稀有度概率与规则\n"
            "重置今日宝可梦 — 管理员重置自己的次数；重置本群宝可梦 — 重置本群\n"
            "下载立绘 — 管理员一键缓存全部宝可梦图片\n"
            "\n图鉴与对战：\n"
            "宝可梦 <编号/名称> — 图鉴卡片\n"
            "宝可梦 <名称> 招式表 [等级] — 可学招式与推荐配招\n"
            "宝可梦 <名称> 能力值 [等级 个体 性格] — 等级能力值\n"
            "特性 / 招式 / 道具 / 性格 — 数据查询\n"
            "相性 <属性1> [属性2]｜相性 打 <属性> — 属性相性\n"
            "克制 <宝可梦>｜克制 <A> <B> — 克制推荐与对比\n"
            "对战 <A> <B> [等级] [详细] [种子=123] — 招式效果与对战过程\n"
            "宝可梦挑战 <图鉴编号/名字> @群友 [回合] — 用已持有的宝可梦邀请对战\n"
            "接收挑战 <自己的图鉴编号/名字>｜拒绝挑战｜取消挑战 — 群内挑战\n"
            "对战记录 [@群友]｜对战排行 — 收藏对战胜负与群榜\n"
            "队伍分析 <名称...> — 队伍弱点分析\n"
            "筛选 <条件> — 按种族值筛选\n"
            "宝冠军 <关键词> — 冠军赛数据\n"
            "许愿星 <性格> [个体] [TID SID] — 许愿星基拉祈筛选\n"
            "群聊 @我 提问时，模型可自动调用图鉴工具。"
        )
        try:
            service = self.partner_service
            if service:
                base += "\n\n【当前抽卡规则】\n" + service.rule_text()
        except Exception as exc:
            logger.warning(f"[{PLUGIN_ID}] 帮助文案追加抽卡规则失败: {exc}")
        return base

    # ---------- 宝可梦 LLM 工具 ----------
    @filter.llm_tool(name="pokemon_partner_draw")
    async def tool_partner_draw(self, event: AstrMessageEvent):
        """为当前用户抽取今日宝可梦（消耗今日次数），返回本次结果。
        用户明确要求抽卡、抽取宝可梦时使用。不要替用户编造未返回的宝可梦。
        """
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            outcome = await asyncio.to_thread(
                service.store.draw, scope, user_id, nickname
            )
        except GachaError as exc:
            return str(exc)
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] LLM 今日宝可梦抽取失败: {exc}")
            return f"抽取失败：{exc}"
        return self._draw_text(outcome, nickname or "训练家")

    @filter.llm_tool(name="pokemon_partner_profile")
    async def tool_partner_profile(self, event: AstrMessageEvent):
        """查询当前用户的宝可梦收集进度：已收集数、闪光数、星尘、今日剩余次数、
        SSR 保底和连续未出新计数。用户问“我还差多少”“我今天还能抽几次”时使用。
        """
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            summary = await asyncio.to_thread(
                service.profile, scope, user_id, nickname
            )
            rank = await asyncio.to_thread(service.store.user_rank, scope, user_id)
        except GachaError as exc:
            return str(exc)
        lines = [
            f"【{summary.nickname} 的宝可梦资料】",
            f"已收集 {summary.collected}/{summary.total}（{summary.percent:.1f}%）",
            f"闪光宝可梦 {summary.shiny_count}｜星尘 {summary.dust}",
            f"今日剩余 {summary.remaining}/{summary.daily_limit} 次",
            f"SSR 保底 {summary.pity_ssr}｜连续未出新 {summary.pity_new}",
            f"本群排名：第 {rank} 名" if rank else "",
        ]
        return "\n".join(line for line in lines if line)

    @filter.llm_tool(name="pokemon_partner_collection")
    async def tool_partner_collection(
        self, event: AstrMessageEvent, page: int = 1, page_size: int = 20
    ):
        """列出当前用户已收集的宝可梦（分页），用于回答“我有哪些宝可梦”“我有没有皮卡丘”。
        Args:
            page(int): 页码，从 1 开始
            page_size(int): 每页数量（1-40），默认 20
        """
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            owned = await asyncio.to_thread(service.owned, scope, user_id)
        except GachaError as exc:
            return str(exc)
        if not owned:
            return "当前用户还没有收集到任何宝可梦。"
        size = max(1, min(40, int(page_size)))
        pages = max(1, (len(owned) + size - 1) // size)
        page = max(1, min(pages, int(page)))
        chunk = owned[(page - 1) * size : page * size]
        lines = [f"已收集 {len(owned)} 位（第 {page}/{pages} 页）："]
        lines += [
            f"#{item.partner.dex_raw} {item.partner.name}"
            f"（{item.partner.rarity}）"
            + (f" 闪光×{item.shiny_count}" if item.shiny_count else "")
            for item in chunk
        ]
        return "\n".join(lines)

    @filter.llm_tool(name="pokemon_partner_exchange")
    async def tool_partner_exchange(
        self, event: AstrMessageEvent, keyword: str, shiny: bool = False
    ):
        """用星尘兑换指定未拥有宝可梦。用户明确要求兑换时调用，不可用于赠送他人。
        Args:
            keyword(str): 宝可梦中文名、英文名或图鉴编号
            shiny(bool): 是否兑换闪光版本（消耗更多星尘），默认 false
        """
        try:
            service = self._partner_or_error()
            scope, user_id, nickname = self._scope(event)
            result = await asyncio.to_thread(
                service.store.exchange,
                scope,
                user_id,
                keyword,
                shiny=shiny,
                nickname=nickname,
            )
        except GachaError as exc:
            return str(exc)
        return (
            f"兑换成功：{'闪光 ' if result.shiny else ''}#{result.partner.dex_raw} "
            f"{result.partner.name}（{result.partner.rarity}），"
            f"消耗 {result.cost} 星尘，剩余 {result.dust}。"
        )

    @filter.llm_tool(name="pokemon_partner_rates")
    async def tool_partner_rates(self, event: AstrMessageEvent):
        """查询当前抽卡规则：稀有度概率、每次数量、每日次数、保底和闪光概率。
        用户问“概率是多少”“保底多少次”时使用。
        """
        try:
            service = self._partner_or_error()
        except GachaError as exc:
            return str(exc)
        counts = service.pool_counts()
        rules = service.store.rules
        weights = service.effective_weights()
        total = sum(weights.values()) or 1
        lines = ["【今日宝可梦规则】"]
        for rarity in ("SSR", "SR", "R", "C"):
            pct = weights.get(rarity, 0) / total * 100
            lines.append(
                f"{rarity}（{rarity_label(rarity)}）：{pct:g}%，池内 {counts.get(rarity, 0)} 位"
            )
        lines.append(f"每次召唤 {rules.draws_per_command} 位，每日 {rules.daily_limit} 次")
        lines.append(
            f"SSR 保底：{rules.ssr_pity_after} 次"
            if rules.ssr_pity_after and counts.get("SSR")
            else "SSR 保底：未启用（已关闭或卡池无 SSR）"
        )
        lines.append(f"闪光概率：{rules.shiny_chance_percent:g}%")
        return "\n".join(lines)

    # ---------- 内部工具 ----------
    async def _artwork_bytes(self, row: dict) -> bytes | None:
        """本地立绘缺失时按配置回退到 PokéAPI 官方图。

        网络官方图只有默认形态，形态行不用它，避免张冠李戴。
        """
        if self.renderer.images_dir and self.db.find_image(
            row, self.renderer.images_dir
        ):
            return None  # 渲染器可直接读本地文件
        if row.get("form_name") or not self.config.get("fallback_web_image", True):
            return None
        try:
            url = (
                f"https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
                f"pokemon/other/official-artwork/{int(row.get('id', 0))}.png"
            )
            return await self.api.fetch_bytes(url)
        except Exception as e:
            logger.warning(f"网络立绘获取失败: {e}")
        return None

    def _champ_card_info(self, row: dict) -> dict | None:
        """图鉴卡片头部的冠军赛名次条数据；无数据/形态行返回 None。"""
        if not self.champions.exists or row.get("form_name"):
            return None
        ranks = self.champions.pokemon_ranks(row.get("name_zh", ""))
        if not ranks:
            return None
        return {
            "season": ranks[0]["season"],
            "total": max(r["total"] for r in ranks),
            "single": next((r["rank"] for r in ranks if r["format"] == "single"), None),
            "double": next((r["rank"] for r in ranks if r["format"] == "double"), None),
        }

    async def _send_pokemon(
        self, event: AstrMessageEvent, row: dict, card: bool, hint: str = ""
    ):
        text = self.db.format_row(row, fields=POKEMON_FIELDS) + hint
        if card and self.config.get("enable_image_card", True):
            img_bytes = await self._artwork_bytes(row)
            # render_pokemon 内部已把同步渲染放进线程池。
            png = await self.renderer.render_pokemon(
                row,
                self.db,
                img_bytes,
                champion=self._champ_card_info(row),
            )
            if png:
                comps = [Comp.Image.fromBytes(png)]
                if hint:
                    comps.append(Comp.Plain(hint))
                yield event.chain_result(comps)
                return
            logger.warning("卡片渲染失败，回退文本")
        yield event.plain_result(text)

    def _pokemon_related_hint(self, row: dict) -> str:
        """图鉴卡片尾部的关联指令反查提示（冠军赛名次 + 常用后续查询）。"""
        name = row.get("name_zh", "")
        if not name:
            return ""
        parts = []
        champ = self._champ_rank_short(name)
        if champ:
            parts.append(f"冠军赛 {champ}（宝冠军 {name} 看配置）")
        if not row.get("form_name"):
            parts.append(f"宝可梦 {name} 招式表")
            parts.append(f"宝可梦 {name} 能力值 50")
        parts.append(f"克制 {name}")
        return "\n———\n相关：" + "｜".join(parts)

    # ---------- 指令 ----------
    @filter.command("宝可梦")
    async def cmd_pokemon(self, event: AstrMessageEvent, keyword: str = ""):
        """宝可梦图鉴卡；宝可梦 <名> 招式表 [等级]｜宝可梦 <名> 能力值 [等级 个体 性格]"""
        tokens = (keyword or "").split()
        if tokens and tokens[0] in {"招式表", "能力值"}:
            yield event.plain_result(
                "用法：宝可梦 <名称> 招式表 [等级]\n"
                "　　宝可梦 <名称> 能力值 [等级 个体 性格]\n"
                "如：宝可梦 皮卡丘 招式表 50 / 宝可梦 皮卡丘 能力值 50 31 固执"
            )
            return
        name = tokens[0] if tokens else ""
        if not name:
            yield event.plain_result(self._help_text())
            return
        sub = tokens[1] if len(tokens) > 1 else ""
        if sub == "招式表":
            level = tokens[2] if len(tokens) > 2 else ""
            async for r in self._learnset_view(event, name, level):
                yield r
            return
        if sub == "能力值":
            level = tokens[2] if len(tokens) > 2 else "50"
            iv = tokens[3] if len(tokens) > 3 else "31"
            nature = tokens[4] if len(tokens) > 4 else ""
            async for r in self._stats_view(event, name, level, iv, nature):
                yield r
            return
        rows = self.db.query("pokemon", name)
        if not rows:
            yield event.plain_result(f"没有查到「{keyword}」的相关信息")
            return
        row = self.db.pick_best(rows, keyword)
        others = sorted(
            {r["form_name"] for r in rows if r.get("form_name") and r is not row}
        )
        hint = self._pokemon_related_hint(row)
        if others:
            hint += (
                f"\n\n———\n还匹配到其他形态：{'、'.join(others)}\n"
                f"发送「宝可梦 {others[0]}」查看对应形态"
            )
        async for r in self._send_pokemon(event, row, card=True, hint=hint):
            yield r

    @filter.command("特性")
    async def cmd_ability(self, event: AstrMessageEvent, keyword: str = ""):
        """查询特性（效果与持有宝可梦）"""
        if not keyword:
            yield event.plain_result("用法：特性 <名称>，如：特性 威吓")
            return
        rows = self.db.query("abilities", keyword) or self.db.query("ability", keyword)
        if not rows:
            yield event.plain_result(f"没有查到特性「{keyword}」的相关信息")
            return
        row = rows[0]
        text = (
            f"特性 {row.get('name_zh', '')}（{row.get('name_en', '')}）\n"
            f"介绍：{row.get('introduction', '')}\n"
            f"效果：{row.get('effect', '')[:400]}\n"
            f"生效条件：{row.get('basic_info', '')}\n"
            f"持有宝可梦：{(row.get('holders', '') or '')[:300]}"
        )
        yield event.plain_result(text)

    @filter.command("招式")
    async def cmd_move(self, event: AstrMessageEvent, keyword: str = ""):
        """查询招式（总表优先，详情补充）"""
        if not keyword:
            yield event.plain_result("用法：招式 <名称>，如：招式 十万伏特")
            return
        rows = self.db.query("move", keyword)
        if not rows:
            detail = self.db.query("moves", keyword)
            if detail:
                rows = [
                    {
                        k: detail[0].get(k, "")
                        for k in (
                            "id",
                            "name_zh",
                            "name_en",
                            "type",
                            "category",
                            "power",
                            "accuracy",
                            "pp",
                            "range",
                            "description",
                            "additional_effect",
                        )
                    }
                ]
        if not rows:
            yield event.plain_result(f"没有查到招式「{keyword}」的相关信息")
            return
        r = rows[0]
        text = (
            f"招式 {r.get('name_zh', '')}（{r.get('name_en', '')}）\n"
            f"{r.get('type', '')}属性·{r.get('category', '')} | 威力 {r.get('power', '')} | "
            f"命中 {r.get('accuracy', '')} | PP {r.get('pp', '')}\n"
            f"说明：{r.get('description', '')}"
        )
        if r.get("additional_effect"):
            text += f"\n追加效果：{r['additional_effect'][:200]}"
        yield event.plain_result(text)

    @filter.command("道具")
    async def cmd_item(self, event: AstrMessageEvent, keyword: str = ""):
        """查询道具"""
        if not keyword:
            yield event.plain_result("用法：道具 <名称>，如：道具 除虫喷雾")
            return
        rows = self.db.query("item", keyword)
        if not rows:
            yield event.plain_result(f"没有查到道具「{keyword}」的相关信息")
            return
        r = rows[0]
        yield event.plain_result(
            f"道具 {r.get('name_zh', '')}（{r.get('name_en', '')}）\n"
            f"类别：{r.get('category_path', '')}\n说明：{r.get('description', '')}"
        )

    @filter.command("性格")
    async def cmd_nature(self, event: AstrMessageEvent, keyword: str = ""):
        """性格速查：性格 [名称]"""
        keyword = (keyword or "").strip()
        if not keyword:
            yield event.plain_result(
                nature_table_text() + "\n性格 <名称> 查看单个性格详情"
            )
            return
        if keyword in NATURES:
            yield event.plain_result(nature_effect_text(keyword))
            return
        hits = [n for n in NATURES if keyword in n]
        if len(hits) == 1:
            yield event.plain_result(nature_effect_text(hits[0]))
            return
        hint = f"，相近的有：{'、'.join(hits)}" if hits else ""
        yield event.plain_result(
            f"没有性格「{keyword}」{hint}\n发送「性格」查看全部 25 种性格"
        )

    @staticmethod
    def _ability_correction_text(result: dict) -> str:
        """把 matchups 的 ability_corrections 压成一行提示，无修正返回空串。"""
        parts = []
        for c in result.get("ability_corrections", []):
            abil = "/".join(c["abilities"])
            cur = "" if c["current_mult"] == 1 else f"（现×{c['current_mult']:g}）"
            parts.append(f"持「{abil}」免疫 {c['type']}{cur}")
        return "特性修正：" + "；".join(parts) if parts else ""

    @filter.command("相性")
    async def cmd_matchups(
        self, event: AstrMessageEvent, type1: str = "", type2: str = ""
    ):
        """属性相性（本地属性表）：相性 火 飞行 = 防守；相性 打 火 = 进攻"""
        offense = False
        if type1 in {"打", "进攻", "攻击"}:  # 相性 打 火
            offense = True
            type1, type2 = type2, ""
        if not type1:
            yield event.plain_result(
                "用法：相性 <属性1> [属性2] = 防守方向；相性 打 <属性> = 进攻方向\n"
                "如：相性 火 飞行 / 相性 打 火"
            )
            return
        if offense:
            async for r in self._offense_text(event, type1):
                yield r
            return
        result = self.db.matchups([t for t in (type1, type2) if t])
        if result is None:
            yield event.plain_result(
                f"没有查到属性「{type1}{'/' + type2 if type2 else ''}」"
            )
            return
        combo = "/".join(x for x in (type1, type2) if x)
        lines = [f"【{combo}】属性相性"]
        lines += (
            ["弱点：" + "、".join(result["weak"])] if result["weak"] else ["弱点：无"]
        )
        lines += (
            ["抗性：" + "、".join(result["resist"])]
            if result["resist"]
            else ["抗性：无"]
        )
        lines += ["免疫：" + "、".join(result["immune"])] if result["immune"] else []
        corr = self._ability_correction_text(result)
        if corr:
            lines.append(corr)
        yield event.plain_result("\n".join(lines))

    async def _offense_text(self, event: AstrMessageEvent, type_name: str):
        """进攻方向相性（原「克制」指令体）。"""
        result = self.db.attack_matchups(type_name)
        if result is None:
            yield event.plain_result(f"没有查到属性「{type_name}」")
            return
        lines = [f"【{type_name}属性攻击】"]
        lines.append("效果拔群：" + ("、".join(result["super"]) or "无"))
        lines.append("收效甚微：" + ("、".join(result["weak"]) or "无"))
        if result["none"]:
            lines.append("无效：" + "、".join(result["none"]))
        yield event.plain_result("\n".join(lines))

    # ---------- Phase 1：能力值 / 进攻相性 / 对比 / 怎么打 ----------
    def _stats_base(self, row: dict) -> dict | None:
        """从种族值文本解析 {能力项: 种族值}。"""
        parsed = self.db.first_stats(row)
        return dict(parsed[0]) if parsed else None

    def _calc_stats_text(self, row: dict, level: int, iv: int, nature: str) -> str:
        base = self._stats_base(row)
        if not base:
            return "该宝可梦缺少种族值数据，无法计算"
        stats = calc_stats(base, level, iv=iv, nature=nature or None)
        head = (
            f"{row.get('name_zh', '')}（{row.get('types', '')}）Lv.{level}"
            f"｜个体 {iv}、努力 0" + (f"、性格 {nature}" if nature else "")
        )
        text = (
            head
            + "\n"
            + "、".join(f"{k} {stats[k]}" for k in STAT_KEYS)
            + f"\n总和 {stats['总和']}"
        )
        if nature:
            text += f"\n{nature_effect_text(nature)}"
        return text

    @staticmethod
    def _parse_clamped_int(text: str, default: int, lo: int, hi: int) -> int:
        try:
            return max(lo, min(hi, int(text)))
        except (TypeError, ValueError):
            return default

    async def _stats_view(
        self, event: AstrMessageEvent, name: str, level: str, iv: str, nature: str
    ):
        """「宝可梦 <名> 能力值 …」子视图：等级能力值计算。"""
        row = self.db.pokemon(name)
        if not row:
            yield event.plain_result(f"没有查到「{name}」的相关信息")
            return
        if nature and nature not in NATURES:
            yield event.plain_result(
                f"没有性格「{nature}」，发送「性格」可查看全部性格速查"
            )
            return
        level_i = self._parse_clamped_int(level, 50, 1, 100)
        iv_i = self._parse_clamped_int(iv, 31, 0, 31)
        yield event.plain_result(self._calc_stats_text(row, level_i, iv_i, nature))

    @staticmethod
    def _stat_compare_line(label: str, v1: int, v2: int, n1: str, n2: str) -> str:
        if v1 > v2:
            win = n1
        elif v2 > v1:
            win = n2
        else:
            win = "平"
        return f"{label} {v1} vs {v2} → {win}"

    def _compare_text(self, row1: dict, row2: dict) -> str:
        n1, n2 = row1.get("name_zh", ""), row2.get("name_zh", "")
        parsed1, parsed2 = self.db.first_stats(row1), self.db.first_stats(row2)
        if not parsed1 or not parsed2:
            return "缺少种族值数据，无法对比"
        t1 = [t for t in (row1.get("types") or "").split("/") if t]
        t2 = [t for t in (row2.get("types") or "").split("/") if t]
        m12 = self.db.matchup_multiplier(t1, t2)
        m21 = self.db.matchup_multiplier(t2, t1)
        lines = [
            f"【克制对比：{n1} vs {n2}】",
            f"属性：{row1.get('types', '')} vs {row2.get('types', '')}",
        ]
        for (label, v1), (_, v2) in zip(parsed1[0], parsed2[0]):
            lines.append(self._stat_compare_line(label, v1, v2, n1, n2))
        lines.append(self._stat_compare_line("总和", parsed1[1], parsed2[1], n1, n2))
        lines.append(f"互克：{n1} 打 {n2} ×{m12:g}｜{n2} 打 {n1} ×{m21:g}")
        ab1 = row1.get("abilities", "") or ""
        ab2 = row2.get("abilities", "") or ""
        lines.append(f"特性：{n1} {ab1}｜{n2} {ab2}")
        lines.append(self._compare_verdict(n1, n2, parsed1[1], parsed2[1], m12, m21))
        return "\n".join(lines)

    @staticmethod
    def _compare_verdict(
        n1: str, n2: str, total1: int, total2: int, m12: float, m21: float
    ) -> str:
        """多维度小结：面板强弱 + 属性克制关系（统一视角，不分攻防）。"""
        if m12 >= 2 and m21 <= 1:
            rel = f"{n1} 属性克制 {n2}"
        elif m21 >= 2 and m12 <= 1:
            rel = f"{n2} 属性克制 {n1}"
        elif m12 >= 2 and m21 >= 2:
            rel = "双方互为效果拔群（对攻）"
        else:
            rel = "属性互不克制"
        if total1 == total2:
            panel = "面板持平"
        else:
            win = n1 if total1 > total2 else n2
            panel = f"{win} 面板更高（{max(total1, total2)} vs {min(total1, total2)}）"
        return f"小结：{panel}，{rel}"

    @filter.command("克制")
    async def cmd_counter(
        self, event: AstrMessageEvent, name1: str = "", name2: str = ""
    ):
        """克制关系：克制 <宝可梦> = 推荐克制它的；克制 <A> <B> = 多维度对比"""
        if not name1:
            yield event.plain_result(
                "用法：克制 <宝可梦> = 推荐克制它的候选\n"
                "　　克制 <A> <B> = 两只多维度对比（面板/属性互克/特性）\n"
                "如：克制 妙蛙花 / 克制 皮卡丘 喷火龙"
            )
            return
        if not name2:
            result = self.db.find_counters(name1)
            if not result:
                yield event.plain_result(
                    f"没有查到「{name1}」的相关信息（查属性相性用 相性 <属性>）"
                )
                return
            yield event.plain_result(self._counters_text(result))
            return
        row1, row2 = self.db.pokemon(name1), self.db.pokemon(name2)
        if not row1 or not row2:
            miss = name1 if not row1 else name2
            yield event.plain_result(f"没有查到「{miss}」的相关信息")
            return
        yield event.plain_result(self._compare_text(row1, row2))

    @staticmethod
    def _counters_text(result: dict) -> str:
        """「克制 X」文本：双向验证的克制候选。"""
        lines = [
            f"【克制 {result['target']}（{result['target_types']}）】",
            "筛选条件：打它 ≥2× 且它打你 ≤1×，按打击倍率与种族值排序",
        ]
        for i, c in enumerate(result["counters"], 1):
            lines.append(
                f"{i}. {c['name']}（{c['types']}）"
                f"打它×{c['atk_mult']:g}｜它打你×{c['def_mult']:g}｜种族{c['total']}"
            )
        if len(lines) == 2:
            lines.append("没有同时满足条件的宝可梦，可放宽要求参考属性相性")
        return "\n".join(lines)

    # ---------- Phase 2：招式表 / 配招 ----------
    def _learnset_text(self, row: dict, level: int | None) -> str:
        learn = self.db.learnset(row)
        name = row.get("name_zh", "")
        lines = [f"【{name} 可学招式】" + (f"（等级≤{level}）" if level else "")]
        ups = learn["level_up"]
        if level:
            ups = [(n, lv) for n, lv in ups if lv is None or lv <= level]
        if ups:
            body = "、".join(f"{n}({lv})" if lv is not None else n for n, lv in ups)
            lines.append(f"升级（{len(ups)}）：{body}")
        if learn["machine"]:
            lines.append(
                f"招式学习器（{len(learn['machine'])}）："
                + "、".join(n for n, _ in learn["machine"])
            )
        if learn["egg"]:
            lines.append(
                f"遗传（{len(learn['egg'])}）：" + "、".join(n for n, _ in learn["egg"])
            )
        if not (ups or learn["machine"] or learn["egg"]):
            lines.append("未收录学习招式表")
        rec, from_learnset = self.db.recommended_moveset(row)
        tag = "" if from_learnset else "（演示配招：学习表未收录，按属性推荐）"
        lines.append(f"推荐配招：{'、'.join(rec)}{tag}")
        return "\n".join(lines)

    async def _learnset_view(self, event: AstrMessageEvent, name: str, level: str):
        """「宝可梦 <名> 招式表 …」子视图：可学招式与推荐配招。"""
        row = self.db.pokemon(name)
        if not row:
            yield event.plain_result(f"没有查到「{name}」的相关信息")
            return
        level_i = self._parse_clamped_int(level, 0, 0, 100) or None
        yield event.plain_result(self._learnset_text(row, level_i))

    # ---------- Phase 3：对战模拟 ----------
    def _battle_pokemon(self, row: dict, level: int) -> BattlePokemon:
        return build_battle_pokemon(self.db, row, level)

    def _champ_ranks_line(self, rows: list[dict]) -> str:
        """ "冠军赛排名（M-7）：A 单#1/双#1｜B 单#7/双#5"，无数据返回空串。"""
        if not self.champions.exists:
            return ""
        season = self.champions.latest_season("single")
        if not season:
            return ""
        parts = []
        for row in rows:
            name = row.get("name_zh", "")
            if not name:
                continue
            ranks = {
                r["format"]: r["rank"]
                for r in self.champions.pokemon_ranks(name, season)
            }
            s = f"单#{ranks['single']}" if ranks.get("single") else "单—"
            d = f"双#{ranks['double']}" if ranks.get("double") else "双—"
            parts.append(f"{name} {s}/{d}")
        if not parts:
            return ""
        return f"冠军赛排名（{season}）：" + "｜".join(parts)

    def _battle_text(self, row1: dict, row2: dict, level: int, *, seed: int | None = None, detailed: bool = False) -> str:
        return format_battle_report(self.db, row1, row2, level, seed=seed, detailed=detailed,
                                    champion_line=self._champ_ranks_line([row1, row2]))

    def _battle_output_mode(self, raw: str) -> str:
        """配置决定默认方式，命令末尾的方式关键词只覆盖本次对战。"""
        modes = {
            "卡片": "card", "图片转发": "card_forward", "卡片转发": "card_forward",
            "转发": "forward", "合并转发": "forward", "文本": "text",
        }
        choices = re.findall(r"(?:^|\s)(图片转发|卡片转发|卡片|合并转发|转发|文本)(?=\s|$)", raw)
        configured = str(self.config.get("battle_output_mode", "card"))
        mode = modes[choices[-1]] if choices else configured
        if mode not in {"card", "card_forward", "forward", "text"}:
            mode = "card"
        return "text" if mode in {"card", "card_forward"} and not self._card_enabled() else mode

    @staticmethod
    def _battle_forward_blocks(report: str) -> list[str]:
        """概况、双方配招、结果与每个回合分别作为转发节点，保留全部原文。"""
        prefix, separator, turns = report.partition("\n逐回合战报：\n")
        sections = prefix.split("\n\n")
        if separator:
            sections.extend(re.split(r"\n(?=第\d+回合：)", turns))
        return [part for section in sections if section.strip() for part in split_battle_text(section, 1600)]

    async def _battle_card_pages(self, battle: dict) -> list[bytes]:
        """立绘预取与渲染分离，关闭抽卡时也能独立生成对战卡片。"""
        try:
            from .battle_cards import render_battle_cards
        except ImportError:
            from battle_cards import render_battle_cards

        artwork = self.artwork
        if artwork is None:
            artwork = ArtworkCache(
                self._partner_state_dir() / "artwork", db=self.db,
                images_dir=getattr(self.renderer, "images_dir", None),
                allow_download=bool(self.config.get("fallback_web_image", True)),
                timeout=float(self.config.get("pokeapi_timeout", 15)),
            )
        rows = [dict(battle["row1"]), dict(battle["row2"])]
        owned_items = battle.get("owned_items") or []
        for index, row in enumerate(rows):
            row.pop("_artwork_shiny", None)
            if index < len(owned_items):
                item = owned_items[index]
                if bool(item.get("shiny") if isinstance(item, dict) else getattr(item, "shiny", False)):
                    row["_artwork_shiny"] = True
        try:
            await asyncio.wait_for(artwork.prefetch(rows, limit=2), timeout=8.0)
        except Exception as exc:
            logger.warning(f"[{PLUGIN_ID}] 对战立绘预取失败，使用现有素材: {exc}")

        def load(row: dict):
            # 默认形态缓存不能冒充 Mega/地区形态的立绘。
            if row.get("form_name"):
                if not artwork.images_dir:
                    return None
                shiny = row.get("_artwork_shiny") is True
                exact = self.db.find_image(row, artwork.images_dir, shiny=True) if shiny else self.db.find_image(row, artwork.images_dir)
                if not exact and shiny:
                    normal_row = dict(row)
                    normal_row.pop("_artwork_shiny", None)
                    exact = self.db.find_image(normal_row, artwork.images_dir)
                if not exact:
                    return None
            return artwork.load(row)

        return await asyncio.to_thread(render_battle_cards, battle, image_loader=load)

    async def _send_battle_report(self, event: AstrMessageEvent, battle: dict, *, mode: str | None = None) -> AsyncIterator[object]:
        """同一对局生成战报；图片转发失败复用普通图片，图片失败保留全回合文本。"""
        mode = mode or self._battle_output_mode(str(getattr(event, "message_str", "") or ""))
        image_mode = mode in {"card", "card_forward"}
        if image_mode and not self._card_enabled():
            mode = "text"
        report = format_battle_report_data({**battle, "detailed": True} if image_mode else battle)
        notice = ""
        if mode in {"card", "card_forward"}:
            try:
                pages = await self._battle_card_pages(battle)
                if not pages:
                    raise ValueError("卡片渲染未返回页面")
                # 先准备全部图片和普通发送结果；转发失败可直接复用，不重新渲染或模拟。
                images = [Comp.Image.fromBytes(page) for page in pages]
                results = [event.chain_result([image]) for image in images]
            except Exception as exc:
                logger.warning(f"[{PLUGIN_ID}] 对战卡片生成失败，回退文本: {exc}")
                notice = "图片暂时无法生成，已改为文本战报。\n"
            else:
                if mode == "card_forward":
                    if event.get_platform_name() == "aiocqhttp":
                        try:
                            bot_id = str(event.get_self_id() or event.get_sender_id() or "0")
                            titles = ("对战结果", "双方配招与效果", "对战过程")
                            nodes = [
                                Comp.Node(content=[image], uin=bot_id,
                                          name=f"宝可梦对战 · {titles[index] if index < len(titles) else '战报'}")
                                for index, image in enumerate(images)
                            ]
                            chain = MessageChain()
                            chain.chain.append(Comp.Nodes(nodes=nodes))
                            await event.send(chain)
                        except Exception as exc:
                            logger.warning(f"[{PLUGIN_ID}] 图片合并转发失败，改为普通图片: {exc}")
                            notice = "图片合并转发暂时无法发送，已改为普通图片。\n"
                        else:
                            return
                    else:
                        notice = "当前平台不支持图片合并转发，已改为普通图片。\n"
                    try:
                        # 提示随第一张图片发送，不额外增加一条消息。
                        results[0] = event.chain_result([Comp.Plain(notice), images[0]])
                    except Exception as exc:
                        logger.warning(f"[{PLUGIN_ID}] 普通图片结果构造失败，回退文本: {exc}")
                        notice = "图片暂时无法发送，已改为文本战报。\n"
                        results = []
                if results:
                    for result in results:
                        yield result
                    return
        elif mode == "forward":
            platform_name = event.get_platform_name()
            if platform_name == "aiocqhttp":
                try:
                    bot_id = str(event.get_self_id() or event.get_sender_id() or "0")
                    nodes = [
                        Comp.Node(content=[Comp.Plain(block)], uin=bot_id, name="宝可梦对战")
                        for block in self._battle_forward_blocks(report)
                    ]
                    chain = MessageChain()
                    chain.chain.append(Comp.Nodes(nodes=nodes))
                    await event.send(chain)
                except Exception as exc:
                    logger.warning(f"[{PLUGIN_ID}] 对战合并转发失败，回退文本: {exc}")
                    notice = "合并转发暂时无法发送，以下为文本战报。\n"
                else:
                    return
            else:
                notice = "当前平台不支持合并转发，以下为文本战报。\n"
        for part in split_battle_text(notice + report):
            yield event.plain_result(part)

    @filter.command("对战")
    async def cmd_battle(
        self,
        event: AstrMessageEvent,
        name1: str = "",
        name2: str = "",
        level: str = "50",
    ):
        """模拟对战；可追加「详细 种子=123 回合=20」及「卡片/图片转发/转发/文本」。"""
        if not name1 or not name2:
            yield event.plain_result(
                "用法：对战 <名称1> <名称2> [等级] [详细] [种子=123] [回合=20] [卡片/图片转发/转发/文本]，如：对战 皮卡丘 喷火龙 50 详细 图片转发"
            )
            return
        row1, row2 = self.db.pokemon(name1), self.db.pokemon(name2)
        if not row1 or not row2:
            miss = name1 if not row1 else name2
            yield event.plain_result(f"没有查到「{miss}」的相关信息")
            return
        level_i = self._parse_clamped_int(level, 50, 1, 100)
        raw = str(getattr(event, "message_str", "") or "")
        detailed, seed = parse_battle_options(raw)
        try:
            max_turns = parse_battle_max_turns(raw)
        except ValueError as exc:
            yield event.plain_result(str(exc))
            return
        battle = await asyncio.to_thread(
            build_battle_report, self.db, row1, row2, level_i, seed=seed, detailed=detailed,
            champion_line=self._champ_ranks_line([row1, row2]), max_turns=max_turns,
        )
        async for result in self._send_battle_report(event, battle, mode=self._battle_output_mode(raw)):
            yield result

    # ---------- 已收藏宝可梦的群内对战 ----------
    def _owned_duel_context(self, event: AstrMessageEvent):
        if event.is_private_chat():
            raise GachaError("收藏对战只能在群聊中进行，请在群里使用。")
        service = self._partner_or_error()
        scope, user_id, nickname = self._scope(event)
        return OwnedDuelService(service.store), scope, user_id, nickname

    @staticmethod
    def _duel_mentions(event: AstrMessageEvent) -> list[tuple[str, str]]:
        """只取真实 @ 组件，忽略对机器人的 @，避免昵称和序号混淆。"""
        bot_id = str(event.get_self_id() or "")
        messages = getattr(getattr(event, "message_obj", None), "message", []) or []
        targets = []
        for message in messages:
            if isinstance(message, Comp.At):
                user_id = str(getattr(message, "qq", "") or "")
                if user_id and user_id not in {bot_id, "all"} and user_id not in {x[0] for x in targets}:
                    targets.append((user_id, str(getattr(message, "name", "") or "")))
        return targets

    @staticmethod
    def _duel_raw(event: AstrMessageEvent) -> str:
        """优先组合文字组件，QQ 插入 @ 前后未加空格也不会连到宝可梦名字。"""
        messages = getattr(getattr(event, "message_obj", None), "message", []) or []
        parts = [message.text for message in messages if isinstance(message, Comp.Plain)]
        text = " ".join(part for part in parts if isinstance(part, str) and part.strip())
        commands = ("宝可梦挑战", "收藏挑战", "挑战宝可梦", "接收挑战", "接受对战", "接受挑战")
        return text if any(command in text for command in commands) else str(getattr(event, "message_str", "") or "")

    @filter.command("宝可梦挑战", alias={"收藏挑战", "挑战宝可梦"})
    async def cmd_owned_challenge(self, event: AstrMessageEvent, keyword: str = ""):
        """宝可梦挑战 <自己的图鉴编号或名字> @群友 [回合] [闪光]。"""
        try:
            try:
                from .duel_commands import parse_challenge
            except ImportError:
                from duel_commands import parse_challenge
            duels, scope, user_id, nickname = self._owned_duel_context(event)
            raw = self._duel_raw(event)
            request = parse_challenge(raw, self._duel_mentions(event), bot_id=str(event.get_self_id() or ""))
            match = await asyncio.to_thread(
                duels.challenge, scope, user_id, request.target_id, request.selection,
                challenger_name=nickname, target_name=request.target_name,
                shiny=request.shiny, max_turns=request.max_turns,
            )
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] 发起收藏对战失败: {exc}")
            yield event.plain_result("挑战暂时无法保存，请稍后重试。")
            return
        chosen = match["challenger"]
        text = (
            f"【宝可梦挑战】{chosen['nickname']} 发起 Lv.50 收藏对战 · 最多 {match['max_turns']} 回合\n"
            f"出战：图鉴编号 #{chosen['dex']} · {chosen['name']}{' · 闪光' if chosen['shiny'] else ''}\n"
            "直接发送“接收挑战”，后面加上你在本群已拥有的宝可梦图鉴编号或名字即可开战，例如“接收挑战 25”或“接收挑战 皮卡丘”。使用闪光个体时在末尾加“闪光”；不想应战可发送“拒绝挑战”。\n"
            "挑战 5 分钟内有效；达到回合上限仍未分胜负判平局。对局不消耗宝可梦和星尘，可用“取消挑战”撤回。"
        )
        yield event.chain_result([Comp.At(qq=match["target"]["user_id"]), Comp.Plain(text)])

    @filter.command("接收挑战", alias={"接受对战", "接受挑战"})
    async def cmd_owned_accept(self, event: AstrMessageEvent, keyword: str = ""):
        """接收挑战 <自己的图鉴编号或名字> [闪光] [详细] [卡片/图片转发/转发/文本]。"""
        match = None
        try:
            try:
                from .duel_commands import parse_accept
            except ImportError:
                from duel_commands import parse_accept
            duels, scope, user_id, nickname = self._owned_duel_context(event)
            raw = self._duel_raw(event)
            request = parse_accept(raw)
            match = await asyncio.to_thread(
                duels.accept, scope, user_id, request.selection, nickname=nickname,
                shiny=request.shiny,
            )
            detailed, _ = parse_battle_options(raw)
            battle = await asyncio.to_thread(
                build_battle_report, self.db, match["challenger"]["row"], match["target"]["row"], 50,
                seed=match["seed"], detailed=detailed, max_turns=match.get("max_turns", 50),
            )
            participants = [match["challenger"], match["target"]]
            battle["trainer_names"] = [side["nickname"] for side in participants]
            battle["owned_items"] = participants
            battle["duel_id"] = match["match_id"]
            await asyncio.to_thread(duels.complete, scope, match["match_id"], battle["result"])
            battle["champion_line"] = f"收藏对战 · 最多 {match.get('max_turns', 50)} 回合 · 战绩已保存 · 对战记录可查询"
        except Exception as exc:
            if match is not None:
                try:
                    await asyncio.to_thread(duels.abort, scope, match["match_id"])
                except Exception as abort_exc:
                    logger.error(f"[{PLUGIN_ID}] 释放收藏对战失败: {abort_exc}")
            if not isinstance(exc, GachaError):
                logger.error(f"[{PLUGIN_ID}] 收藏对战失败: {exc}")
            yield event.plain_result(str(exc) if isinstance(exc, GachaError) else "对战暂时无法完成，本次未计入战绩，请重新挑战。")
            return
        async for result in self._send_battle_report(event, battle, mode=self._battle_output_mode(raw)):
            yield result

    async def _withdraw_owned_duel(self, event: AstrMessageEvent, *, reject: bool):
        try:
            duels, scope, user_id, _ = self._owned_duel_context(event)
            match = await asyncio.to_thread(duels.reject if reject else duels.cancel, scope, user_id)
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] 撤回收藏挑战失败: {exc}")
            yield event.plain_result("操作暂时无法保存，请稍后重试。")
            return
        yield event.plain_result(f"已{'拒绝' if reject else '取消'} {match['challenger']['nickname']} 发起的挑战，未计入战绩。")

    @filter.command("拒绝对战", alias={"拒绝挑战"})
    async def cmd_owned_reject(self, event: AstrMessageEvent):
        """拒绝等待自己接受的收藏挑战，不计入战绩。"""
        async for result in self._withdraw_owned_duel(event, reject=True):
            yield result

    @filter.command("取消对战", alias={"取消挑战"})
    async def cmd_owned_cancel(self, event: AstrMessageEvent):
        """取消自己发起的收藏挑战，不计入战绩。"""
        async for result in self._withdraw_owned_duel(event, reject=False):
            yield result

    @filter.command("对战记录", alias={"我的战绩"})
    async def cmd_owned_history(self, event: AstrMessageEvent):
        """查看自己或 @ 群友的胜负平统计及最近五场收藏对战。"""
        try:
            duels, scope, user_id, nickname = self._owned_duel_context(event)
            mentions = self._duel_mentions(event)
            if len(mentions) > 1:
                raise GachaError("一次只能查看一位训练家的战绩。")
            if mentions:
                user_id, nickname = mentions[0]
            stats, records = await asyncio.gather(
                asyncio.to_thread(duels.profile, scope, user_id),
                asyncio.to_thread(duels.history, scope, user_id, limit=5),
            )
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] 读取收藏战绩失败: {exc}")
            yield event.plain_result("战绩暂时无法读取，请稍后重试。")
            return
        name = nickname or stats["nickname"]
        lines = [f"【{name} 的收藏战绩】", f"{stats['wins']}胜 / {stats['losses']}负 / {stats['draws']}平 · {stats['total']}场",
                 f"积分 {stats['points']} · 胜率 {stats['win_rate']:g}%"]
        for record in records:
            a, b = record["challenger"], record["target"]
            verdict = "平局" if not record["winner_id"] else "胜" if record["winner_id"] == user_id else "负"
            lines.append(f"{verdict} · {a['nickname']}的{a['name']} vs {b['nickname']}的{b['name']} · {record['turns']}回合 · 种子={record['seed']}")
        if not records:
            lines.append("暂无对战记录。例如发送“宝可梦挑战 25 @群友”开始第一场；把 25 换成本群已拥有的宝可梦图鉴编号或名字，并真正 @ 要挑战的群友。")
        else:
            lines.append("以上为最近五场；3分/胜、1分/平。对战排行查看本群榜单。")
        for part in split_battle_text("\n".join(lines)):
            yield event.plain_result(part)

    @filter.command("对战排行")
    async def cmd_owned_ranking(self, event: AstrMessageEvent):
        """本群收藏对战积分前十名，3分/胜、1分/平。"""
        try:
            duels, scope, _, _ = self._owned_duel_context(event)
            rows = await asyncio.to_thread(duels.ranking, scope, limit=10)
        except GachaError as exc:
            yield event.plain_result(str(exc))
            return
        except Exception as exc:
            logger.error(f"[{PLUGIN_ID}] 读取对战排行失败: {exc}")
            yield event.plain_result("对战排行暂时无法读取，请稍后重试。")
            return
        lines = ["【本群对战排行】3分/胜、1分/平；同分按胜率、胜场排序"]
        lines.extend(f"{index}. {row['nickname']} · {row['points']}分 · {row['wins']}胜{row['losses']}负{row['draws']}平 · 胜率{row['win_rate']:g}%" for index, row in enumerate(rows, 1))
        if not rows:
            lines.append("暂无已完成的收藏对战。")
        yield event.plain_result("\n".join(lines))

    # ---------- Phase 4：队伍分析 / 种族值筛选 ----------
    def _champ_rank_short(self, name: str) -> str:
        """成员名次短标注「单#3/双#82」；查不到或无数据返回空串。"""
        if not self.champions.exists:
            return ""
        ranks = {r["format"]: r["rank"] for r in self.champions.pokemon_ranks(name)}
        if not ranks:
            return ""
        s = f"单#{ranks['single']}" if ranks.get("single") else "单—"
        d = f"双#{ranks['double']}" if ranks.get("double") else "双—"
        return f"{s}/{d}"

    def _team_text(self, result: dict) -> str:
        lines = [
            "【队伍分析】"
            + "、".join(m.get("name") or m.get("keyword") for m in result["members"])
        ]
        for m in result["members"]:
            if "error" in m:
                lines.append(f"? {m['keyword']}：{m['error']}")
                continue
            weak = "、".join(f"{a}×{v:g}" for a, v in m["weak"]) or "无"
            champ = self._champ_rank_short(m["name"])
            tag = f"［冠军赛 {champ}］" if champ else ""
            lines.append(f"{m['name']}{tag}（{m['types']}）：怕 {weak}")
        if result["common_weaknesses"]:
            lines.append("⚠ 共同弱点（队伍整体需防范）：")
            lines += [
                f"· {a}（{len(ns)} 只怕：{'、'.join(ns)}）"
                for a, ns in result["common_weaknesses"]
            ]
        else:
            lines.append("✅ 没有出现在过半成员身上的共同弱点")
        if result["team_resists"]:
            lines.append(
                "✅ 全队都不怕的进攻属性：" + "、".join(result["team_resists"])
            )
        if result["attack_coverage"]:
            lines.append("本队属性覆盖：" + "、".join(result["attack_coverage"]))
        return "\n".join(lines)

    @filter.command("队伍分析")
    async def cmd_team(self, event: AstrMessageEvent, keyword: str = ""):
        """分析队伍弱点配置，如：队伍分析 皮卡丘、喷火龙、卡比兽"""
        names = [x for x in keyword.replace("，", " ").replace("、", " ").split() if x]
        if len(names) < 2:
            yield event.plain_result(
                "用法：队伍分析 <名称1> <名称2> [最多6只]，如：队伍分析 皮卡丘 喷火龙 卡比兽"
            )
            return
        result = self.db.analyze_team(names[:6])
        yield event.plain_result(self._team_text(result))

    @filter.command("筛选")
    async def cmd_by_stats(self, event: AstrMessageEvent, keyword: str = ""):
        """按种族值筛选宝可梦，如：筛选 速度>100 特攻>=120"""
        conds = parse_stat_conditions(keyword)
        if not conds:
            yield event.plain_result(
                "用法：筛选 <条件>（可多条件空格分隔），如：筛选 速度>100 特攻>=120 总和<=500\n"
                "条件：HP/攻击/防御/特攻/特防/速度/总和 + >=/<=/>/</="
            )
            return
        hits = self.db.by_stats(conds)
        if not hits:
            yield event.plain_result("没有符合条件的宝可梦")
            return
        cond_text = " ".join(f"{l}{o}{v}" for l, o, v in conds)
        lines = [f"【筛选 {cond_text}】命中 {len(hits)} 只（按种族总和降序）："]
        lines += [
            f"{i}. {r['name']}（{r['types']}）总和{r['total']}"
            for i, r in enumerate(hits, 1)
        ]
        yield event.plain_result("\n".join(lines))

    # ---------- 许愿星基拉祈（WishMaker） ----------
    @filter.command("许愿星")
    async def cmd_wishmaker(
        self,
        event: AstrMessageEvent,
        nature: str = "",
        min_iv: str = "",
        tid: str = "",
        sid: str = "",
    ):
        """许愿星基拉祈目标筛选（红/蓝宝石），如：许愿星 固执 25 20040 0"""
        if not nature:
            yield event.plain_result(
                "用法：许愿星 <性格> [个体下限] [TID SID]\n"
                "性格：\n" + WISHMAKER_NATURES + "\n"
                "如：许愿星 固执 25 = 六项个体≥25 的固执；末尾附 TID SID 可标注异色"
            )
            return
        try:
            iv_floor = max(0, min(31, int(min_iv))) if min_iv else 0
            tsv = (int(tid) ^ int(sid)) if tid and sid else None
        except ValueError:
            yield event.plain_result("个体下限 / TID / SID 需要是数字")
            return
        # 65536 行全表扫描，放线程池避免阻塞事件循环。
        count, rows = await asyncio.to_thread(
            self.db.wishmaker_query,
            nature=nature,
            min_iv=iv_floor,
            tsv=tsv,
        )
        if not count:
            yield event.plain_result(
                f"没有符合的目标（性格请从：{WISHMAKER_NATURES} 中选择）"
            )
            return
        # 默认图片卡片；wishmaker_style=markdown 时走 markdown（QQ 官方 / 微信个人号等支持渲染的平台）
        if self.config.get("wishmaker_style", "image") == "markdown":
            result = event.plain_result(
                build_wishmaker_markdown(nature, iv_floor, count, rows, tsv)
            )
            result.use_markdown(True)  # 仅支持的平台消费该标记，其他平台忽略
            yield result
            return
        try:
            png = await asyncio.to_thread(
                self.renderer.render_wishmaker_card,
                nature,
                iv_floor,
                count,
                rows,
                tsv,
            )
            yield event.chain_result([Comp.Image.fromBytes(png)])
        except Exception as e:
            logger.warning(f"许愿星卡片渲染失败: {e}")
            yield event.plain_result("许愿星卡片渲染失败，请稍后再试")

    @staticmethod
    def _format_wishmaker(
        nature: str, iv_floor: int, count: int, rows: list[dict]
    ) -> str:
        lines = [
            f"【许愿星基拉祈】性格 {nature or '任意'} · 个体≥{iv_floor}（命中 {count} 个种子）"
        ]
        if nature in NATURES:
            lines.append(nature_effect_text(nature))
        for i, r in enumerate(rows, 1):
            ivs = "/".join(r[k] for k in ("hp", "atk", "def", "spa", "spd", "spe"))
            mark = " ✨异色" if r.get("shiny") else ""
            lines.append(
                f"{i}. 种子 {r['seed']} | PID {r['pid']} | 个体 {ivs}（总和{r['total']}）"
                f"| 觉醒力量 {r['hp_type']}{r['hp_power']}{mark}"
            )
        lines.append(
            "提示：种子 = 红/蓝宝石接收许愿星基拉祈时的 WishMaker 种子（16位十六进制）"
        )
        return "\n".join(lines)

    # ---------- 冠军赛季数据（champions.db，只读查询） ----------
    # 一个入口搞定全部查询，参数按 token 自动分类：
    #   宝冠军                         → 数据状态 + 用法
    #   宝冠军 单打 / 宝冠军 双打       → Top10（可再加赛季：M-6 / 6 / 上一轮）
    #   宝冠军 3 / 宝冠军 双打 3        → 第3名是谁（附配置摘要）
    #   宝冠军 烈咬陆鲨                 → 名次（单+双）+ 配置使用率
    #   宝冠军 烈咬陆鲨 趋势            → 跨赛季名次变化
    #   宝冠军 烈咬陆鲨 M-6            → 指定赛季（数字与名称同现时视为赛季）
    #   宝冠军 生命宝珠                 → 道具反查（谁在带它）
    #   宝冠军 数据                     → 本地库状态
    CHAMP_FORMATS: ClassVar[dict[str, str]] = {
        "单打": "single",
        "双打": "double",
        "single": "single",
        "double": "double",
    }
    CHAMP_LATEST_WORDS: ClassVar[set[str]] = {
        "最新",
        "这赛季",
        "本赛季",
        "latest",
        "this",
    }
    CHAMP_PREV_WORDS: ClassVar[set[str]] = {
        "上一轮",
        "上一赛季",
        "上赛季",
        "上个赛季",
        "上季",
        "prev",
        "previous",
    }
    CHAMP_SEASON_RE: ClassVar[re.Pattern[str]] = re.compile(
        r"(?:m-?|第)?(\d{1,3})(?:赛季?)?", re.IGNORECASE
    )

    @classmethod
    def _parse_champ_args(cls, text: str) -> dict:
        """把指令参数按 token 分类。

        数字语义消歧：与名称词同现时视为赛季编号（宝冠军 烈咬陆鲨 6 → M-6），
        否则首个数字是名次（宝冠军 3 → 第3名）、第二个数字是赛季。
        返回 {fmt, rank, season, view, names}；season 取值 None(最新)/"prev"/"M-N"。
        """
        fmt = rank = view = None
        season: str | None = None
        names: list[str] = []
        digits: list[int] = []
        for raw in (text or "").split():
            t = raw.strip()
            low = t.lower()
            if low in cls.CHAMP_FORMATS and fmt is None:
                fmt = cls.CHAMP_FORMATS[low]
            elif low in {"趋势", "trend"}:
                view = "trend"
            elif low in {"帮助", "help", "用法"}:
                view = "help"
            elif low in {"数据", "状态", "status"}:
                view = "status"
            elif low in cls.CHAMP_LATEST_WORDS:
                season = None
            elif low in cls.CHAMP_PREV_WORDS:
                season = "prev"
            elif low.startswith(("m", "第")) or low.endswith("赛季"):
                m = cls.CHAMP_SEASON_RE.fullmatch(low)
                if m:
                    season = f"M-{m.group(1)}"
                else:
                    names.append(t)
            elif t.lstrip("#").isdigit() and len(t.lstrip("#")) <= 3:
                digits.append(int(t.lstrip("#")))
            else:
                names.append(t)
        if names:  # 有名称词：数字是赛季
            if digits:
                season = f"M-{digits.pop(0)}"
        elif digits:  # 无名称词：首个数字是名次，其余是赛季
            rank = digits.pop(0)
            if digits:
                season = f"M-{digits.pop(0)}"
        return {
            "fmt": fmt,
            "rank": rank,
            "season": season,
            "view": view,
            "names": names,
        }

    def _champ_season_real(self, season: str | None, fmt: str) -> str | None:
        """把 None/"prev"/"M-N"/"6" 解析为库里真实赛季；prev 只有一个赛季时 None。"""
        if season == "prev":
            seasons = self.champions.seasons(fmt)
            return seasons[1] if len(seasons) > 1 else None
        s = (season or "").strip()
        if s.isdigit():
            s = f"M-{s}"
        return self.champions.resolve_season(s or None, fmt)

    @staticmethod
    def _champ_format_arg(fmt: str) -> str:
        """LLM 工具的 fmt 参数（single/double）→ 内部格式。"""
        return "double" if (fmt or "").strip().lower() == "double" else "single"

    def _champ_updated_line(
        self, season: str | None, source: str = "pokechamdb"
    ) -> str:
        """ "本地数据更新：…（站点更新：…）" 行，跨格式查询复用。"""
        st = self.champions.status(source=source, season=season)
        if not st:
            return ""
        local = ""
        try:
            from datetime import datetime

            dt = datetime.fromisoformat(st["run_at"])
            local = dt.astimezone().strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            pass
        site = st.get("source_updated_at") or "站点时间未知"
        return f"本地数据更新：{local or st.get('run_at', '?')}（站点更新：{site}）"

    def _champ_header(
        self, fmt: str, season: str | None, source: str = "pokechamdb"
    ) -> str:
        """查询结果头部：赛季/格式 + 数据来源 + 本地数据更新日期。"""
        season = self.champions.resolve_season(season, fmt, source)
        lines = [
            (
                f"【宝可梦 Champions {season or '?'} {fmt_label(fmt)}】"
                f"来源：{source_label(source)}"
            )
        ]
        updated = self._champ_updated_line(season, source)
        if updated:
            lines.append(updated)
        return "\n".join(lines)

    @staticmethod
    def _champ_ev_text(value: str) -> str:
        """ "2-32-0-0-0-32" → "HP2/攻32/防0/特攻0/特防0/速32"。"""
        keys = ("HP", "攻", "防", "特攻", "特防", "速")
        parts = value.split("-")
        if len(parts) != 6:
            return value
        return "/".join(f"{k}{v}" for k, v in zip(keys, parts))

    def _champ_no_data(self) -> str:
        return (
            "本地还没有冠军赛数据。\n"
            "请在插件目录运行独立抓取脚本：uv run python champions_fetch.py"
            "（详见 README「冠军赛季数据」一节）"
        )

    def _champ_option_line(
        self, pokemon_id: str, fmt: str, season: str | None, otype: str, top_n: int
    ) -> str | None:
        """某类配置的一行摘要（名称 使用率%），无数据返回 None。"""
        opts = self.champions.pokemon_options(
            pokemon_id, fmt, season, option_type=otype, top_n=top_n
        )
        if not opts:
            return None
        parts = []
        for o in opts:
            if otype == "teammate":
                parts.append(f"{o['option_value']}(#{o['rank']})")
            elif otype == "ev_spread":
                pct = f" {o['usage_pct']:g}%" if o.get("usage_pct") is not None else ""
                parts.append(f"{self._champ_ev_text(o['option_value'])}{pct}")
            else:
                pct = f" {o['usage_pct']:g}%" if o.get("usage_pct") is not None else ""
                parts.append(f"{o['option_value']}{pct}")
        return f"{OPTION_LABELS.get(otype, otype)}：{'、'.join(parts)}"

    def _champ_ranking_text(
        self, fmt: str, season: str | None, limit: int | None = None
    ) -> str:
        """排名 TopN 文本。"""
        limit = limit or max(1, min(50, int(self.config.get("champions_top_n", 10))))
        rows = self.champions.top_usage(fmt, season, limit=limit)
        if not rows:
            season_real = self.champions.resolve_season(season, fmt)
            return (
                f"本地没有 {fmt_label(fmt)} {season_real or ''} 的排名数据，"
                f"可用赛季：{'、'.join(self.champions.seasons(fmt)) or '无'}"
            )
        lines = [self._champ_header(fmt, season), ""]
        for r in rows:
            pct = f"{r['usage_pct']:g}%" if r.get("usage_pct") is not None else ""
            lines.append(
                f"#{r['rank']:<3} {r['name_zh'] or r['pokemon_id']} {pct}".rstrip()
            )
        if rows[0].get("usage_pct") is None:
            lines.append("（该来源仅提供名次，无使用率百分比）")
        lines.append("提示：宝冠军 3 = 第3名是谁｜宝冠军 <宝可梦> = 名次+配置")
        return "\n".join(lines)

    def _champ_rank_row_text(self, fmt: str, row: dict) -> str:
        """「第 N 名是谁」：名次 + 宝可梦 + 招式/道具摘要 + 反查提示。"""
        season = row.get("season") or self.champions.resolve_season(None, fmt)
        lines = [
            (
                f"【宝可梦 Champions {season or '?'} {fmt_label(fmt)} "
                f"第 {row['rank']} 名】来源：{source_label('pokechamdb')}"
            ),
            self._champ_updated_line(season),
            (
                f"#{row['rank']} {row['name_zh'] or row['pokemon_id']}"
                f"（共 {row.get('total', '?')} 只）"
            ),
        ]
        for otype, top_n in (("move", 3), ("item", 2)):
            line = self._champ_option_line(row["pokemon_id"], fmt, season, otype, top_n)
            if line:
                lines.append(line)
        lines.append(
            f"提示：宝冠军 {row['name_zh'] or row['pokemon_id']} = 完整配置与单双打名次"
        )
        return "\n".join(line for line in lines if line)

    def _champ_pokemon_card_text(
        self, prow: dict, fmt_req: str | None, season_real: str | None
    ) -> str:
        """宝可梦卡片：名次（未指定格式则单双都给）+ 配置使用率 + 反查提示。"""
        pid = prow["pokemon_id"]
        name = prow.get("name_zh") or pid
        fmt_show = fmt_req or "single"
        fmts = [fmt_req] if fmt_req else ["single", "double"]
        lines = [
            (
                f"【{name} 冠军赛配置】赛季 {season_real or '?'}｜"
                f"来源：{source_label('pokechamdb')}"
            )
        ]
        updated = self._champ_updated_line(season_real)
        if updated:
            lines.append(updated)
        for fmt in fmts:
            row = (
                prow
                if fmt == fmt_show
                else self.champions.resolve_pokemon(pid, fmt, season_real)
            )
            if not row:
                lines.append(f"{fmt_label(fmt)}：榜单未收录")
                continue
            pct = (
                f"，使用率 {row['usage_pct']:g}%"
                if row.get("usage_pct") is not None
                else ""
            )
            lines.append(
                f"{fmt_label(fmt)}：#{row['rank']}（共 "
                f"{self.champions.count(fmt, row['season'])} 只）{pct}"
            )
            for otype, top_n in (
                ("move", 4),
                ("item", 3),
                ("ability", 2),
                ("nature", 2),
                ("ev_spread", 2),
                ("teammate", 3),
                ("tera", 2),
            ):
                line = self._champ_option_line(pid, fmt, row["season"], otype, top_n)
                if line:
                    lines.append(("　" if len(fmts) > 1 else "") + line)
        other = next(f for f in ("single", "double") if f != fmt_show)
        lines.append(
            f"提示：宝冠军 {name} 趋势 = 跨赛季｜宝冠军 {name} "
            f"{fmt_label(other)} = 另一格式"
        )
        return "\n".join(line for line in lines if line)

    def _champ_trend_text(self, prow: dict, fmt_req: str | None) -> str:
        """跨赛季名次（未指定格式则单双并列）。"""
        pid = prow["pokemon_id"]
        name = prow.get("name_zh") or pid
        fmts = [fmt_req] if fmt_req else ["single", "double"]
        lines = [f"【{name} 跨赛季名次】来源：{source_label('pokechamdb')}"]
        any_row = False
        for fmt in fmts:
            rows = self.champions.trend(pid, fmt)
            if not rows:
                continue
            any_row = True
            body = "｜".join(f"{r['season']} #{r['rank']}" for r in rows)
            lines.append(f"{fmt_label(fmt)}：{body}")
        if not any_row:
            lines.append("本地只有一个赛季的数据，暂无趋势（多抓几个赛季后再试）")
        else:
            lines.append("提示：宝冠军 <宝可梦> = 当前赛季完整配置")
        return "\n".join(lines)

    def _champ_item_text(
        self, item: str, rows: list[dict], fmt: str, season_real: str | None
    ) -> str:
        """道具反查：谁在带它。"""
        lines = [
            (
                f"【{fmt_label(fmt)} {season_real or '?'} 携带「{rows[0]['item']}」】"
                f"来源：{source_label('pokechamdb')}"
            ),
            self._champ_updated_line(season_real),
        ]
        for r in rows[:10]:
            pct = (
                f" {r['item_usage_pct']:g}%"
                if r.get("item_usage_pct") is not None
                else ""
            )
            lines.append(f"#{r['rank']} {r['name_zh'] or r['pokemon_id']}{pct}")
        lines.append("提示：宝冠军 <宝可梦名> = 它的完整配置")
        return "\n".join(line for line in lines if line)

    def _champ_status_text(self) -> str:
        """本地库状态。"""
        info = self.champions.database_summary()
        if not info.get("exists"):
            return self._champ_no_data()
        lines = ["【冠军赛本地数据】"]
        for fmt in ("single", "double"):
            seasons = self.champions.seasons(fmt)
            if not seasons:
                continue
            latest = seasons[0]
            n = len(self.champions.top_usage(fmt, latest, limit=1000))
            st = self.champions.status(fmt=fmt, season=latest)
            when = ""
            if st:
                try:
                    from datetime import datetime

                    when = (
                        datetime.fromisoformat(st["run_at"])
                        .astimezone()
                        .strftime("%Y-%m-%d %H:%M")
                    )
                except (ValueError, TypeError):
                    pass
            src = f"，来源 {source_label(st['source'])}" if st else ""
            lines.append(
                f"{fmt_label(fmt)} {'、'.join(seasons[:3])}：{n} 只"
                + (f"，本地更新 {when}{src}" if when else "")
            )
        if len(lines) == 1:
            lines.append("数据库为空，请先运行抓取脚本")
        lines.append(f"库文件：{info['path']}")
        return "\n".join(lines)

    def _champ_help_text(self) -> str:
        st_line = ""
        latest = self.champions.latest_season("single")
        if latest:
            st_line = f"当前本地赛季：{latest}（宝冠军 数据 看详情）\n"
        return (
            "【冠军赛查询】\n" + st_line + "宝冠军 单打 / 宝冠军 双打 — Top10\n"
            "宝冠军 3 / 宝冠军 双打 3 — 第3名是谁\n"
            "宝冠军 <宝可梦> — 名次(单双) + 配置使用率\n"
            "宝冠军 <宝可梦> 趋势 — 跨赛季名次\n"
            "宝冠军 <道具名> — 谁在带这个道具\n"
            "赛季：默认最新，可加 M-6 / 6 / 上一轮\n"
            "对战 <A> <B> 结果里也会带双方名次"
        )

    @filter.command("宝冠军")
    async def cmd_champion(self, event: AstrMessageEvent, keyword: str = ""):
        """冠军赛一站式查询：宝冠军 帮助 看用法"""
        if not self.champions.exists:
            yield event.plain_result(self._champ_no_data())
            return
        args = self._parse_champ_args(keyword)
        fmt, rank, season, view, names = (
            args["fmt"],
            args["rank"],
            args["season"],
            args["view"],
            args["names"],
        )
        if view == "status":
            yield event.plain_result(self._champ_status_text())
            return
        if view == "help" or (
            not names
            and rank is None
            and fmt is None
            and view is None
            and season is None
        ):
            yield event.plain_result(self._champ_help_text())
            return
        fmt_eff = fmt or self.config.get("champions_default_format", "single")
        season_real = self._champ_season_real(season, fmt_eff)
        if season == "prev" and season_real is None:
            latest = self.champions.latest_season(fmt_eff)
            yield event.plain_result(f"本地只有 {latest or '?'} 一个赛季，无法查上一轮")
            return

        # ---- 无名称：排名 / 按名次 ----
        if not names:
            if rank is not None:
                row = self.champions.by_rank(rank, fmt_eff, season_real)
                if not row:
                    yield event.plain_result(
                        f"{fmt_label(fmt_eff)} {season_real or ''} 榜单没有第 {rank} 名"
                    )
                else:
                    yield event.plain_result(self._champ_rank_row_text(fmt_eff, row))
            else:
                yield event.plain_result(self._champ_ranking_text(fmt_eff, season_real))
            return

        # ---- 有名称：趋势 / 宝可梦卡片 / 道具反查 ----
        name = names[0]
        if view == "trend":
            prow = self.champions.resolve_pokemon(name, fmt_eff, season_real)
            if not prow:
                yield event.plain_result(
                    f"{fmt_label(fmt_eff)} {season_real or ''} 榜单中没有「{name}」"
                )
            else:
                yield event.plain_result(self._champ_trend_text(prow, fmt))
            return
        prow = self.champions.resolve_pokemon(name, fmt_eff, season_real)
        if prow:
            yield event.plain_result(
                self._champ_pokemon_card_text(prow, fmt, season_real)
            )
            return
        rows = self.champions.find_pokemon_by_item(name, fmt_eff, season_real)
        if rows:
            yield event.plain_result(
                self._champ_item_text(name, rows, fmt_eff, season_real)
            )
            return
        yield event.plain_result(
            f"「{name}」既不在 {fmt_label(fmt_eff)} 榜单也没有道具匹配；"
            f"发「宝冠军 帮助」看用法"
        )

    def _help_text(self) -> str:
        return (
            "🎒 宝可梦图鉴\n"
            "今日宝可梦 / 我的宝可梦 [页码] / 收集资料 / 兑换宝可梦 <名称> [闪光]\n"
            "收集排行 / 抽取概率 / 图鉴帮助 — 抽卡与收藏\n"
            "宝可梦 <编号/名称> — 图鉴卡片（头部带冠军赛名次）\n"
            "宝可梦 <名称> 招式表 [等级] — 可学招式与推荐配招\n"
            "宝可梦 <名称> 能力值 [等级 个体 性格] — 等级能力值计算\n"
            "特性 <名称> / 招式 <名称> / 道具 <名称> — 数据查询\n"
            "性格 [名称] — 性格能力修正速查（性格 固执 看单个）\n"
            "相性 <属性1> [属性2] / 相性 打 <属性> — 属性相性防守/进攻\n"
            "克制 <宝可梦> / 克制 <A> <B> — 克制推荐与多维度对比\n"
            "对战 <名称1> <名称2> [等级] [详细] [种子=123] — 招式效果与逐回合战报\n"
            "队伍分析 <名称...> — 队伍弱点配置分析\n"
            "筛选 <条件> — 按种族值筛选，如 速度>100 特攻>=120\n"
            "许愿星 <性格> [个体下限] [TID SID] — 许愿星基拉祈目标筛选\n"
            "宝冠军 <关键词> — 冠军赛一站式查询，宝冠军 帮助 看用法\n"
            "群聊 @我 直接提问时也会自动调用图鉴工具（需模型支持函数调用）"
        )

    # ---------- LLM 函数调用工具 ----------
    @filter.llm_tool(name="query_pokemon")
    async def tool_query_pokemon(self, event: AstrMessageEvent, keyword: str):
        """查询宝可梦图鉴详情（中文）。适用于用户询问某只宝可梦的属性、种族值、特性、
        弱点、进化方式等。返回中文图鉴数据，回答时直接引用，不要自行翻译或编造。
        Args:
            keyword(str): 宝可梦的全国图鉴编号或名称（中/日/英均可）
        """
        rows = self.db.query("pokemon", keyword)
        if not rows:
            return f"没有查到「{keyword}」的相关信息"
        row = self.db.pick_best(rows, keyword)
        others = sorted(
            {r["form_name"] for r in rows if r.get("form_name") and r is not row}
        )
        text = self.db.format_row(row, fields=POKEMON_FIELDS)
        if others:
            text += (
                f"\n其他形态（可用 query_pokemon 按形态名查询）：{'、'.join(others)}"
            )
        return text

    @filter.llm_tool(name="search_pokemon")
    async def tool_search_pokemon(self, event: AstrMessageEvent, keyword: str):
        """模糊搜索宝可梦/特性/招式/道具名称，返回候选列表。适用于用户只记得部分名字时。
        Args:
            keyword(str): 名称关键词（支持片段）
        """
        found = self.db.search(keyword)
        if not found:
            return f"没有查到「{keyword}」的相关信息"
        out = []
        for table, rows in found.items():
            for r in rows[:5]:
                if table == "pokedex":
                    if r.get("dex") == "national":
                        out.append(
                            f"[地区图鉴] 全国 {r.get('id_raw', '')}："
                            f"{r.get('name', '')}（{r.get('types', '')}）"
                        )
                    else:
                        out.append(
                            f"[{r.get('dex', '')}图鉴] {r.get('id_raw', '')}"
                            f"（全国 {r.get('national_id', '')}）：{r.get('name', '')}"
                        )
                    continue
                parts = [
                    f"{r.get(c, '')}"
                    for c in ("id_raw", "national_id", "name_zh", "name_en")
                    if r.get(c)
                ]
                out.append(f"[{TABLES[table]}] {' | '.join(parts)}")
        return "\n".join(out)

    @filter.llm_tool(name="query_ability")
    async def tool_query_ability(self, event: AstrMessageEvent, keyword: str):
        """查询特性（ability）的效果与持有宝可梦。返回中文数据，直接引用即可。
        Args:
            keyword(str): 特性名称（中文或英文）
        """
        rows = self.db.query("abilities", keyword) or self.db.query("ability", keyword)
        if not rows:
            return f"没有查到特性「{keyword}」的相关信息"
        r = rows[0]
        return (
            f"特性 {r.get('name_zh', '')}（{r.get('name_en', '')}）\n"
            f"介绍：{r.get('introduction', '')}\n效果：{r.get('effect', '')[:600]}\n"
            f"持有宝可梦：{(r.get('holders', '') or '')[:400]}"
        )

    @filter.llm_tool(name="query_move")
    async def tool_query_move(self, event: AstrMessageEvent, keyword: str):
        """查询招式（move）的属性、威力、命中、PP 与说明。返回中文数据。
        Args:
            keyword(str): 招式名称（中文或英文）
        """
        rows = self.db.query("move", keyword)
        if rows:
            r = rows[0]
            fields = (
                "id",
                "name_zh",
                "name_en",
                "type",
                "category",
                "power",
                "accuracy",
                "pp",
                "generation",
                "description",
            )
            return self.db.format_row(r, fields=fields)
        detail = self.db.query("moves", keyword)
        if not detail:
            return f"没有查到招式「{keyword}」的相关信息"
        r = detail[0]
        return self.db.format_row(
            r,
            fields=(
                "name_zh",
                "name_en",
                "type",
                "category",
                "power",
                "accuracy",
                "pp",
                "range",
                "description",
                "additional_effect",
            ),
        )

    @filter.llm_tool(name="query_item")
    async def tool_query_item(self, event: AstrMessageEvent, keyword: str):
        """查询道具（携带物品）的类别与说明。返回中文数据，直接引用即可。
        Args:
            keyword(str): 道具名称（中文或英文，支持片段）
        """
        rows = self.db.query("item", keyword)
        if not rows:
            return f"没有查到道具「{keyword}」的相关信息"
        r = rows[0]
        return (
            f"道具 {r.get('name_zh', '')}（{r.get('name_en', '')}）\n"
            f"类别：{r.get('category_path', '')}\n说明：{r.get('description', '')}"
        )

    @filter.llm_tool(name="query_nature")
    async def tool_query_nature(self, event: AstrMessageEvent, keyword: str = ""):
        """查询对战性格（共 25 种）：能力值提升/下降项与口味偏好，用于配招、喂树果或选性格。
        Args:
            keyword(str): 性格中文名（如 固执/胆小/内敛），留空返回全部性格分组速查表
        """
        keyword = (keyword or "").strip()
        if not keyword:
            return nature_table_text()
        if keyword in NATURES:
            return nature_effect_text(keyword)
        hits = [n for n in NATURES if keyword in n]
        if len(hits) == 1:
            return nature_effect_text(hits[0])
        if hits:
            return f"「{keyword}」匹配到多个性格：{'、'.join(hits)}"
        return f"没有性格「{keyword}」。全部性格：{'、'.join(NATURES)}"

    @filter.llm_tool(name="query_dex_region")
    async def tool_query_dex_region(self, event: AstrMessageEvent, keyword: str):
        """查询宝可梦在地区图鉴中的位置，或某地区图鉴的收录范围。返回中文数据。
        Args:
            keyword(str): 全国图鉴编号或地区名（如 999、丰缘、帕底亚）
        """
        rows = self.db.query("pokedex", keyword)
        if not rows:
            return f"没有查到「{keyword}」的图鉴记录"
        out = []
        for r in rows[:15]:
            if r.get("dex") == "national":
                out.append(
                    f"全国图鉴 {r['id_raw']}：{r.get('name', '')}（{r.get('types', '')}，"
                    f"第 {r.get('generation', '?')} 世代）"
                )
            else:
                out.append(
                    f"{r.get('dex', '')}图鉴 {r.get('id_raw', '')}"
                    f"（全国 {r.get('national_id', '')}）：{r.get('name', '')}"
                )
        return "\n".join(out)

    @filter.llm_tool(name="query_type_matchups")
    async def tool_query_type_matchups(
        self, event: AstrMessageEvent, type1: str, type2: str = ""
    ):
        """查询属性相性倍率（本地属性表）。适用于询问某属性（或双属性组合）怕什么、抗什么、免疫什么。
        Args:
            type1(str): 第一个属性（中文，如 火）
            type2(str, optional): 第二个属性（可选，如 飞行）
        """
        result = self.db.matchups([t for t in (type1, type2) if t])
        if result is None:
            return f"没有查到属性「{type1}{'/' + type2 if type2 else ''}」"
        combo = "/".join(x for x in (type1, type2) if x)
        text = (
            f"【{combo}】\n"
            f"弱点：{'、'.join(result['weak']) or '无'}\n"
            f"抗性：{'、'.join(result['resist']) or '无'}\n"
            f"免疫：{'、'.join(result['immune']) or '无'}"
        )
        corr = self._ability_correction_text(result)
        if corr:
            text += f"\n{corr}（若防守方宝可梦持有对应特性则生效）"
        return text

    @filter.llm_tool(name="query_wishmaker_jirachi")
    async def tool_query_wishmaker(
        self,
        event: AstrMessageEvent,
        nature: str = "",
        min_iv: int = 0,
        hp_type: str = "",
        tid: int = -1,
        sid: int = -1,
        shiny_only: bool = False,
    ):
        """查询红/蓝宝石"许愿星基拉祈"（Bonus Disc 领取）的目标种子与个体值。
        覆盖全部 65536 个 WishMaker 种子（与 pokemonrng.com 教程数据一致），
        可按目标性格、六项个体值下限、觉醒力量属性筛选，提供 TID/SID 时可判定异色。
        Args:
            nature(str): 目标性格中文名（勤奋/怕寂寞/勇敢/固执/顽皮/大胆/坦率/悠闲/淘气/乐天/胆小/急躁/认真/爽朗/天真/内敛/慢吞吞/温和/害羞/马虎/沉着/温柔/自大/慎重/浮躁），留空不限
            min_iv(int): 六项个体值的下限（0-31），如 25 表示每项都≥25
            hp_type(str): 觉醒力量属性中文名（格斗/飞行/毒/地面/岩石/虫/幽灵/钢/火/水/草/电/超能力/冰/龙/恶），留空不限
            tid(int): 训练家 ID（异色判定用），不知道传 -1
            sid(int): 隐藏 ID（异色判定用），不知道传 -1
            shiny_only(bool): 只看异色目标（需提供 tid 和 sid）
        """
        tsv = (tid ^ sid) if tid >= 0 and sid >= 0 else None
        iv_floor = max(0, min(31, min_iv))
        count, rows = self.db.wishmaker_query(
            nature=nature or None,
            min_iv=iv_floor,
            hp_type=hp_type or None,
            tsv=tsv,
            shiny_only=shiny_only,
            limit=10,
        )
        if not count:
            return "没有符合条件的目标，请放宽个体值下限或核对性格/属性名称"
        return self._format_wishmaker(nature, iv_floor, count, rows)

    # ---------- 冠军赛季 LLM 工具 ----------
    @filter.llm_tool(name="query_champions_ranking")
    async def tool_champ_ranking(
        self,
        event: AstrMessageEvent,
        fmt: str = "single",
        season: str = "",
        limit: int = 10,
    ):
        """查询宝可梦 Champions 冠军赛使用率排名（本地离线数据）。
        返回前 N 名及数据来源与本地更新日期。适用于"当前单打使用率排名""双打什么强"。
        Args:
            fmt(str): 格式，single=单打 / double=双打，默认 single
            season(str): 赛季，M-7、6 或 "上一轮" 均可，留空用本地最新赛季
            limit(int): 返回数量（1-20），默认 10
        """
        if not self.champions.exists:
            return self._champ_no_data()
        f = self._champ_format_arg(fmt)
        rows = self.champions.top_usage(
            f, self._champ_season_real(season, f), limit=max(1, min(20, int(limit)))
        )
        if not rows:
            return (
                f"本地没有 {fmt_label(f)} 的排名数据"
                f"（可用赛季：{'、'.join(self.champions.seasons(f)) or '无'}）"
            )
        lines = [self._champ_header(f, self._champ_season_real(season, f))]
        for r in rows:
            pct = f"{r['usage_pct']:g}%" if r.get("usage_pct") is not None else ""
            lines.append(
                f"#{r['rank']} {r['name_zh'] or r['pokemon_id']} {pct}".rstrip()
            )
        return "\n".join(lines)

    @filter.llm_tool(name="query_rank_pokemon")
    async def tool_rank_pokemon(
        self,
        event: AstrMessageEvent,
        rank: int,
        fmt: str = "single",
        season: str = "",
    ):
        """按名次反查冠军赛宝可梦：返回该名次的宝可梦与配置摘要（本地离线数据）。
        适用于"单打第3名是谁""双打第5名用什么"；查某宝可梦的名次用 query_pokemon_rank。
        Args:
            rank(int): 名次（如 3 表示第 3 名）
            fmt(str): single=单打 / double=双打，默认 single
            season(str): 赛季，M-7、6 或 "上一轮" 均可，留空用本地最新赛季
        """
        if not self.champions.exists:
            return self._champ_no_data()
        f = self._champ_format_arg(fmt)
        season_real = self._champ_season_real(season, f)
        row = self.champions.by_rank(int(rank), f, season_real)
        if not row:
            return f"{fmt_label(f)} {season_real or ''} 榜单没有第 {rank} 名"
        return self._champ_rank_row_text(f, row)

    @filter.llm_tool(name="query_pokemon_rank")
    async def tool_champ_lookup(
        self, event: AstrMessageEvent, keyword: str, season: str = ""
    ):
        """查询某只宝可梦在冠军赛单打和双打的排名（本地离线数据）。
        适用于"烈咬陆鲨排第几""谜拟Ｑ双打排名多少"；按名次反查宝可梦用 query_rank_pokemon。
        Args:
            keyword(str): 宝可梦名称（如 烈咬陆鲨）
            season(str): 赛季，M-7、6 或 "上一轮" 均可，留空用本地最新赛季
        """
        if not self.champions.exists:
            return self._champ_no_data()
        kw = (keyword or "").strip().lstrip("#")
        rows = self.champions.pokemon_ranks(
            kw, self._champ_season_real(season, "single")
        )
        if not rows:
            season_real = self._champ_season_real(season, "single")
            return f"{season_real or ''} 冠军赛榜单中没有「{keyword}」"
        lines = [
            (
                f"【{rows[0]['name_zh'] or rows[0]['pokemon_id']} 冠军赛排名】"
                f"赛季 {rows[0]['season']}｜来源：{source_label('pokechamdb')}"
            ),
            self._champ_updated_line(rows[0]["season"]),
        ]
        for r in rows:
            pct = (
                f"，使用率 {r['usage_pct']:g}%"
                if r.get("usage_pct") is not None
                else ""
            )
            lines.append(
                f"{fmt_label(r['format'])}：#{r['rank']}（共 {r['total']} 只）{pct}"
            )
        missing = {"single", "double"} - {r["format"] for r in rows}
        if missing:
            lines.append(
                f"{'、'.join(fmt_label(m) for m in sorted(missing))}榜单中未收录该宝可梦"
            )
        return "\n".join(line for line in lines if line)

    @filter.llm_tool(name="query_champions_build")
    async def tool_champ_build(
        self,
        event: AstrMessageEvent,
        keyword: str,
        fmt: str = "single",
        season: str = "",
    ):
        """查询宝可梦在冠军赛的主流配置使用率：招式、道具、特性、性格、能力点、队友
        （本地离线数据，仅单打有配置明细）。适用于"烈咬陆鲨冠军赛怎么配""耿鬼带什么招"。
        Args:
            keyword(str): 宝可梦名称（中文名优先，如 烈咬陆鲨）
            fmt(str): 格式，single=单打 / double=双打，默认 single
            season(str): 赛季，M-7、6 或 "上一轮" 均可，留空用本地最新赛季
        """
        if not self.champions.exists:
            return self._champ_no_data()
        f = self._champ_format_arg(fmt)
        row = self.champions.resolve_pokemon(
            keyword, f, self._champ_season_real(season, f)
        )
        if not row:
            return (
                f"{fmt_label(f)} {self._champ_season_real(season, f) or ''} "
                f"榜单中没有「{keyword}」"
            )
        lines = [self._champ_header(f, self._champ_season_real(season, f))]
        rank_pct = f"（#{row['rank']}" + (
            f"，使用率 {row['usage_pct']:g}%）"
            if row.get("usage_pct") is not None
            else "）"
        )
        lines.append(f"{row['name_zh'] or row['pokemon_id']} {rank_pct}")
        for otype in self.champions.option_types(
            row["pokemon_id"], f, self._champ_season_real(season, f)
        ):
            opts = self.champions.pokemon_options(
                row["pokemon_id"],
                f,
                self._champ_season_real(season, f),
                option_type=otype,
                top_n=3 if otype == "teammate" else 5,
            )
            label = OPTION_LABELS.get(otype, otype)
            parts = []
            for o in opts:
                pct = (
                    f"{o['usage_pct']:g}%"
                    if o.get("usage_pct") is not None
                    else (f"(#{o['rank']})" if o.get("rank") is not None else "")
                )
                if otype == "ev_spread":
                    parts.append(
                        f"{self._champ_ev_text(o['option_value'])} {pct}".strip()
                    )
                else:
                    parts.append(f"{o['option_value']}{pct}")
            if parts:
                lines.append(f"{label}：{'、'.join(parts)}")
        if len(lines) <= 2:
            lines.append("（该宝可梦暂无配置明细；双打格式站点不提供明细）")
        return "\n".join(lines)

    @filter.llm_tool(name="find_champions_by_item")
    async def tool_champ_item(
        self,
        event: AstrMessageEvent,
        item_name: str,
        fmt: str = "single",
        season: str = "",
    ):
        """反查冠军赛中携带某道具的宝可梦及携带率（本地离线数据）。
        适用于"谁带讲究围巾""生命宝珠使用者排名"。
        Args:
            item_name(str): 道具名称（支持片段，如 围巾、宝珠）
            fmt(str): 格式，single=单打 / double=双打，默认 single
            season(str): 赛季，M-7、6 或 "上一轮" 均可，留空用本地最新赛季
        """
        if not self.champions.exists:
            return self._champ_no_data()
        f = self._champ_format_arg(fmt)
        rows = self.champions.find_pokemon_by_item(
            item_name, f, self._champ_season_real(season, f)
        )
        if not rows:
            return f"{fmt_label(f)} 没有宝可梦携带含「{item_name}」的道具"
        lines = [
            self._champ_header(f, self._champ_season_real(season, f)),
            f"携带「{rows[0]['item']}」的宝可梦：",
        ]
        for r in rows:
            pct = (
                f"{r['item_usage_pct']:g}%"
                if r.get("item_usage_pct") is not None
                else ""
            )
            lines.append(
                f"#{r['rank']} {r['name_zh'] or r['pokemon_id']}（道具 {pct}）".rstrip()
            )
        return "\n".join(lines)

    @filter.llm_tool(name="query_champions_trend")
    async def tool_champ_trend(
        self,
        event: AstrMessageEvent,
        keyword: str,
        fmt: str = "single",
    ):
        """查询宝可梦在冠军赛的跨赛季排名变化（本地离线数据）。
        适用于"烈咬陆鲨这几赛季排名怎么样""某宝可梦热度变化"。
        Args:
            keyword(str): 宝可梦名称（中文名优先）
            fmt(str): 格式，single=单打 / double=双打，默认 single
        """
        if not self.champions.exists:
            return self._champ_no_data()
        f = self._champ_format_arg(fmt)
        row = self.champions.resolve_pokemon(keyword, f)
        if not row:
            return f"冠军赛数据中没有「{keyword}」"
        rows = self.champions.trend(row["pokemon_id"], f)
        if not rows:
            return "本地只有一个赛季的数据，暂无趋势可查（运行抓取脚本时多抓几个赛季）"
        lines = [f"【{row['name_zh'] or row['pokemon_id']} 跨赛季排名】{fmt_label(f)}"]
        for r in rows:
            pct = (
                f"，使用率 {r['usage_pct']:g}%"
                if r.get("usage_pct") is not None
                else ""
            )
            lines.append(f"{r['season']}：#{r['rank']}{pct}")
        return "\n".join(lines)

    @filter.llm_tool(name="champions_data_status")
    async def tool_champ_status(self, event: AstrMessageEvent):
        """查看本地冠军赛数据库状态：覆盖的赛季/格式、条目数与最后更新时间。
        用户询问冠军赛数据是否最新、数据来自哪里时使用。
        """
        info = self.champions.database_summary()
        if not info.get("exists"):
            return self._champ_no_data()
        lines = ["【冠军赛本地数据】"]
        for fmt in ("single", "double"):
            seasons = self.champions.seasons(fmt)
            if not seasons:
                continue
            latest = seasons[0]
            n = len(self.champions.top_usage(fmt, latest, limit=1000))
            st = self.champions.status(fmt=fmt, season=latest)
            if st:
                lines.append(
                    f"{fmt_label(fmt)} {latest}：{n} 只，"
                    f"来源 {source_label(st['source'])}，"
                    f"站点更新 {st.get('source_updated_at') or '?'}"
                )
            else:
                lines.append(f"{fmt_label(fmt)} {latest}：{n} 只")
        if len(lines) == 1:
            lines.append("数据库为空")
        return "\n".join(lines)

    @filter.llm_tool(name="find_pokemon_by_type")
    async def tool_find_by_type(self, event: AstrMessageEvent, type_name: str):
        """列出某属性的宝可梦（默认形态）。适用于"电属性宝可梦有哪些"这类问题。
        Args:
            type_name(str): 属性中文名（如 电、水）
        """
        hits = self.db.by_type(type_name)
        if not hits:
            return f"没有查到「{type_name}」属性的宝可梦"
        return f"{type_name}属性宝可梦（共 {len(hits)} 只，列出前 40）：\n" + "、".join(
            f"{r['name_zh']}({r['id_raw']})" for r in hits
        )

    @filter.llm_tool(name="pokeapi_pokemon_detail")
    async def tool_pokeapi_detail(self, event: AstrMessageEvent, name_or_id: str):
        """从 PokéAPI 查询宝可梦官方数据（含中文官方译名与图鉴说明、特性英文效果）。
        本地图鉴查不到、或用户需要官方英文特性效果时使用。
        Args:
            name_or_id(str): 宝可梦的英文名或全国图鉴编号
        """
        if not self.config.get("pokeapi_enabled", True):
            return "PokéAPI 已在配置中禁用"
        try:
            data = await self.api.get_pokemon(name_or_id)
        except Exception as e:
            logger.warning(f"PokéAPI 请求失败: {e}")
            return "PokéAPI 查询失败，请稍后再试"
        if not data:
            return f"没有查到「{name_or_id}」的相关信息"
        stats = "、".join(f"{k}{v}" for k, v in data["stats"].items())
        abilities = "、".join(
            f"{a['name_zh'] or a['name_en']}({'隐藏特性，' if a['hidden'] else ''}{a['effect']})"
            for a in data["abilities"]
        )
        return (
            f"{data['name_zh']}（{data['name_en']}）#{data['id']}，第 {data['generation']} 世代，"
            f"{data['genus']}\n属性：{data['types']} | 身高 {data['height_m']}m | 体重 {data['weight_kg']}kg\n"
            f"种族值：{stats}\n特性：{abilities}\n图鉴：{data['flavor']}"
        )

    @filter.llm_tool(name="pokeapi_type_matchups")
    async def tool_pokeapi_matchups(
        self, event: AstrMessageEvent, type1: str, type2: str = ""
    ):
        """从 PokéAPI 查询属性相性倍率。query_type_matchups 查不到或需要官方倍率表时使用。
        Args:
            type1(str): 第一个属性（中文，如 火）
            type2(str, optional): 第二个属性（可选，如 飞行）
        """
        if not self.config.get("pokeapi_enabled", True):
            return "PokéAPI 已在配置中禁用"
        try:
            result = await self.api.get_type_matchups([t for t in (type1, type2) if t])
        except Exception as e:
            logger.warning(f"PokéAPI 请求失败: {e}")
            return "PokéAPI 查询失败，请稍后再试"
        if result is None:
            return f"没有查到属性「{type1}{'/' + type2 if type2 else ''}」"
        combo = "/".join(x for x in (type1, type2) if x)
        return (
            f"【{combo}】\n"
            f"弱点：{'、'.join(result['weak']) or '无'}\n"
            f"抗性：{'、'.join(result['resist']) or '无'}\n"
            f"免疫：{'、'.join(result['immune']) or '无'}"
        )

    # ---------- Phase 1 LLM 工具：能力值 / 进攻相性 / 对比 / 克制推荐 ----------
    @filter.llm_tool(name="calc_pokemon_stats")
    async def tool_calc_stats(
        self,
        event: AstrMessageEvent,
        keyword: str,
        level: int = 50,
        iv: int = 31,
        nature: str = "",
    ):
        """计算宝可梦在指定等级的能力值（官方公式，含个体值与性格修正，努力值按 0）。
        适用于"皮卡丘50级能力值多少""固执性格影响什么数值"这类问题。
        Args:
            keyword(str): 宝可梦的全国图鉴编号或名称（中/日/英均可）
            level(int): 等级（1-100），默认 50
            iv(int): 个体值（0-31，六项统一），默认 31
            nature(str): 性格中文名（如 固执/内敛/胆小），可留空表示无修正
        """
        row = self.db.pokemon(keyword)
        if not row:
            return f"没有查到「{keyword}」的相关信息"
        if nature and nature not in NATURES:
            return f"没有性格「{nature}」。\n{nature_table_text()}"
        return self._calc_stats_text(
            row, max(1, min(100, int(level))), max(0, min(31, int(iv))), nature
        )

    @filter.llm_tool(name="query_type_offense")
    async def tool_type_offense(self, event: AstrMessageEvent, type_name: str):
        """查询某属性进攻方向的相性：打哪些属性效果拔群、哪些收效甚微、哪些无效。
        适用于"火属性打什么效果拔群"这类攻击方向的问题（防守方向用 query_type_matchups）。
        Args:
            type_name(str): 属性中文名（如 火、龙）
        """
        result = self.db.attack_matchups(type_name)
        if result is None:
            return f"没有查到属性「{type_name}」"
        return (
            f"【{type_name}属性攻击】\n"
            f"效果拔群：{'、'.join(result['super']) or '无'}\n"
            f"收效甚微：{'、'.join(result['weak']) or '无'}\n"
            f"无效：{'、'.join(result['none']) or '无'}"
        )

    @filter.llm_tool(name="compare_pokemon")
    async def tool_compare(self, event: AstrMessageEvent, name1: str, name2: str):
        """对比两只宝可梦：逐项种族值（含每项更强的一方）、属性互克倍率、特性。
        适用于"皮卡丘和喷火龙谁更强""两只宝可梦对比"这类问题。
        Args:
            name1(str): 第一只宝可梦的编号或名称
            name2(str): 第二只宝可梦的编号或名称
        """
        row1, row2 = self.db.pokemon(name1), self.db.pokemon(name2)
        if not row1 or not row2:
            return f"没有查到「{name1 if not row1 else name2}」的相关信息"
        return self._compare_text(row1, row2)

    @filter.llm_tool(name="find_pokemon_counters")
    async def tool_counters(self, event: AstrMessageEvent, keyword: str):
        """推荐克制某宝可梦的候选列表（双向验证：候选打目标 ≥2倍 且目标打候选 ≤1倍，
        按打击倍率与种族值排序）。适用于"用什么打妙蛙花""妙蛙花的克制是谁"。
        Args:
            keyword(str): 目标宝可梦的编号或名称
        """
        result = self.db.find_counters(keyword)
        if not result:
            return f"没有查到「{keyword}」的相关信息"
        lines = [f"克制 {result['target']}（{result['target_types']}）的推荐："]
        for i, c in enumerate(result["counters"], 1):
            lines.append(
                f"{i}. {c['name']}（{c['types']}）"
                f"打它×{c['atk_mult']:g}｜它打你×{c['def_mult']:g}｜种族{c['total']}"
            )
        if len(lines) == 1:
            lines.append("没有同时满足双向条件的宝可梦")
        return "\n".join(lines)

    # ---------- Phase 2 LLM 工具：招式表 / 配招校验 ----------
    @filter.llm_tool(name="query_learnset")
    async def tool_learnset(
        self, event: AstrMessageEvent, keyword: str, max_level: int = 0
    ):
        """查询宝可梦可学的招式表（升级/招式学习器/遗传分类）与推荐配招。
        适用于"皮卡丘能学什么招""皮卡丘50级学什么""给皮卡丘推荐配招"。
        Args:
            keyword(str): 宝可梦的全国图鉴编号或名称（中/日/英均可）
            max_level(int): 只看该等级及以下可学的升级招式（1-100），0 表示不限等级
        """
        row = self.db.pokemon(keyword)
        if not row:
            return f"没有查到「{keyword}」的相关信息"
        return self._learnset_text(row, max(0, min(100, int(max_level))) or None)

    @filter.llm_tool(name="validate_pokemon_moveset")
    async def tool_validate_moveset(
        self, event: AstrMessageEvent, keyword: str, moves: list[str], level: int = 50
    ):
        """校验宝可梦能否学会给定招式（最多 4 个）。非法招式会给出相近名称建议。
        组队或讨论配招前先调用本工具核实，避免编造招式。
        Args:
            keyword(str): 宝可梦的全国图鉴编号或名称
            moves(list[str]): 招式中文名列表，如 ["十万伏特", "电磁波"]
            level(int): 宝可梦等级（1-100），默认 50
        """
        result = self.db.validate_moveset(
            keyword, list(moves), max(1, min(100, int(level)))
        )
        if "error" in result:
            return result["error"]
        lines = [f"【{result['pokemon']} Lv.{result['level']} 配招校验】"]
        if result["valid"]:
            lines.append("合法：" + "、".join(result["valid"]))
        if result["egg_only"]:
            lines.append("合法但需遗传获得：" + "、".join(result["egg_only"]))
        if result["invalid"]:
            lines.append("学不会：" + "、".join(result["invalid"]))
            if result.get("suggestions"):
                lines.append("相近招式：" + "、".join(result["suggestions"]))
        lines.append(
            "结论："
            + ("配招全部合法" if result["is_valid"] else "配招包含学不会的招式")
        )
        return "\n".join(lines)

    # ---------- Phase 3 LLM 工具：对战模拟 ----------
    @filter.llm_tool(name="simulate_battle")
    async def tool_battle(
        self, event: AstrMessageEvent, name1: str, name2: str, level: int = 50, seed: int = -1, detailed: bool = False
    ):
        """模拟两只宝可梦的对战，列出配招、实际模拟效果、伤害与逐回合战报。
        支持优先度、能力等级、状态、回复/吸血/反伤、守住、蓄力和部分普通特性免疫。
        未覆盖图鉴效果会明确标注；不含道具、天气、场地与换人。
        Args:
            name1(str): 第一只宝可梦的编号或名称
            name2(str): 第二只宝可梦的编号或名称
            level(int): 双方等级（1-100），默认 50
            seed(int): 复盘种子，-1 为随机；战报显示实际种子
            detailed(bool): 是否返回完整逐回合事件，默认预览长对局
        """
        row1, row2 = self.db.pokemon(name1), self.db.pokemon(name2)
        if not row1 or not row2:
            return f"没有查到「{name1 if not row1 else name2}」的相关信息"
        return await asyncio.to_thread(self._battle_text, row1, row2, max(1, min(100, int(level))),
                                       seed=None if seed < 0 else seed, detailed=detailed)

    # ---------- Phase 4 LLM 工具：队伍分析 / 种族值筛选 ----------
    @filter.llm_tool(name="analyze_team")
    async def tool_team(self, event: AstrMessageEvent, members: list[str]):
        """分析宝可梦队伍（2-6 只）：各成员弱点、共同弱点、全队都不怕的属性、属性覆盖。
        适用于"帮我看看这支队伍有什么问题""这几只组队怕什么"。
        Args:
            members(list[str]): 队伍成员名称列表（2-6 个），如 ["皮卡丘", "喷火龙"]
        """
        names = [m.strip() for m in members if m.strip()][:6]
        if len(names) < 2:
            return "请提供至少 2 只宝可梦"
        return self._team_text(self.db.analyze_team(names))

    @filter.llm_tool(name="find_pokemon_by_stats")
    async def tool_by_stats(
        self,
        event: AstrMessageEvent,
        min_total: int = 0,
        hp_min: int = 0,
        atk_min: int = 0,
        def_min: int = 0,
        spa_min: int = 0,
        spd_min: int = 0,
        spe_min: int = 0,
        type_name: str = "",
        limit: int = 10,
    ):
        """按种族值条件筛选宝可梦（默认形态，按种族总和降序）。适用于组队时找
        "速度超过100的宝可梦""特攻高且耐打的宝可梦"这类需求。
        Args:
            min_total(int): 种族值总和下限，0 表示不限
            hp_min(int): HP 种族值下限，0 表示不限
            atk_min(int): 攻击种族值下限，0 表示不限
            def_min(int): 防御种族值下限，0 表示不限
            spa_min(int): 特攻种族值下限，0 表示不限
            spd_min(int): 特防种族值下限，0 表示不限
            spe_min(int): 速度种族值下限，0 表示不限
            type_name(str): 限定属性（中文，如 龙），可留空
            limit(int): 返回数量上限（1-20），默认 10
        """
        conds = []
        if int(min_total) > 0:
            conds.append(("总和", ">=", int(min_total)))
        for label, val in (
            ("HP", hp_min),
            ("攻击", atk_min),
            ("防御", def_min),
            ("特攻", spa_min),
            ("特防", spd_min),
            ("速度", spe_min),
        ):
            if int(val) > 0:
                conds.append((label, ">=", int(val)))
        if not conds:
            return "请至少提供一个数值条件（如 min_total 或 spe_min）"
        hits = self.db.by_stats(conds, limit=max(1, min(20, int(limit))))
        if type_name:
            hits = [h for h in hits if type_name in (h["types"] or "")]
        if not hits:
            return "没有符合条件的宝可梦"
        cond_text = " ".join(f"{l}>={v}" for l, _, v in conds)
        lines = [f"符合条件的宝可梦（{cond_text}，按总和降序）："]
        lines += [
            f"{i}. {r['name']}（{r['types']}）总和{r['total']}"
            for i, r in enumerate(hits, 1)
        ]
        return "\n".join(lines)
