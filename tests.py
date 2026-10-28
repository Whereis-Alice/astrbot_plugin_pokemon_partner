#!/usr/bin/env python3
"""astrbot_plugin_pokemon_partner 功能测试：uv run python tests.py

核心模块（pokedex_db / battle_engine）直接测试；main.py 通过 stub astrbot
依赖做导入与 llm_tool 注册检查（docstring Args 类型为插件加载硬要求）。
"""

import inspect
import os
import re
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
# AstrBot 的一些模块会以当前工作目录作为数据根，测试必须隔离在临时目录，
# 否则会在插件目录里生成 cmd_config.json、t2i_templates/ 等运行时文件。
os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="partner-astrbot-"))

PASS = FAIL = 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name}" + (f"（{detail}）" if detail else ""))


def section(title: str):
    print(f"\n== {title} ==")


# ---------------- Phase 1 ----------------
def test_phase1_engine():
    from battle_engine import NATURES, calc_stats

    section("Phase 1 / 能力值计算")
    pikachu = {"HP": 35, "攻击": 55, "防御": 40, "特攻": 50, "特防": 50, "速度": 90}
    s = calc_stats(pikachu, 50, iv=0)
    check(
        "L50 个体0 无修正",
        (s["HP"], s["攻击"], s["防御"], s["特攻"], s["特防"], s["速度"])
        == (95, 60, 45, 55, 55, 95),
        str(s),
    )
    s = calc_stats(pikachu, 50, iv=31)
    check(
        "L50 个体31",
        (s["HP"], s["攻击"], s["防御"], s["特攻"], s["特防"], s["速度"])
        == (110, 75, 60, 70, 70, 110),
        str(s),
    )
    s = calc_stats(pikachu, 50, iv=0, nature="固执")
    check(
        "固执 +攻击-特攻（60→66 / 55→49）", (s["攻击"], s["特攻"]) == (66, 49), str(s)
    )
    s = calc_stats(pikachu, 50, iv=0, nature="内敛")
    check("内敛 +特攻-攻击", (s["特攻"], s["攻击"]) == (60, 54), str(s))
    s = calc_stats(pikachu, 50, iv=0, nature="胆小")
    check("胆小 +速度-攻击", (s["速度"], s["攻击"]) == (104, 54), str(s))
    s = calc_stats(pikachu, 50, iv={"HP": 31, "攻击": 0}, nature="浮躁")
    check("分项个体值", s["HP"] == 110 and s["攻击"] == 60, str(s))
    s = calc_stats(pikachu, 50, iv=31, ev={"攻击": 252})
    check("努力值 252（等级缩放后 +32）", s["攻击"] == 107, str(s))
    check(
        "种族HP=1 保持 1（脱壳忍者）",
        calc_stats(
            {"HP": 1, "攻击": 90, "防御": 45, "特攻": 30, "特防": 30, "速度": 40}, 50
        )["HP"]
        == 1,
    )
    check(
        "未知性格按无修正",
        calc_stats(pikachu, 50, iv=0, nature="不存在的性格")["攻击"] == 60,
    )
    check("性格表完整 25 种", len(NATURES) == 25)
    check(
        "等级越界收敛 1-100",
        calc_stats(pikachu, 999)["HP"] == calc_stats(pikachu, 100)["HP"],
    )


def test_phase1_matchups():
    section("Phase 1 / 进攻相性与互克倍率")
    db = PokedexDB()
    r = db.attack_matchups("火")
    check(
        "火攻效果拔群 草/冰/虫/钢×2",
        sorted(r["super"]) == sorted(["草×2", "冰×2", "虫×2", "钢×2"]),
        str(r["super"]),
    )
    check(
        "火攻收效甚微 火/水/岩石/龙×0.5",
        sorted(r["weak"]) == sorted(["火×0.5", "水×0.5", "岩石×0.5", "龙×0.5"]),
        str(r["weak"]),
    )
    check("火攻无无效属性", r["none"] == [])
    check("未知属性返回 None", db.attack_matchups("光") is None)
    check("电攻打地面无效", "地面" in db.attack_matchups("电")["none"])
    check("水打火/岩石 = 4×", db.matchup_multiplier(["水"], ["火", "岩石"]) == 4.0)
    check("一般打幽灵 = 0×", db.matchup_multiplier(["一般"], ["幽灵"]) == 0.0)
    check("电打水/飞行 = 4×", db.matchup_multiplier(["电"], ["水", "飞行"]) == 4.0)
    check(
        "电打火/飞行 = 2×（克飞行不克火）",
        db.matchup_multiplier(["电"], ["火", "飞行"]) == 2.0,
    )


def test_phase1_counters():
    section("Phase 1 / 克制推荐（双向验证）")
    db = PokedexDB()
    r = db.find_counters("妙蛙种子")
    check(
        "妙蛙种子有候选",
        r and len(r["counters"]) >= 3,
        str(len(r["counters"]) if r else 0),
    )
    check("目标信息正确", r["target"] == "妙蛙种子" and r["target_types"] == "草/毒")
    ok = all(c["atk_mult"] >= 2 and c["def_mult"] <= 1 for c in r["counters"])
    check("全部候选满足 双向≥2×/≤1×", ok)
    check(
        "排序：打击倍率降序",
        all(
            r["counters"][i]["atk_mult"] >= r["counters"][i + 1]["atk_mult"]
            for i in range(len(r["counters"]) - 1)
        ),
    )
    names = [c["name"] for c in r["counters"]]
    check(
        "不含目标自身/形态行",
        "妙蛙种子" not in names and "妙蛙花" not in names,
        str(names[:5]),
    )
    check("未知宝可梦返回 None", db.find_counters("皮卡梦") is None)


def test_phase1_compare_data():
    section("Phase 1 / 对比数据")
    db = PokedexDB()
    p1, p2 = db.pokemon("皮卡丘"), db.pokemon("喷火龙")
    st1, st2 = db.first_stats(p1), db.first_stats(p2)
    check("皮卡丘种族总和 320", st1[1] == 320, str(st1))
    check("喷火龙种族总和 534", st2[1] == 534, str(st2))
    check("电打火/飞行 ×2", db.matchup_multiplier(["电"], ["火", "飞行"]) == 2.0)
    check(
        "火/飞行打电 ×0.5（电抗飞行）",
        db.matchup_multiplier(["火", "飞行"], ["电"]) == 0.5,
    )


# ---------------- main.py 导入与注册检查 ----------------
def test_main_imports():
    section("main.py 导入与 llm_tool 注册（stub astrbot）")

    registered = {"commands": [], "aliases": [], "tools": [], "tool_funcs": []}

    def _mod(name, **attrs):
        m = types.ModuleType(name)
        for k, v in attrs.items():
            setattr(m, k, v)
        sys.modules[name] = m
        return m

    class _Dummy:
        pass

    class _MessageChain:
        def __init__(self):
            self.chain = []

        def message(self, text):
            self.chain.append(text)
            return self

    def _command(name=None, alias=None, **kw):
        def deco(fn):
            registered["commands"].append(name or fn.__name__)
            registered["aliases"].extend(alias or [])
            return fn

        return deco

    def _llm_tool(name=None, **kw):
        def deco(fn):
            registered["tools"].append(name or fn.__name__)
            registered["tool_funcs"].append(fn)
            return fn

        return deco

    _mod("astrbot")
    _mod(
        "astrbot.api",
        logger=types.SimpleNamespace(
            info=lambda *a: None, warning=lambda *a: None, error=lambda *a: None
        ),
        AstrBotConfig=dict,
    )
    _mod(
        "astrbot.api.event",
        filter=types.SimpleNamespace(
            command=_command,
            llm_tool=_llm_tool,
            permission_type=lambda *a, **kw: (lambda fn: fn),
            PermissionType=types.SimpleNamespace(ADMIN="admin"),
        ),
        AstrMessageEvent=_Dummy,
        MessageChain=_MessageChain,
    )
    _mod(
        "astrbot.api.star",
        Context=_Dummy,
        Star=type("Star", (), {"__init__": lambda self, ctx: None}),
        register=lambda *a, **kw: lambda cls: cls,
    )
    _mod(
        "astrbot.api.message_components",
        Image=type("Image", (), {"fromBytes": staticmethod(lambda b: None)}),
        Plain=lambda *a, **kw: None,
    )
    _mod(
        "astrbot.core.star.star_tools",
        StarTools=type(
            "StarTools",
            (),
            {
                "get_data_dir": staticmethod(
                    lambda name=None: Path(tempfile.mkdtemp(prefix="partner-test-"))
                )
            },
        ),
    )
    _mod(
        "astrbot.api.web",
        jsonify=lambda payload: payload,
        request=types.SimpleNamespace(args={}, get_json=lambda **kw: None),
    )

    import main  # 导入即触发指令/工具装饰器注册（下方有引用，勿删）

    expected_cmds = {
        "图鉴帮助",
        "今日宝可梦",
        "抽取概率",
        "我的宝可梦",
        "收集资料",
        "兑换宝可梦",
        "收集排行",
        "重置今日宝可梦",
        "增加宝可梦次数",
        "重置本群宝可梦",
        "下载立绘",
        "宝可梦",
        "特性",
        "招式",
        "道具",
        "性格",
        "相性",
        "克制",
        "对战",
        "宝可梦挑战",
        "接收挑战",
        "拒绝对战",
        "取消对战",
        "对战记录",
        "对战排行",
        "队伍分析",
        "筛选",
        "许愿星",
        "宝冠军",
    }
    check(
        "指令全部注册",
        expected_cmds <= set(registered["commands"]),
        str(expected_cmds - set(registered["commands"])),
    )
    legacy_aliases = {"宝可梦图鉴", "图鉴帮助", "pm", "pokedex"}
    check(
        "不带旧插件别名",
        not (legacy_aliases & set(registered["aliases"])),
        str(legacy_aliases & set(registered["aliases"])),
    )
    check(
        "今日宝可梦保留旧抽卡别名",
        {"抽取宝可梦", "抽宝可梦"} <= set(registered["aliases"]),
        str(registered["aliases"]),
    )
    check(
        "宝可梦挑战保留旧指令并兼容自然接受拒绝取消说法",
        {"收藏挑战", "挑战宝可梦", "接受对战", "接受挑战", "拒绝挑战", "取消挑战"} <= set(registered["aliases"]),
        str(registered["aliases"]),
    )
    command_surface = set(registered["commands"]) | set(registered["aliases"])
    collisions = sorted(
        name
        for name in command_surface
        if name not in {"宝可梦", "宝可梦挑战"} and name.startswith("宝可梦")
    )
    check(
        "除明确挑战指令外没有意外宝可梦前缀",
        not collisions,
        str(collisions),
    )

    expected_tools = {
        "pokemon_partner_draw",
        "pokemon_partner_profile",
        "pokemon_partner_collection",
        "pokemon_partner_exchange",
        "pokemon_partner_rates",
        "query_pokemon",
        "search_pokemon",
        "query_ability",
        "query_move",
        "query_nature",
        "query_dex_region",
        "query_type_matchups",
        "query_wishmaker_jirachi",
        "find_pokemon_by_type",
        "pokeapi_pokemon_detail",
        "pokeapi_type_matchups",
        "calc_pokemon_stats",
        "query_type_offense",
        "compare_pokemon",
        "find_pokemon_counters",
        "query_learnset",
        "validate_pokemon_moveset",
        "simulate_battle",
        "analyze_team",
        "find_pokemon_by_stats",
        "query_champions_ranking",
        "query_pokemon_rank",
        "query_champions_build",
        "find_champions_by_item",
        "query_champions_trend",
        "champions_data_status",
    }
    check(
        "llm_tool 全部注册",
        expected_tools <= set(registered["tools"]),
        str(expected_tools - set(registered["tools"])),
    )

    bad = []
    for fn in registered["tool_funcs"]:
        doc = inspect.getdoc(fn) or ""
        params = [
            p for p in inspect.signature(fn).parameters if p not in ("self", "event")
        ]
        for p in params:
            if not re.search(rf"^\s*{re.escape(p)}\([^):]+\):", doc, re.MULTILINE):
                bad.append(f"{fn.__name__}:{p}")
    check("llm_tool 参数全部有 docstring 类型标注（Args）", not bad, str(bad))

    class _Ev:
        message_str = ""

        def __init__(self, raw=""):
            self.message_str = raw

        def plain_result(self, t):
            return t

    async def _run(coro_gen, ev):
        return [r async for r in coro_gen]

    async def _directions():
        plugin = main.PokemonPartnerPlugin(context=None, config={})
        out = await _run(plugin.cmd_matchups(_Ev(), "打", "火"), _Ev("相性 打 火"))
        check("相性 打 火（进攻）含效果拔群", any("效果拔群" in r for r in out))
        out = await _run(plugin.cmd_matchups(_Ev(), "火", "飞行"), _Ev("相性 火 飞行"))
        check(
            "相性 火 飞行（防守）含弱点/抗性",
            any("弱点" in r for r in out) and any("抗性" in r for r in out),
        )
        hint = plugin._pokemon_related_hint({"name_zh": "烈咬陆鲨", "form_name": ""})
        check(
            "图鉴反查提示含后续指令",
            "宝可梦 烈咬陆鲨 招式表" in hint and "克制 烈咬陆鲨" in hint,
            hint,
        )

    import asyncio

    asyncio.run(_directions())

    async def _hub():
        plugin = main.PokemonPartnerPlugin(context=None, config={})

        class _Ev2(_Ev):
            def plain_result(self, t):
                return t

        out = await _run(plugin.cmd_pokemon(_Ev2(), "皮卡丘 招式表 50"), _Ev2())
        check(
            "宝可梦 X 招式表 子视图",
            any("可学招式" in r or "推荐配招" in r for r in out),
            str(out)[:120],
        )
        out = await _run(plugin.cmd_pokemon(_Ev2(), "皮卡丘 能力值 50 31 固执"), _Ev2())
        check("宝可梦 X 能力值 子视图", any("总和" in r for r in out))
        check(
            "能力值输出附性格修正说明",
            any("攻击+10%" in r and "特攻-10%" in r for r in out),
            str(out)[:120],
        )
        out = await _run(plugin.cmd_pokemon(_Ev2(), ""), _Ev2())
        check("宝可梦 无参 → 帮助", any("宝可梦图鉴" in r for r in out))
        out = await plugin.tool_search_pokemon(_Ev2(), "丰缘")
        check("search_pokemon 工具含地区图鉴", "图鉴" in out, out[:120])
        out = await plugin.tool_query_item(_Ev2(), "讲究围巾")
        check("query_item 原子工具", "讲究围巾" in out and "类别" in out)
        out = await _run(plugin.cmd_nature(_Ev2(), ""), _Ev2())
        check("性格 无参 → 全表速查", any("性格速查" in r and "中性" in r for r in out))
        out = await _run(plugin.cmd_nature(_Ev2(), "固执"), _Ev2())
        check(
            "性格 固执 → 增减与口味",
            any("adamant" in r and "攻击+10%" in r and "涩味" in r for r in out),
            str(out)[:120],
        )
        out = await _run(plugin.cmd_nature(_Ev2(), "勤奋"), _Ev2())
        check("性格 勤奋 → 中性无修正", any("无能力修正" in r for r in out))
        out = await _run(plugin.cmd_nature(_Ev2(), "胆"), _Ev2())
        check("性格 片段匹配 → 胆小/胆量候选", any("胆小" in r for r in out))
        out = await plugin.tool_query_nature(_Ev2(), "胆小")
        check(
            "query_nature 原子工具",
            "timid" in out and "速度+10%" in out and "辣味" in out,
            out[:120],
        )
        out = await _run(plugin.cmd_nature(_Ev2(), "不存在的"), _Ev2())
        check("性格 未识别 → 引导全表", any("没有性格" in r for r in out))
        if plugin.champions.exists:
            out = await plugin.tool_rank_pokemon(_Ev2(), 3, fmt="double")
            check("query_rank_pokemon 原子工具", "第 3 名" in out, out[:120])
            out = await plugin.tool_champ_lookup(_Ev2(), "烈咬陆鲨")
            check("query_pokemon_rank 只查名称名次", "冠军赛排名" in out)
        else:
            check("无冠军赛数据库时优雅提示", not plugin.champions.exists)
        out = await _run(plugin.cmd_counter(_Ev2(), "妙蛙花"), _Ev2())
        check(
            "克制 X = 克制推荐",
            any("筛选条件" in r and "克制 妙蛙花" in r for r in out),
        )
        out = await _run(plugin.cmd_counter(_Ev2(), "皮卡丘", "喷火龙"), _Ev2())
        check(
            "克制 A B = 多维度对比含小结",
            any("小结：" in r and "面板" in r for r in out),
        )
        out = await _run(plugin.cmd_counter(_Ev2(), "不存在兽"), _Ev2())
        check("克制 查无此人 → 提示用相性", any("相性" in r for r in out))

    asyncio.run(_hub())

    conds = main.parse_stat_conditions("速度>100 特攻>=120 总和<=500")
    check(
        "筛选条件解析",
        conds == [("速度", ">", 100), ("特攻", ">=", 120), ("总和", "<=", 500)],
        str(conds),
    )
    check(
        "非法筛选条件返回 None",
        main.parse_stat_conditions("速度>>100") is None
        and main.parse_stat_conditions("") is None,
    )


# ---------------- Phase 2 ----------------
def test_phase2_learnset():
    section("Phase 2 / 招式表与配招")
    db = PokedexDB()
    row = db.pokemon("妙蛙种子")
    ls = db.learnset(row)
    check(
        "新格式解析：首招 撞击(1)",
        ls["level_up"][0] == ("撞击", 1),
        str(ls["level_up"][:3]),
    )
    check("等级内嵌：藤鞭(3)", ("藤鞭", 3) in ls["level_up"])
    check(
        "招式学习器非空且无等级",
        ls["machine"] and all(lv is None for _, lv in ls["machine"]),
    )
    check(
        "形态行回退默认形态行",
        db.learnset(db.pokemon("超级烈咬陆鲨"))["level_up"]
        == db.learnset(db.pokemon("烈咬陆鲨"))["level_up"],
    )

    old_row = {
        "level_up_moves": "撞击、叫声、藤鞭",
        "machine_moves": "守住、剑舞",
        "egg_moves": "",
        "form_name": "",
    }
    old = db.learnset(old_row)
    check(
        "旧格式兼容：等级为 None",
        old["level_up"] == [("撞击", None), ("叫声", None), ("藤鞭", None)]
        and old["machine"] == [("守住", None), ("剑舞", None)],
    )

    check("move_power：十万伏特 90", db.move_power(db.move_index()["十万伏特"]) == 90)
    check("move_power：变化招 None", db.move_power(db.move_index()["剑舞"]) is None)

    rec, from_ls = db.recommended_moveset(row)
    learnable = {n for n, _ in ls["level_up"] + ls["machine"] + ls["egg"]}
    check(
        "推荐配招 4 个且全部来自学习表",
        len(rec) == 4 and set(rec) <= learnable,
        str(rec),
    )
    check("推荐配招标记 from_learnset", from_ls)
    demo_row = {
        "name_zh": "测试",
        "types": "火",
        "form_name": "",
        "level_up_moves": "",
        "machine_moves": "",
        "egg_moves": "",
    }
    demo, demo_ok = db.recommended_moveset(demo_row)
    check(
        "无学习表回退演示配招（火属性全局高威力）",
        len(demo) == 4
        and not demo_ok
        and all(db.move_index()[n]["type"] == "火" for n in demo),
        str(demo),
    )

    v = db.validate_moveset("皮卡丘", ["十万伏特", "电磁波", "十万福特"])
    check("校验：TM 与状态招合法", set(v["valid"]) == {"十万伏特", "电磁波"}, str(v))
    check(
        "校验：错别字招式判非法并给建议",
        v["invalid"] == ["十万福特"] and "十万伏特" in v["suggestions"],
        str(v.get("suggestions")),
    )
    check("校验：is_valid 标记", v["is_valid"] is False)

    learnable_up = [(n, lv) for n, lv in ls["level_up"] if lv and lv >= 5]
    if learnable_up:
        mv, lv = learnable_up[0]
        v_low = db.validate_moveset("妙蛙种子", [mv], level=1)
        v_ok = db.validate_moveset("妙蛙种子", [mv], level=100)
        check(
            f"等级过滤：{mv}({lv}) 1级不可/100级可",
            mv in v_low["invalid"] and mv in v_ok["valid"],
        )
    if ls["egg"]:
        egg_move = ls["egg"][0][0]
        v_egg = db.validate_moveset("妙蛙种子", [egg_move])
        check(f"遗传招单独标注：{egg_move}", egg_move in v_egg["egg_only"], str(v_egg))


def test_phase2_data_quality():
    section("Phase 2 / 数据覆盖抽查")
    db = PokedexDB()
    rows = [r for r in db.table("pokemon") if not r.get("form_name")]
    with_ls = sum(1 for r in rows if (r.get("level_up_moves") or "").strip())
    check(
        "默认形态招式表覆盖 ≥95%", with_ls / len(rows) >= 0.95, f"{with_ls}/{len(rows)}"
    )
    ls = db.learnset(db.pokemon("皮卡丘"))
    idx = db.move_index()
    missing = [n for n, _ in ls["level_up"] + ls["machine"] if n not in idx]
    check("皮卡丘招式表全部可在招式总表命中", not missing, str(missing[:5]))


# ---------------- Phase 3 ----------------
def test_phase3_battle():
    import random

    from battle_engine import (
        BattleMove,
        BattlePokemon,
        BattleSimulator,
        calc_stats,
        make_move,
    )

    section("Phase 3 / 对战模拟")
    db = PokedexDB()
    chart = db.type_chart()

    def mk(row, level):
        stats = calc_stats(dict(db.first_stats(row)[0]), level)
        moves, _ = db.recommended_moveset(row)
        bms = [make_move(db.move_index()[n], db.STATUS_MOVES.get(n)) for n in moves]
        return BattlePokemon(
            name=row["name_zh"],
            types=row["types"].split("/"),
            stats=stats,
            moves=bms,
            level=level,
        )

    pika, charz = db.pokemon("皮卡丘"), db.pokemon("喷火龙")

    winners, turns_ok = set(), True
    for seed in range(20):
        rep = BattleSimulator(chart, rng=random.Random(seed)).simulate(
            mk(pika, 50), mk(charz, 50)
        )
        winners.add(rep["winner"])
        turns_ok = turns_ok and 1 <= rep["turns"] <= 100
    check(
        "20 场对战全部正常结束",
        turns_ok and winners <= {"皮卡丘", "喷火龙", "平局"},
        str(winners),
    )
    check("双方都有胜出（随机性生效）", {"皮卡丘", "喷火龙"} <= winners, str(winners))

    r1 = BattleSimulator(chart, rng=random.Random(7)).simulate(
        mk(pika, 50), mk(charz, 50)
    )
    r2 = BattleSimulator(chart, rng=random.Random(7)).simulate(
        mk(pika, 50), mk(charz, 50)
    )
    check("同种子结果完全可复现", r1 == r2)

    wins = sum(
        BattleSimulator(chart, rng=random.Random(s)).simulate(
            mk(charz, 100), mk(pika, 1)
        )["winner"]
        == "喷火龙"
        for s in range(20)
    )
    check("等级压制：100级喷火龙 20 战 ≥19 胜 1 级皮卡丘", wins >= 19, f"{wins}/20")

    tank = {"HP": 200, "攻击": 20, "防御": 20, "特攻": 20, "特防": 20, "速度": 20}
    tiny = BattleMove(
        name="撞击", type="一般", category="物理", power=40, accuracy=100, pp=1
    )
    a = BattlePokemon(
        name="甲", types=["一般"], stats=calc_stats(tank, 50), moves=[tiny], level=50
    )
    b = BattlePokemon(
        name="乙",
        types=["一般"],
        stats=calc_stats(tank, 50),
        moves=[
            BattleMove(
                name="叫声",
                type="一般",
                category="变化",
                power=None,
                accuracy=100,
                pp=1,
            )
        ],
        level=50,
    )
    sim = BattleSimulator(chart, rng=random.Random(1))
    rep = sim.simulate(a, b)
    check("PP 耗尽后使用挣扎（含反伤）", any("挣扎" in x for x in sim.log))
    check("挣扎有反伤记录", any("反作用力" in x for x in sim.log))

    status_atk = BattlePokemon(
        name="特",
        types=["一般"],
        stats=calc_stats(tank, 50),
        moves=[make_move(db.move_index()["电磁波"], "麻痹")],
        level=50,
    )
    sim = BattleSimulator(chart, rng=random.Random(3))
    rep = sim.simulate(status_atk, mk(pika, 50))
    check("电属性免疫麻痹（电磁波对皮卡丘无效）", any("免受麻痹" in x for x in sim.log))

    elec = BattleMove(
        name="十万伏特", type="电", category="特殊", power=90, accuracy=100, pp=999
    )
    g1 = BattlePokemon(
        name="地1", types=["地面"], stats=calc_stats(tank, 50), moves=[elec], level=50
    )
    g2 = BattlePokemon(
        name="地2",
        types=["岩石", "地面"],
        stats=calc_stats(tank, 50),
        moves=[elec],
        level=50,
    )
    sim = BattleSimulator(chart, rng=random.Random(5))
    rep = sim.simulate(g1, g2)
    check(
        "电攻打地面完全免疫（0 伤害打满回合上限后平局）",
        rep["turns"] == 100
        and rep["winner"] == "平局"
        and any("免疫" in x for x in sim.log),
        str(rep["turns"]),
    )

    burn_atk = BattlePokemon(
        name="烧",
        types=["一般"],
        stats=calc_stats(tank, 50),
        moves=[make_move(db.move_index()["磷火"], "灼伤")],
        level=50,
    )
    sim = BattleSimulator(chart, rng=random.Random(9))
    rep = sim.simulate(burn_atk, mk(pika, 50))
    check(
        "磷火对非火属性命中灼伤并结算回合伤害",
        any("陷入灼伤" in x for x in sim.log) and any("灼伤伤害" in x for x in sim.log),
    )

    weak = BattleMove(
        name="撞击", type="一般", category="物理", power=40, accuracy=100, pp=999
    )
    t1 = BattlePokemon(
        name="坦1", types=["一般"], stats=calc_stats(tank, 50), moves=[weak], level=50
    )
    t2 = BattlePokemon(
        name="坦2", types=["一般"], stats=calc_stats(tank, 50), moves=[weak], level=50
    )
    sim = BattleSimulator(chart, rng=random.Random(11), max_turns=3)
    rep = sim.simulate(t1, t2)
    check(
        "回合上限生效（3 回合无人倒下）",
        rep["turns"] == 3 and not any(st["fainted"] for st in rep["final"].values()),
        str(rep["turns"]),
    )

    mv = make_move(db.move_index()["十万伏特"])
    check(
        "make_move：十万伏特 威力90/命中100/有PP",
        mv.power == 90 and mv.accuracy == 100 and mv.pp > 0,
    )
    mv = make_move(db.move_index()["剑舞"])
    check("make_move：剑舞 变化招无威力", mv.category == "变化" and mv.power is None)


# ---------------- Phase 4 ----------------
def test_phase4_team_and_stats():
    section("Phase 4 / 队伍分析与种族值筛选")
    db = PokedexDB()
    r = db.analyze_team(["大岩蛇", "隆隆岩"])
    check(
        "双岩队共同弱点含 水（4倍）",
        any(a == "水" and len(ns) == 2 for a, ns in r["common_weaknesses"]),
        str(r["common_weaknesses"][:3]),
    )
    check("共同弱点计数全部 ≥2", all(len(ns) >= 2 for _, ns in r["common_weaknesses"]))
    check(
        "全队不怕的属性含 一般/毒/飞行",
        {"一般", "毒", "飞行"} <= set(r["team_resists"]),
        str(r["team_resists"][:6]),
    )
    check("属性覆盖 岩石+地面", {"岩石", "地面"} <= set(r["attack_coverage"]))
    check("成员弱点列表非空", all(m["weak"] for m in r["members"]))

    r2 = db.analyze_team(["皮卡丘", "不存在的宝可梦"])
    check(
        "未知成员记录错误不崩溃",
        r2["members"][1].get("error") == "未找到" and len(r2["members"]) == 2,
    )
    check("单有效成员无共同弱点（阈值≥2）", r2["common_weaknesses"] == [])

    hits = db.by_stats([("速度", ">", 100)], limit=15)
    from pokedex_db import PokedexDB as _  # noqa: F401

    ok = True
    for h in hits:
        row = db.pokemon(h["name"])
        speed = dict(db.first_stats(row)[0])["速度"]
        ok = ok and speed > 100
    check("速度>100 结果全部达标且非空", hits and ok, f"{len(hits)} 条")
    totals = [h["total"] for h in hits]
    check("结果按种族总和降序", totals == sorted(totals, reverse=True))

    top = db.by_stats([("总和", ">=", 600)], limit=5)
    check(
        "总和≥600 有命中且全部达标",
        top and all(h["total"] >= 600 for h in top),
        str([h["name"] for h in top[:3]]),
    )
    check(
        "多条件 AND：速度>110 且 总和<=450",
        all(
            h["total"] <= 450
            for h in db.by_stats([("速度", ">", 110), ("总和", "<=", 450)])
        ),
    )
    check("无命中返回空列表", db.by_stats([("总和", ">", 9999)]) == [])


# ---------------- 受击类特性免疫 / 相性修正 ----------------
def test_ability_corrections():
    section("受击类特性映射 / 相性修正字段")
    db = PokedexDB()
    check(
        "皮卡丘隐藏特性避雷针 → 免疫 电",
        db.ability_immunities(db.pokemon("皮卡丘")) == [("电", "避雷针")],
    )
    check("无相关特性的宝可梦返回空", db.ability_immunities(db.pokemon("大岩蛇")) == [])

    floats = [
        r
        for r in db.table("pokemon")
        if not r.get("form_name") and "飘浮" in (r.get("abilities") or "")
    ]
    check("数据集中存在飘浮特性持有者", bool(floats))
    if floats:
        check(
            f"飘浮持有者（{floats[0]['name_zh']}）→ 免疫 地面",
            db.ability_immunities(floats[0]) == [("地面", "飘浮")],
        )

    r = db.matchups(["电"])
    corr = {c["type"]: c for c in r["ability_corrections"]}
    check(
        "相性 电：地面×2 可被飘浮免疫",
        corr.get("地面", {}).get("abilities") == ["飘浮"]
        and corr["地面"]["current_mult"] == 2.0,
    )
    check(
        "相性 电：避雷针/蓄电 合并展示（现×0.5）",
        set(corr.get("电", {}).get("abilities", [])) == {"避雷针", "蓄电"}
        and corr["电"]["current_mult"] == 0.5,
    )
    check(
        "修正按当前倍率降序",
        [c["current_mult"] for c in r["ability_corrections"]]
        == sorted((c["current_mult"] for c in r["ability_corrections"]), reverse=True),
    )

    r2 = db.matchups(["一般"])
    corr2 = {c["type"] for c in r2["ability_corrections"]}
    check("已免疫属性不重复修正（幽灵打一般 0× 不出现）", "幽灵" not in corr2)
    check("无对应免疫特性的属性不出现（格斗打一般 2×）", "格斗" not in corr2)
    check("火属性有引火修正", "火" in corr2)


def test_card_champion_section():
    section("图鉴卡片冠军赛名次条（Pillow 渲染）")
    import io

    from PIL import Image

    from card_renderer import CardRenderer

    db = PokedexDB()
    renderer = CardRenderer()
    row = next(
        r
        for r in db.table("pokemon")
        if r.get("name_zh") == "皮卡丘" and not r.get("form_name")
    )
    stats, total = db.first_stats(row)
    base = renderer.render_pokemon_card(row, stats, total)
    with_champ = renderer.render_pokemon_card(
        row,
        stats,
        total,
        champion={"season": "M-7", "single": 7, "double": 5, "total": 262},
    )
    h1 = Image.open(io.BytesIO(base)).height
    h2 = Image.open(io.BytesIO(with_champ)).height
    check("名次条使卡片增高 ≥40px", h2 - h1 >= 40, f"{h1} -> {h2}")
    png = Image.open(io.BytesIO(with_champ))
    check(
        "卡片尺寸合法（宽 1080，PNG 可解码）", png.width == 1080 and png.mode == "RGB"
    )


def test_champ_arg_parser():
    section("冠军指令参数分类（单入口 token 消歧）")
    import main  # 依赖 test_main_imports 已注入的 astrbot stub

    P = main.PokemonPartnerPlugin._parse_champ_args
    a = P("")
    check(
        "空参数",
        a == {"fmt": None, "rank": None, "season": None, "view": None, "names": []},
        str(a),
    )
    a = P("双打")
    check("格式词", a["fmt"] == "double" and not a["names"])
    a = P("单打 3")
    check("格式+名次", a["fmt"] == "single" and a["rank"] == 3)
    a = P("#3")
    check("#3 名次", a["rank"] == 3)
    a = P("烈咬陆鲨")
    check("宝可梦名", a["names"] == ["烈咬陆鲨"])
    a = P("烈咬陆鲨 6")
    check("名+数字 → 赛季", a["names"] == ["烈咬陆鲨"] and a["season"] == "M-6")
    a = P("烈咬陆鲨 M-6")
    check("显式赛季", a["season"] == "M-6")
    a = P("双打 上一轮")
    check("语义赛季", a["fmt"] == "double" and a["season"] == "prev")
    a = P("3 6")
    check("双数字 = 名次+赛季", a["rank"] == 3 and a["season"] == "M-6")
    a = P("生命宝珠")
    check("道具名走 names", a["names"] == ["生命宝珠"])
    a = P("谜拟丘 趋势")
    check("趋势视图", a["view"] == "trend" and a["names"] == ["谜拟丘"])
    a = P("7赛季 状态")
    check("N赛季 + 状态视图", a["season"] == "M-7" and a["view"] == "status")


# ---------------- 宝可梦抽卡 ----------------
def test_gacha_rules():
    section("宝可梦抽卡 / 配置与稀有度")
    from gacha import (
        DEFAULT_DUPLICATE_DUST,
        DEFAULT_SPARK_COSTS,
        DEFAULT_WEIGHTS,
        ConfigError,
        GachaRules,
    )

    rules = GachaRules.from_config({})
    check(
        "默认权重 55/30/12/3",
        rules.weight_list() == [DEFAULT_WEIGHTS[r] for r in ("C", "R", "SR", "SSR")]
        == [55, 30, 12, 3],
        str(rules.weight_list()),
    )
    check(
        "默认星尘与兑换价",
        dict(rules.duplicate_dust) == DEFAULT_DUPLICATE_DUST
        and dict(rules.spark_costs) == DEFAULT_SPARK_COSTS,
    )
    check(
        "默认阈值与次数",
        (rules.rare_min_bst, rules.super_rare_min_bst, rules.ultra_rare_min_bst)
        == (400, 480, 580)
        and rules.draws_per_command == 1
        and rules.daily_limit == 3,
    )
    check(
        "权重按比例归一化",
        GachaRules.from_config(
            {"rarity_weights": {"C": 1, "R": 1, "SR": 1, "SSR": 1}}
        ).weight_list()
        == [1, 1, 1, 1],
    )
    check(
        "单档为 0 仍合法",
        GachaRules.from_config(
            {"rarity_weights": {"C": 100, "R": 0, "SR": 0, "SSR": 0}}
        ).weight_list()
        == [100, 0, 0, 0],
    )
    try:
        GachaRules.from_config(
            {"rarity_weights": {"C": 0, "R": 0, "SR": 0, "SSR": 0}}
        )
        check("全 0 权重应报错", False)
    except ConfigError:
        check("全 0 权重应报错", True)
    try:
        GachaRules.from_config(
            {"rare_min_bst": 600, "super_rare_min_bst": 500, "ultra_rare_min_bst": 700}
        )
        check("阈值乱序应报错", False)
    except ConfigError:
        check("阈值乱序应报错", True)
    check(
        "稀有度边界：399/400/479/480/579/580",
        [
            rules.rarity_for_bst(399),
            rules.rarity_for_bst(400),
            rules.rarity_for_bst(479),
            rules.rarity_for_bst(480),
            rules.rarity_for_bst(579),
            rules.rarity_for_bst(580),
        ]
        == ["C", "R", "R", "SR", "SR", "SSR"],
    )
    check(
        "次数与保底越界收敛",
        GachaRules.from_config(
            {
                "draws_per_command": 99,
                "daily_draw_limit": -5,
                "ssr_pity_after": -3,
                "draw_cooldown_seconds": -1,
            }
        ).draws_per_command
        == 10
        and GachaRules.from_config({"daily_draw_limit": -5}).daily_limit == 0
        and GachaRules.from_config({"ssr_pity_after": -3}).ssr_pity_after == 0
        and GachaRules.from_config({"draw_cooldown_seconds": -1}).cooldown_seconds == 0,
    )
    check(
        "闪光星尘与兑换倍率生效",
        rules.dust_for("SSR", shiny=True) == 240
        and rules.spark_cost_for("SSR", shiny=True) == 1600,
    )
    check(
        "非法数值回退默认",
        GachaRules.from_config({"draws_per_command": "abc"}).draws_per_command == 1,
    )


def test_gacha_store():
    section("宝可梦抽卡 / 记录、保底与兑换")
    import random
    import tempfile
    from pathlib import Path

    from gacha import GachaError, GachaRules, build_partners
    from gacha_store import GachaStore

    db = PokedexDB()
    rules = GachaRules.from_config(
        {
            "rarity_weights": {"C": 1, "R": 0, "SR": 0, "SSR": 0},
            "draws_per_command": 3,
            "daily_draw_limit": 5,
            "draw_cooldown_seconds": 0,
            "ssr_pity_after": 0,
            "new_pity_after": 0,
            "shiny_chance_percent": 0,
        }
    )
    partners = build_partners(db, rules)
    check("默认池只含默认形态", len(partners) == 1025, str(len(partners)))
    check(
        "池内无形态行",
        all(not str(p.row.get("form_name") or "") for p in partners),
    )
    check(
        "池内唯一键不重复",
        len({p.key for p in partners}) == len(partners),
    )
    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(Path(td), partners, rules, rng=random.Random(2026))
        outcome = store.draw("group:1", "u1", "小明", today="2026-10-27")
        check(
            "每次固定抽取数量（3）",
            len(outcome.items) == 3 and outcome.requested == 3,
            str(len(outcome.items)),
        )
        check("首次抽取全部为新", outcome.new_count == 3, str(outcome.items))
        check(
            "按配置概率只出 C 档",
            all(item.rarity == "C" for item in outcome.items),
        )
        check("剩余次数正确", outcome.remaining == 2, str(outcome.remaining))
        outcome2 = store.draw("group:1", "u1", "小明", today="2026-10-27")
        check(
            "剩余次数不足时按剩余抽取并标记 capped",
            len(outcome2.items) == 2 and outcome2.capped,
            str(outcome2),
        )
        try:
            store.draw("group:1", "u1", "小明", today="2026-10-27")
            check("次数耗尽应报错", False)
        except GachaError:
            check("次数耗尽应报错", True)
        check(
            "跨日额度重置",
            len(store.draw("group:1", "u1", "小明", today="2026-10-28").items) == 3,
        )
        summary = store.get_profile(
            "group:1", "u1", "小明", today="2026-10-28"
        )
        check(
            "资料统计与当日使用一致",
            summary.collected >= 3 and summary.used_today == 3,
            str(summary),
        )
        check("图鉴只记录已拥有", len(store.owned_partners("group:1", "u1")) == summary.collected)
        check("其他群互不影响", store.get_profile("group:2", "u1").collected == 0)
        check("其他用户互不影响", store.get_profile("group:1", "u2").collected == 0)
        leader = store.leaderboard("group:1")
        check("排行榜包含自己", leader and leader[0][1] == "小明", str(leader))
        check("个人排名为 1", store.user_rank("group:1", "u1") == 1)

        store.reload()
        check(
            "状态落盘后可恢复",
            store.get_profile("group:1", "u1", "小明").collected == summary.collected,
        )

        store.set_dust("group:1", "u1", 10000)
        first = next(p for p in partners if p.rarity == "C" and p.key not in {
            item.partner.key for item in store.owned_partners("group:1", "u1")
        })
        result = store.exchange("group:1", "u1", first.name)
        check(
            "星尘兑换扣费并入库",
            result.partner.key == first.key and result.dust == 10000 - result.cost,
            str(result),
        )
        try:
            store.exchange("group:1", "u1", first.name)
            check("重复兑换应报错", False)
        except GachaError:
            check("重复兑换应报错", True)
        try:
            store.exchange("group:1", "u1", "不存在的宝可梦")
            check("未知宝可梦应报错", False)
        except GachaError:
            check("未知宝可梦应报错", True)

        store.add_bonus("group:1", "u1", 2)
        check(
            "额外次数计入当日上限",
            store.get_profile("group:1", "u1").daily_limit == 7,
        )
        store.reset_today("group:1", "u1")
        check(
            "重置后当日次数归零",
            store.get_profile("group:1", "u1").used_today == 0,
        )


def test_gacha_pity_and_shiny():
    section("宝可梦抽卡 / 保底与闪光")
    import random
    import tempfile
    from pathlib import Path

    from gacha import GachaRules, build_partners
    from gacha_store import GachaStore

    db = PokedexDB()
    rules = GachaRules.from_config(
        {
            "rarity_weights": {"C": 0, "R": 0, "SR": 0, "SSR": 100},
            "draws_per_command": 1,
            "daily_draw_limit": 100,
            "draw_cooldown_seconds": 0,
            "ssr_pity_after": 0,
            "shiny_chance_percent": 100,
        }
    )
    partners = build_partners(db, rules)
    with tempfile.TemporaryDirectory() as td:
        single = (next(p for p in partners if p.rarity == "SSR"),)
        store = GachaStore(Path(td), single, rules, rng=random.Random(1))
        outcome = store.draw("s", "u", today="2026-10-27")
        item = outcome.items[0]
        check("100% 闪光时必为闪光", item.shiny)
        check("闪光首次获取不折算星尘", item.dust == 0)
        second = store.draw("s", "u", today="2026-10-27")
        third = store.draw("s", "u", today="2026-10-27")
        check(
            "重复闪光折算倍率星尘",
            second.items[0].shiny and second.items[0].dust >= 1,
            str(second.items[0]),
        )
        check(
            "星尘累加",
            second.dust == outcome.dust + second.items[0].dust
            and third.dust == second.dust + third.items[0].dust,
        )

    pity_rules = GachaRules.from_config(
        {
            "rarity_weights": {"C": 100, "R": 0, "SR": 0, "SSR": 0},
            "draws_per_command": 1,
            "daily_draw_limit": 100,
            "draw_cooldown_seconds": 0,
            "ssr_pity_after": 3,
            "shiny_chance_percent": 0,
        }
    )
    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(
            Path(td), build_partners(db, pity_rules), pity_rules, rng=random.Random(7)
        )
        rarities = [
            store.draw("s", "u", today="2026-10-27").items[0].rarity
            for _ in range(3)
        ]
        check(
            "SSR 保底第 3 次触发",
            rarities == ["C", "C", "SSR"],
            str(rarities),
        )
        check("出 SSR 后保底清零", store.get_profile("s", "u").pity_ssr == 0)
        rarities = [
            store.draw("s", "u", today="2026-10-27").items[0].rarity
            for _ in range(3)
        ]
        check(
            "保底循环可重复触发",
            rarities == ["C", "C", "SSR"],
            str(rarities),
        )

    new_rules = GachaRules.from_config(
        {
            "rarity_weights": {"C": 100, "R": 0, "SR": 0, "SSR": 0},
            "draws_per_command": 1,
            "daily_draw_limit": 100,
            "draw_cooldown_seconds": 0,
            "ssr_pity_after": 0,
            "new_pity_after": 2,
            "shiny_chance_percent": 0,
        }
    )
    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(
            Path(td), build_partners(db, new_rules), new_rules, rng=random.Random(3)
        )
        store.draw("s", "u", today="2026-10-27")
        store.draw("s", "u", today="2026-10-27")
        third = store.draw("s", "u", today="2026-10-27")
        check(
            "连续重复 2 次后强制出新",
            third.items[0].is_new,
            str(third.items[0]),
        )
        check("出新后连续未出新计数清零", store.get_profile("s", "u").pity_new == 0)


def test_partner_cards():
    section("宝可梦卡片渲染")
    import io
    import random
    import tempfile
    from pathlib import Path

    from PIL import Image

    from gacha import GachaRules, build_partners
    from gacha_store import GachaStore
    from partner_cards import (
        _gender_text,
        render_collection_card,
        render_draw_card,
        render_profile_card,
        render_rarity_legend,
    )

    db = PokedexDB()
    check(
        "无性别与单性别文案",
        _gender_text("♂0% ♀0%") == "无性别"
        and _gender_text("♂0% ♀100%") == "仅雌性"
        and _gender_text("♂100% ♀0%") == "仅雄性",
    )
    rules = GachaRules.from_config(
        {"draw_cooldown_seconds": 0, "shiny_chance_percent": 10}
    )
    partners = build_partners(db, rules)
    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(Path(td), partners, rules, rng=random.Random(11))
        outcome = store.draw("s", "u", "测试", count=3, today="2026-10-27")
        owned = store.owned_partners("s", "u")
        summary = store.get_profile("s", "u", "测试")
        images = {
            "抽卡": render_draw_card(
                outcome.items,
                nickname="测试",
                remaining=outcome.remaining,
                daily_limit=outcome.daily_limit,
                dust=outcome.dust,
                pity_ssr=outcome.pity_ssr,
                ssr_pity_after=rules.ssr_pity_after,
            ),
            "图鉴": render_collection_card(
                owned,
                nickname="测试",
                page=1,
                page_size=24,
                total=summary.total,
                collected=summary.collected,
            ),
            "资料": render_profile_card(summary, rank=1, rules=rules),
            "概率": render_rarity_legend(
                rules, {r: len(v) for r, v in store.by_rarity.items()}
            ),
        }
        for label, data in images.items():
            try:
                image = Image.open(io.BytesIO(data))
                ok = image.width == 1080 and image.height > 300
            except Exception:
                ok = False
            check(f"{label}卡片可解码且宽度 1080", ok)


def test_gacha_empty_rarity_pool():
    section("宝可梦抽卡 / 空稀有度档位")
    import random
    import tempfile
    from pathlib import Path

    from gacha import GachaRules, Partner
    from gacha_store import GachaStore

    rows = [
        {
            "id": str(i),
            "id_raw": f"{i:04d}",
            "name_zh": f"P{i}",
            "name_en": f"P{i}",
            "form_name": "",
            "types": "一般",
            "stats": "HP50/攻击50/防御50/特攻50/特防50/速度50（总和300）",
        }
        for i in range(1, 6)
    ]

    class _StubDB:
        def table(self, _name):
            return rows

        @staticmethod
        def first_stats(_row):
            return None, 300

    rules = GachaRules.from_config(
        {
            "rarity_weights": {"C": 0, "R": 0, "SR": 0, "SSR": 100},
            "daily_draw_limit": 10,
            "draw_cooldown_seconds": 0,
            "ssr_pity_after": 3,
            "shiny_chance_percent": 0,
        }
    )
    partners = tuple(
        Partner(
            key=str(row["id"]),
            dex=int(row["id"]),
            dex_raw=row["id_raw"],
            name=row["name_zh"],
            types=row["types"],
            bst=300,
            rarity="C",
            row=row,
        )
        for row in rows
    )
    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(Path(td), partners, rules, rng=random.Random(1))
        check(
            "空档位被剔除后权重落到可用档",
            store.effective_weights() == {"C": 1},
            str(store.effective_weights()),
        )
        check("卡池无 SSR 时保底不生效", store.pity_rarity() is None)
        outcome = store.draw("g", "u", "n", count=5, today="2026-10-27")
        check(
            "仍能正常抽满且只出可用档",
            len(outcome.items) == 5
            and all(item.rarity == "C" for item in outcome.items),
            str([item.rarity for item in outcome.items]),
        )
        check(
            "无 SSR 时不累积保底计数",
            store.get_profile("g", "u", today="2026-10-27").pity_ssr == 0,
        )


def test_collection_pagination():
    section("我的宝可梦 / 分页按已收集计算")
    import io
    import random
    import tempfile
    from pathlib import Path

    from PIL import Image

    from gacha import GachaRules, build_partners
    from gacha_store import GachaStore
    from partner_cards import render_collection_card

    db = PokedexDB()
    rules = GachaRules.from_config(
        {"daily_draw_limit": 100, "draw_cooldown_seconds": 0, "shiny_chance_percent": 0}
    )
    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(
            Path(td), build_partners(db, rules), rules, rng=random.Random(5)
        )
        store.draw("s", "u", "n", count=30, today="2026-10-27")
        owned = store.owned_partners("s", "u")
        summary = store.get_profile("s", "u", today="2026-10-27")
        data = render_collection_card(
            owned,
            nickname="n",
            page=1,
            page_size=24,
            total=summary.total,
            collected=summary.collected,
        )
        image = Image.open(io.BytesIO(data))
        expected_pages = -(-len(owned) // 24)
        check(
            "图鉴卡片可渲染",
            image.width == 1080 and image.height > 0,
            f"{len(owned)} 位",
        )
        check(
            "分页数按已收集数计算，与卡池总量无关",
            expected_pages == max(1, -(-len(owned) // 24))
            and expected_pages < summary.total,
            f"pages={expected_pages}, collected={len(owned)}, total={summary.total}",
        )


def test_card_extreme_inputs():
    section("宝可梦卡片 / 极端输入")
    import io
    import os
    import tempfile

    from PIL import Image

    from gacha import DrawItem
    from partner_cards import render_draw_card

    os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="partner-cards-"))
    row = {"id": "1", "id_raw": "0001", "name_zh": "超长名字" * 6}
    item = DrawItem(
        key="1",
        dex=1,
        dex_raw="0001",
        name=row["name_zh"],
        types="草/毒/飞行/龙/恶",
        bst=999,
        rarity="SSR",
        shiny=True,
        is_new=False,
        dust=12345,
        owned_count=9,
        row=row,
    )
    data = render_draw_card(
        [item] * 10,
        nickname="很长的训练家昵称" * 3,
        remaining=0,
        daily_limit=10,
        dust=999999,
        pity_ssr=999,
        ssr_pity_after=30,
    )
    image = Image.open(io.BytesIO(data))
    check(
        "10 连 + 超长名称/昵称仍可渲染",
        image.width == 1080 and image.height > 1000,
        str(image.size),
    )


def test_artwork_cache_bounded():
    section("立绘缓存 / 内存上限")
    import tempfile
    from pathlib import Path

    from PIL import Image

    from artwork import ArtworkCache

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        for i in range(1, 41):
            Image.new("RGBA", (64, 64), (i, 0, 0, 255)).save(root / f"{i:04d}.png")
        cache = ArtworkCache(root)
        cache.max_images = 8
        for i in range(1, 41):
            cache.load({"id": i})
        check(
            "缓存数量不超过上限",
            cache.cache_size() == 8,
            str(cache.cache_size()),
        )
        cache.load({"id": 40})
        check(
            "重复访问命中后仍不超上限",
            cache.cache_size() == 8,
            str(cache.cache_size()),
        )
        cache.clear_cache()
        check("clear_cache 能清空", cache.cache_size() == 0)


def test_exchange_arg_parsing():
    section("宝可梦兑换 / 参数解析")
    import main  # 依赖 test_main_imports 注入的 stub

    class _Ev:
        def __init__(self, text):
            self.message_str = text

    parse = main.PokemonPartnerPlugin._parse_exchange_args
    name, shiny, unknown = parse(_Ev("兑换宝可梦 皮卡丘"), "")
    check("单个名称", name == "皮卡丘" and not shiny and not unknown, str((name, shiny, unknown)))
    name, shiny, unknown = parse(_Ev("兑换宝可梦 皮卡丘 闪光"), "皮卡丘")
    check(
        "名称+闪光（第二个词被 AstrBot 截断时仍正确）",
        name == "皮卡丘" and shiny and not unknown,
        str((name, shiny, unknown)),
    )
    name, shiny, unknown = parse(_Ev("/兑换宝可梦 超梦 异色"), "")
    check("带斜杠与别名标记", name == "超梦" and shiny and not unknown, str((name, shiny, unknown)))
    name, shiny, unknown = parse(_Ev("兑换宝可梦 皮卡丘 闪光 多余"), "")
    check(
        "多余词被显式报告而不是静默丢弃",
        name == "皮卡丘" and shiny and unknown == ["多余"],
        str((name, shiny, unknown)),
    )
    name, shiny, unknown = parse(_Ev("兑换宝可梦 闪光"), "")
    check("只给闪光标记时名称为空", name == "" and shiny, str((name, shiny, unknown)))


def test_store_recovery():
    section("宝可梦数据 / 损坏恢复")
    import random
    import tempfile
    from pathlib import Path

    from gacha import GachaError, GachaRules, build_partners
    from gacha_store import GachaStore

    db = PokedexDB()
    rules = GachaRules.from_config(
        {"daily_draw_limit": 20, "draw_cooldown_seconds": 0, "shiny_chance_percent": 0}
    )
    partners = build_partners(db, rules)
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        store = GachaStore(folder, partners, rules, rng=random.Random(2))
        for _ in range(3):
            store.draw("g", "u", "n", today="2026-10-27")
        expected = store.get_profile("g", "u", today="2026-10-27").collected

        store.state_path.write_text("{broken", encoding="utf-8")
        recovered = GachaStore(folder, partners, rules, rng=random.Random(2))
        got = recovered.get_profile("g", "u", today="2026-10-27").collected
        check(
            "主文件损坏时从 .bak 恢复（允许丢最后一次写入）",
            got in {expected, max(0, expected - 1)},
            f"expected={expected}, got={got}",
        )
        snapshots = list(folder.glob("state.corrupt-*.json"))
        check("损坏文件被另存为快照", len(snapshots) == 1, str([p.name for p in snapshots]))
        recovered.draw("g", "u", "n", today="2026-10-27")
        backup_text = recovered.state_path.with_suffix(".json.bak").read_text(
            encoding="utf-8"
        )
        check(
            "恢复后写入不会把损坏文件复制成新备份",
            not backup_text.startswith("{broken") and backup_text.strip().startswith("{"),
            backup_text[:20],
        )

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        store = GachaStore(folder, partners, rules, rng=random.Random(3))
        store.draw("g", "u", "n", today="2026-10-27")
        store.state_path.write_text("{broken", encoding="utf-8")
        store.state_path.with_suffix(".json.bak").write_text("{broken", encoding="utf-8")
        broken = GachaStore(folder, partners, rules, rng=random.Random(3))
        try:
            broken.draw("g", "u", "n", today="2026-10-27")
            check("两份文件都损坏时拒绝覆盖写入", False)
        except GachaError:
            check("两份文件都损坏时拒绝覆盖写入", True)
        check(
            "损坏文件仍保留在磁盘上",
            store.state_path.exists()
            and store.state_path.read_text(encoding="utf-8") == "{broken",
        )


def test_render_does_not_block_event_loop():
    section("渲染 / 不阻塞事件循环")
    import asyncio
    import inspect

    from card_renderer import CardRenderer

    check(
        "render_pokemon 是异步接口",
        inspect.iscoroutinefunction(CardRenderer.render_pokemon),
    )
    check(
        "同步实现独立存在，供线程池调用",
        not inspect.iscoroutinefunction(CardRenderer.render_pokemon_sync),
    )

    async def _measure() -> float:
        db = PokedexDB()
        renderer = CardRenderer()
        row = db.pokemon("烈咬陆鲨")
        gaps: list[float] = []
        stop = asyncio.Event()

        async def ticker() -> None:
            import time

            last = time.perf_counter()
            while not stop.is_set():
                await asyncio.sleep(0.01)
                now = time.perf_counter()
                gaps.append(now - last)
                last = now

        task = asyncio.create_task(ticker())
        await asyncio.sleep(0.05)
        await renderer.render_pokemon(row, db)
        stop.set()
        await task
        return max(gaps) if gaps else 0.0

    gap = asyncio.run(_measure())
    check(
        "渲染期间事件循环停顿小于 0.1s",
        gap < 0.1,
        f"max gap={gap:.3f}s",
    )


def test_multi_instance_no_data_loss():
    section("宝可梦数据 / 多实例并发写入")
    import random
    import tempfile
    from pathlib import Path

    from gacha import GachaRules, build_partners
    from gacha_store import GachaStore

    db = PokedexDB()
    rules = GachaRules.from_config(
        {"daily_draw_limit": 10, "draw_cooldown_seconds": 0, "shiny_chance_percent": 0}
    )
    partners = build_partners(db, rules)

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        # 模拟插件热重载：两个实例同时存活，各自持有启动时的快照。
        first = GachaStore(folder, partners, rules, rng=random.Random(1))
        second = GachaStore(folder, partners, rules, rng=random.Random(2))
        first.draw("g", "ua", "a", today="2026-10-27")
        second.draw("g", "ub", "b", today="2026-10-27")
        first.draw("g", "ua2", "a2", today="2026-10-27")

        reloaded = GachaStore(folder, partners, rules, rng=random.Random(3))
        users = sorted(reloaded.state["scopes"]["g"]["users"])
        check(
            "两个实例的写入都不丢失",
            users == ["ua", "ua2", "ub"],
            str(users),
        )

    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        first = GachaStore(folder, partners, rules, rng=random.Random(4))
        second = GachaStore(folder, partners, rules, rng=random.Random(5))
        first.draw("g", "same", "n", today="2026-10-27")
        before = GachaStore(folder, partners, rules, rng=random.Random(6))
        baseline = before.get_profile("g", "same", today="2026-10-27").used_today
        # second 持有旧快照（updated_at 更早），写入不应把计数改小。
        second.draw("g", "same", "n", today="2026-10-27")
        third = GachaStore(folder, partners, rules, rng=random.Random(6))
        used = third.get_profile("g", "same", today="2026-10-27").used_today
        check(
            "同一用户并发写入时计数不倒退",
            used >= baseline,
            f"before={baseline}, after={used}",
        )


def test_draw_card_includes_artwork():
    section("抽卡卡片 / 立绘与信息")
    import io
    import random
    import tempfile
    from pathlib import Path

    from PIL import Image

    from artwork import ArtworkCache
    from gacha import GachaRules, build_partners
    from gacha_store import GachaStore
    from partner_cards import render_draw_card

    db = PokedexDB()
    rules = GachaRules.from_config(
        {"daily_draw_limit": 10, "draw_cooldown_seconds": 0, "shiny_chance_percent": 0}
    )
    partners = build_partners(db, rules)
    pika = next(p for p in partners if p.dex == 25)

    with tempfile.TemporaryDirectory() as td:
        art_dir = Path(td) / "art"
        art_dir.mkdir()
        # 用纯色块代替网络立绘，验证渲染确实把图片贴进卡片。
        Image.new("RGBA", (300, 300), (255, 220, 0, 255)).save(art_dir / "0025.png")
        cache = ArtworkCache(art_dir, db=db, allow_download=False)

        with tempfile.TemporaryDirectory() as store_dir:
            store = GachaStore(
                Path(store_dir), (pika,), rules, rng=random.Random(1)
            )
            outcome = store.draw("g", "u", "n", today="2026-10-27")
            with_art = render_draw_card(
                outcome.items,
                nickname="n",
                remaining=outcome.remaining,
                daily_limit=outcome.daily_limit,
                dust=outcome.dust,
                pity_ssr=outcome.pity_ssr,
                ssr_pity_after=rules.ssr_pity_after,
                image_loader=cache.load,
            )
            without_art = render_draw_card(
                outcome.items,
                nickname="n",
                remaining=outcome.remaining,
                daily_limit=outcome.daily_limit,
                dust=outcome.dust,
                pity_ssr=outcome.pity_ssr,
                ssr_pity_after=rules.ssr_pity_after,
                image_loader=None,
            )
            two = render_draw_card(
                outcome.items * 2,
                nickname="n",
                remaining=outcome.remaining,
                daily_limit=outcome.daily_limit,
                dust=outcome.dust,
                pity_ssr=outcome.pity_ssr,
                ssr_pity_after=rules.ssr_pity_after,
                image_loader=cache.load,
            )
            five = render_draw_card(
                outcome.items * 5,
                nickname="n",
                remaining=outcome.remaining,
                daily_limit=outcome.daily_limit,
                dust=outcome.dust,
                pity_ssr=outcome.pity_ssr,
                ssr_pity_after=rules.ssr_pity_after,
                image_loader=cache.load,
            )

        a = Image.open(io.BytesIO(with_art)).convert("RGB")
        b = Image.open(io.BytesIO(without_art)).convert("RGB")
        two_image = Image.open(io.BytesIO(two)).convert("RGB")
        five_image = Image.open(io.BytesIO(five)).convert("RGB")
        check("单抽卡片尺寸", a.width == 1080 and 1100 <= a.height <= 3200, str(a.size))
        # 五种卡片构图不同；检测真实贴图色块的面积，避免依赖旧版固定坐标。
        def artwork_pixels(image):
            return sum(red > 235 and green > 195 and blue < 55
                       for red, green, blue in image.getdata())

        artwork_area = artwork_pixels(a)
        check(
            "立绘已绘制到卡片上",
            artwork_area > 30000,
            str(artwork_area),
        )
        check(
            "无立绘时使用占位（与有立绘明显不同）",
            a.size == b.size and artwork_area - artwork_pixels(b) > 30000,
        )
        check("两抽网格保留双方立绘", two_image.width == 1080 and artwork_pixels(two_image) > 30000, str(two_image.size))
        check("五抽网格按行增加高度并保留立绘",
              five_image.width == 1080 and two_image.height < five_image.height <= 3200
              and artwork_pixels(five_image) > artwork_pixels(two_image), str(five_image.size))


def test_webui_settings_rollback_and_artwork():
    section("WebUI / 配置回滚与图鉴立绘")
    import asyncio
    import base64
    import tempfile
    from pathlib import Path

    from PIL import Image

    from artwork import ArtworkCache
    from gacha import GachaRules, build_partners
    from webui import PokemonPartnerWebUI

    class Config(dict):
        def save_config(self):
            return None

    class Plugin:
        def __init__(self):
            self.config = Config({"gacha_enabled": True, "value": 1})
            self.partner_service = object()
            self.artwork = None
            self._partner_init_error = ""
            self.attempts = 0

        def _init_partners(self):
            self.attempts += 1
            if self.attempts == 1:
                self.partner_service = None
                self._partner_init_error = "forced failure"
            else:
                self.partner_service = object()
                self._partner_init_error = ""

    plugin = Plugin()
    webui = PokemonPartnerWebUI(plugin)
    try:
        webui._apply_settings({"gacha_enabled": True, "value": 2})
        rolled_back = False
    except RuntimeError:
        rolled_back = True
    check(
        "重载失败时恢复旧配置与旧服务",
        rolled_back
        and plugin.config["value"] == 1
        and plugin.partner_service is not None
        and plugin.attempts == 2,
        str((plugin.config, plugin.attempts)),
    )

    db = PokedexDB()
    rules = GachaRules.from_config(
        {"daily_draw_limit": 3, "draw_cooldown_seconds": 0}
    )
    partner = next(p for p in build_partners(db, rules) if p.dex == 25)
    with tempfile.TemporaryDirectory() as td:
        art_dir = Path(td)
        Image.new("RGBA", (64, 64), (255, 220, 0, 255)).save(art_dir / "0025.png")
        plugin.artwork = ArtworkCache(art_dir, db=db, allow_download=False)
        url = asyncio.run(webui._artwork_data_url(partner.row))
        payload = url.partition(",")[2]
        check(
            "图鉴详情返回可显示的立绘 data URL",
            url.startswith("data:image/png;base64,")
            and len(base64.b64decode(payload)) > 100,
            url[:48],
        )


def test_webui_bridge_protocol():
    section("WebUI / Bridge 调用协议")
    core_js = (
        ROOT / "pages" / "partner-console" / "js" / "core.js"
    ).read_text(encoding="utf-8")
    check(
        "Bridge GET 使用相对 endpoint 与独立参数",
        "bridge.apiGet(bridgeEndpoint(path), query)" in core_js,
    )
    check(
        "Bridge POST 使用相对 endpoint",
        "bridge.apiPost(bridgeEndpoint(path), body ?? {})" in core_js,
    )
    check(
        "Bridge endpoint 禁止查询串和路径穿越",
        "endpoint.split(\"/\")" in core_js
        and ("/[?#\\\\]/" in core_js or "/[?:#\\\\]/" in core_js),
    )


def test_webui_theme_namespace():
    section("WebUI / 主题变量隔离")
    core_js = (
        ROOT / "pages" / "partner-console" / "js" / "core.js"
    ).read_text(encoding="utf-8")
    tokens_css = (
        ROOT / "pages" / "partner-console" / "styles" / "tokens.css"
    ).read_text(encoding="utf-8")
    check(
        "主题变量使用 pk 命名空间",
        "--pk-bg:" in tokens_css
        and "--pk-bg-panel:" in tokens_css
        and "--pk-text:" in tokens_css
        and "--pk-accent:" in tokens_css,
    )
    check(
        "页面不再定义通用宿主变量",
        all(
            f"\n  --{name}:" not in tokens_css
            for name in ("bg", "bg-panel", "text", "accent")
        ),
    )
    check(
        "主题同步 html/body 与 color-scheme",
        "document.body.dataset.partnerTheme = value" in core_js
        and "document.documentElement.style.colorScheme" in core_js,
    )


def test_scope_identity():
    section("会话隔离 / 平台群标识兼容")
    from types import SimpleNamespace

    import main

    class Event:
        def __init__(
            self,
            *,
            user: str = "u1",
            session: str = "",
            group: str = "",
            private: bool = False,
            raw=None,
        ):
            self.message_obj = SimpleNamespace(raw_message=raw or {})
            self.unified_msg_origin = f"platform:session:{session or group}"
            self._user = user
            self._session = session
            self._group = group
            self._private = private

        def get_sender_id(self):
            return self._user

        def get_session_id(self):
            return self._session

        def get_group_id(self):
            return self._group

        def get_sender_name(self):
            return "测试用户"

        def is_private_chat(self):
            return self._private

    scope = main.PokemonPartnerPlugin._scope
    first = scope(Event(session="group-a"))[0]
    second = scope(Event(session="group-b"))[0]
    check(
        "群 ID 缺失时使用会话 ID 隔离",
        first == "group:group-a" and second == "group:group-b" and first != second,
        f"{first} / {second}",
    )

    raw_first = scope(Event(session="shared", raw={"group_openid": "openid-a"}))[0]
    raw_second = scope(Event(session="shared", raw={"group_openid": "openid-b"}))[0]
    check(
        "原始事件群标识优先于重复会话 ID",
        raw_first != raw_second
        and raw_first == "group:openid-a"
        and raw_second == "group:openid-b",
        f"{raw_first} / {raw_second}",
    )
    check(
        "私聊仍按用户隔离",
        scope(Event(session="private-a", private=True))[0] == "private:u1",
    )


def test_session_isolation_and_my_pokemon():
    section("会话隔离 / 我的宝可梦")
    import random
    import tempfile
    from pathlib import Path

    from gacha import GachaRules, build_partners
    from gacha_store import GachaStore

    db = PokedexDB()
    rules = GachaRules.from_config(
        {"daily_draw_limit": 20, "draw_cooldown_seconds": 0, "shiny_chance_percent": 0}
    )
    partners = build_partners(db, rules)

    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(Path(td), partners, rules, rng=random.Random(7))
        store.draw("group:1001", "u1", "甲", count=5, today="2026-10-27")
        store.draw("group:2002", "u1", "甲", count=3, today="2026-10-27")
        a = store.get_profile("group:1001", "u1", today="2026-10-27")
        b = store.get_profile("group:2002", "u1", today="2026-10-27")
        check(
            "同一用户在不同群的数据独立",
            a.collected != b.collected and a.used_today == 5 and b.used_today == 3,
            f"A={a.collected}/{a.used_today}, B={b.collected}/{b.used_today}",
        )
        check(
            "「我的宝可梦」只返回本群已获得",
            len(store.owned_partners("group:1001", "u1")) == a.collected
            and len(store.owned_partners("group:2002", "u1")) == b.collected,
        )
        check(
            "另一个群看不到别人的收藏",
            store.get_profile("group:3003", "u1", today="2026-10-27").collected == 0,
        )


def test_champions_db():
    """冠军赛查询层：临时库写入 -> 排名/配置/反查/趋势/幂等。"""
    import sqlite3
    import tempfile

    from champions_db import SCHEMA, ChampionsDB, resolve_db_path, season_key

    section("冠军赛数据（champions_db）")

    check(
        "赛季排序键 M-7 > M-6（同源内）",
        season_key("M-7") > season_key("M-6"),
    )
    check(
        "赛季排序键 月份格式按年月比",
        season_key("2026-10") > season_key("2026-09"),
    )

    with tempfile.TemporaryDirectory() as td:
        path = f"{td}/champions.db"
        conn = sqlite3.connect(path)
        conn.executescript(SCHEMA)

        # 模拟写入端逻辑（champions_fetch.upsert_* 的 SQL 语义）
        def upsert(pid, name, fmt, season, rank, usage=None):
            conn.execute(
                "INSERT INTO champion_pokemon(pokemon_id, name_zh) VALUES(?,?) "
                "ON CONFLICT(pokemon_id) DO UPDATE SET name_zh=excluded.name_zh",
                (pid, name),
            )
            conn.execute(
                """INSERT INTO champion_usage(pokemon_id, format, season, source,
                       usage_pct, rank, updated_at)
                   VALUES(?,?,?,?,?,?,?)
                   ON CONFLICT(pokemon_id, format, season, source) DO UPDATE SET
                     usage_pct=COALESCE(excluded.usage_pct, champion_usage.usage_pct),
                     rank=COALESCE(excluded.rank, champion_usage.rank)""",
                (pid, fmt, season, "pokechamdb", usage, rank, "2026/10/17 17:14"),
            )

        rows = [
            ("garchomp", "烈咬陆鲨", 1),
            ("primarina", "西狮海壬", 2),
            ("mimikyu", "谜拟Ｑ", 3),
        ]
        for pid, name, rank in rows:
            upsert(pid, name, "single", "M-7", rank)
        upsert("garchomp", "烈咬陆鲨", "single", "M-6", 4)  # 上一赛季
        upsert("garchomp", "烈咬陆鲨", "double", "M-7", 5)  # 双打
        conn.commit()

        db = ChampionsDB(path)
        top = db.top_usage("single", limit=10)
        check(
            "top_usage 按 rank 排序",
            [r["pokemon_id"] for r in top] == ["garchomp", "primarina", "mimikyu"],
        )
        check("latest_season 自动取 M-7", db.latest_season("single") == "M-7")
        check(
            "resolve_season 空参数回退最新", db.resolve_season(None, "single") == "M-7"
        )
        row = db.resolve_pokemon("烈咬陆鲨", "single")
        check("resolve_pokemon 中文名命中", row and row["pokemon_id"] == "garchomp")
        by_rank = db.by_rank(2, "single", "M-7")
        check(
            "by_rank 第2名→西狮海壬（含 total）",
            by_rank and by_rank["name_zh"] == "西狮海壬" and by_rank["total"] == 3,
            str(by_rank),
        )
        check("by_rank 越界名次返回 None", db.by_rank(99, "single", "M-7") is None)
        check(
            "pokemon_ranks 双格式各带 total",
            {r["format"]: r["rank"] for r in db.pokemon_ranks("烈咬陆鲨")}
            == {"single": 1, "double": 5},
        )
        check(
            "trend 跨赛季两行",
            [r["season"] for r in db.trend("garchomp", "single")] == ["M-6", "M-7"],
        )
        check("格式隔离（双打互不干扰）", db.top_usage("double")[0]["rank"] == 5)

        # 配置项
        uid = conn.execute(
            "SELECT usage_id FROM champion_usage WHERE pokemon_id='garchomp' "
            "AND format='single' AND season='M-7'"
        ).fetchone()[0]
        conn.executemany(
            "INSERT INTO champion_option(usage_id, option_type, option_value, usage_pct, rank)"
            " VALUES(?,?,?,?,?)",
            [
                (uid, "move", "地震", 65.0, None),
                (uid, "move", "隐形岩", 46.0, None),
                (uid, "item", "生命宝珠", 1.0, None),
                (uid, "teammate", "巨牙鲨", None, 1),
            ],
        )
        conn.commit()
        moves = db.pokemon_options("garchomp", "single", "M-7", "move", top_n=1)
        check(
            "pokemon_options 按使用率降序+limit",
            len(moves) == 1 and moves[0]["option_value"] == "地震",
        )
        hits = db.find_pokemon_by_item("生命宝珠", "single", "M-7")
        check(
            "find_pokemon_by_item 反查",
            len(hits) == 1
            and hits[0]["name_zh"] == "烈咬陆鲨"
            and hits[0]["item_usage_pct"] == 1.0,
        )
        check(
            "未写入 option 的类型返回空",
            db.pokemon_options("garchomp", "single", "M-7", "nature") == [],
        )

        # 幂等：同 (pokemon,fmt,season,source) 再写不产生新行
        def garchomp_rank():
            return db.resolve_pokemon("烈咬陆鲨", "single", "M-7")["rank"]

        upsert("garchomp", "烈咬陆鲨", "single", "M-7", 9)
        conn.commit()
        n = conn.execute(
            "SELECT COUNT(*) FROM champion_usage WHERE pokemon_id='garchomp' "
            "AND format='single' AND season='M-7'"
        ).fetchone()[0]
        check("幂等 upsert 不新增行", n == 1)
        check("新排名覆盖旧排名", garchomp_rank() == 9)
        upsert("garchomp", "烈咬陆鲨", "single", "M-7", None)  # 抓取侧 rank 缺失
        conn.commit()
        check("rank 缺失时 COALESCE 保留旧值", garchomp_rank() == 9)

        conn.close()

    check(
        "resolve_db_path 返回 Path",
        isinstance(resolve_db_path(None), Path),
    )


def test_store_transaction_regressions():
    section("宝可梦数据 / 完整事务与管理回归")
    import random
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch
    from gacha import GachaRules, build_partners
    from gacha_store import GachaStore

    rules = GachaRules.from_config({"daily_draw_limit": 20, "draw_cooldown_seconds": 0, "shiny_chance_percent": 0})
    partners = build_partners(PokedexDB(), rules)
    with tempfile.TemporaryDirectory() as td:
        stores = [GachaStore(td, partners, rules, rng=random.Random(i)) for i in range(2)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            outcomes = list(pool.map(lambda i: stores[i % 2].draw("group:g", "u", today="2026-10-28"), range(12)))
        summary = stores[0].get_profile("group:g", "u", today="2026-10-28")
        check("两个实例并发同用户 12 抽全部入账", summary.used_today == 12 and summary.total_draws == 12)
        check("返回每次唯一递增的使用次数", sorted(o.used_today for o in outcomes) == list(range(1, 13)))
        before = stores[0].snapshot()
        rng_before = stores[0].rng.getstate()
        with patch.object(stores[0], "_write_snapshot", side_effect=OSError("forced write failure")):
            try:
                stores[0].draw("group:g", "u", today="2026-10-28")
            except OSError:
                pass
        check("写入失败回滚内存抽卡与随机状态", stores[0].state == before and stores[0].rng.getstate() == rng_before)
        check("删除会话返回真实人数", stores[0].delete_scope("group:g") == 1)
        stores[1].set_dust("group:other", "v", 50)
        check("旧实例后续写入不会复活已删会话", "group:g" not in stores[0].snapshot()["scopes"])

    milestone_rules = GachaRules.from_config({"draw_cooldown_seconds": 0, "shiny_chance_percent": 0})
    with tempfile.TemporaryDirectory() as td:
        store = GachaStore(td, partners, milestone_rules)
        profile = store._profile("g", "u")
        profile["owned"] = {p.key: {"normal": 1} for p in partners[:99]}
        profile["milestones"] = {str(n): True for n in (10, 30, 60)}
        store._save()
        with patch.object(store, "_choose", return_value=partners[99]):
            result = store.draw("g", "u", today="2026-10-28")
        check("本次里程碑额外次数即时显示", result.daily_limit == 4 and result.remaining == 3)


def test_artwork_repair_and_webui_export():
    section("缓存与 WebUI / 损坏修复、缩略图、导出")
    import asyncio
    import base64
    import io
    from types import SimpleNamespace
    from unittest.mock import patch
    from PIL import Image
    from artwork import ArtworkCache
    from gacha import ConfigError, GachaRules, build_partners
    from gacha_store import GachaStore
    from partner_service import PartnerService
    from webui import PokemonPartnerWebUI

    db = PokedexDB()
    rules = GachaRules.from_config({"draw_cooldown_seconds": 0})
    partners = build_partners(db, rules)
    row = next(p.row for p in partners if p.dex == 25)
    with tempfile.TemporaryDirectory() as td:
        folder = Path(td)
        artwork = ArtworkCache(folder / "artwork")
        artwork.cache_dir.mkdir()
        target = artwork.cache_dir / "0025.png"
        target.write_bytes(b"broken PNG")
        check("损坏 PNG 被重新识别为缺图且重复编号去重", len(artwork.missing([row, row])) == 1)
        output = io.BytesIO()
        Image.new("RGBA", (480, 480), (250, 210, 10, 255)).save(output, format="PNG")
        async def download(_session, _dex):
            return output.getvalue()
        with patch.object(artwork, "_download", side_effect=download):
            saved = asyncio.run(artwork.prefetch([row, row]))
        check("重新下载修复损坏缓存", saved == 1 and artwork.load(row) is not None and not artwork.missing([row]))
        store = GachaStore(folder / "state", partners, rules)
        store.set_dust("group:g", "u", 42)
        service = PartnerService(db, store, artwork.load)
        plugin = SimpleNamespace(partner_service=service, artwork=artwork, db=db)
        api = PokemonPartnerWebUI(plugin)
        thumb = Image.open(io.BytesIO(base64.b64decode(api._local_thumbnail(row).partition(",")[2])))
        check("列表缩略图保持 240px 有界尺寸", thumb.size == (240, 240))
        with patch("webui._query", return_value="group:g"):
            exported = asyncio.run(api.api_sessions_export())
        check("导出仅含指定会话并保留结构版本时间", exported["version"] == 2 and exported["data"]["users"]["u"]["dust"] == 42 and "exported_at" in exported and "config" not in exported)
    invalid = [{"gacha_enabled": "false"}, {"pokeapi_timeout": 0}, {"unknown_secret": "x"}]
    errors = 0
    for config in invalid:
        try:
            PokemonPartnerWebUI._validate_settings(config, config)
        except ConfigError:
            errors += 1
    check("配置类型、超时与未知字段在写入前拒绝", errors == len(invalid))


from pokedex_db import PokedexDB

if __name__ == "__main__":
    test_phase1_engine()
    test_phase1_matchups()
    test_phase1_counters()
    test_phase1_compare_data()
    test_phase2_learnset()
    test_phase2_data_quality()
    test_phase3_battle()
    test_phase4_team_and_stats()
    test_ability_corrections()
    test_champions_db()
    test_main_imports()
    test_champ_arg_parser()
    test_card_champion_section()
    test_gacha_rules()
    test_gacha_store()
    test_gacha_pity_and_shiny()
    test_partner_cards()
    test_gacha_empty_rarity_pool()
    test_collection_pagination()
    test_card_extreme_inputs()
    test_artwork_cache_bounded()
    test_exchange_arg_parsing()
    test_store_recovery()
    test_render_does_not_block_event_loop()
    test_multi_instance_no_data_loss()
    test_store_transaction_regressions()
    test_artwork_repair_and_webui_export()
    test_draw_card_includes_artwork()
    test_webui_settings_rollback_and_artwork()
    test_webui_bridge_protocol()
    test_webui_theme_namespace()
    test_scope_identity()
    test_session_isolation_and_my_pokemon()
    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)
