<p align="center">
  <img src="./logo.png" alt="宝可梦伙伴图鉴" width="112">
</p>

<h1 align="center">宝可梦伙伴图鉴</h1>

<p align="center">在群聊里召唤宝可梦伙伴，收集图鉴，顺便查资料、算能力值、模拟对战。</p>

<p align="center">
  <a href="https://github.com/Whereis-Alice/astrbot_plugin_pokemon_partner"><img alt="repo" src="https://img.shields.io/badge/GitHub-astrbot__plugin__pokemon__partner-2f81f7"></a>
  <img alt="AstrBot" src="https://img.shields.io/badge/AstrBot-%3E%3D4.16%2C%3C5-4c8bf5">
  <img alt="license" src="https://img.shields.io/badge/license-MIT-2ea44f">
</p>

---

## 这是什么

这是一个 AstrBot 插件，包含两套互相独立、但共用同一份本地图鉴数据的能力：

1. **伙伴召唤与收藏**：每天按配置的次数和稀有度概率抽取宝可梦伙伴，收集个人图鉴、闪光个体，用重复伙伴换星尘，再定向兑换想要的伙伴。
2. **宝可梦资料与对战工具**：查询 1025 只宝可梦和 295 个形态的中文图鉴、特性、招式、道具、地区图鉴；计算等级能力值；查属性相性、克制关系；校验配招；模拟对战；分析队伍；查询冠军赛使用率数据。

所有图鉴、概率、对战数据都在本地。只有两项功能会联网：

- 可选的 PokéAPI 补充查询（默认开启，可在配置中关闭）；
- 伙伴卡片缺少本地立绘时，从 PokéAPI 官方图源下载一次并缓存（默认开启，可关闭）。

关闭这两项后，插件完全离线可用。

> 本项目是独立实现，插件标识符、数据目录、指令与卡片样式均与上游 `ca1e/astrbot_plugin_pokedex` 及 `Whereis-Alice/astrbot_plugin_kirby_catalog` 分离，不会与它们共用配置或数据。

## 功能一览

| 模块 | 能力 |
| --- | --- |
| 每日召唤 | 每次固定抽取数量（1–10 可配）、四档稀有度概率、SSR 保底、连续未出新保底、冷却、每日次数 |
| 收藏养成 | 个人图鉴、闪光个体分开收藏、重复折算星尘、星尘定向兑换、收集里程碑奖励、会话内排行榜 |
| 伙伴卡片 | 抽卡结果、收藏图鉴、训练家资料、概率说明四张主题卡片；渲染失败自动回退纯文本 |
| 图鉴查询 | 编号/中英日名称/形态名查询，含种族值、特性、弱点、进化链与世代图鉴说明 |
| 对战工具 | 能力值计算、双向属性相性、克制推荐、双宝可梦对比、招式表、配招校验、回合制模拟、队伍分析、种族值筛选 |
| 冠军赛数据 | 使用率排名、单只配置、道具反查、跨赛季趋势（需要自行抓取本地 SQLite） |
| LLM 工具 | 图鉴、对战、冠军赛与伙伴系统共 32 个原子工具，供模型在群聊中组合调用 |
| 离线可用 | 除可选的 PokéAPI 与立绘下载外不发起网络请求 |

## 快速开始

### 环境要求

- AstrBot `>=4.16,<5`
- Python 依赖：`Pillow`、`aiohttp`（见 `requirements.txt`）
- 中文系统字体（Windows 自带微软雅黑；Linux 建议安装 `fonts-noto-cjk`）

### 安装

在 AstrBot WebUI 的插件管理中选择「从仓库安装」，填入：

```text
https://github.com/Whereis-Alice/astrbot_plugin_pokemon_partner
```

也可以手动放入 `data/plugins/`：

```bash
cd /path/to/AstrBot/data/plugins
git clone https://github.com/Whereis-Alice/astrbot_plugin_pokemon_partner.git
python -m pip install -r astrbot_plugin_pokemon_partner/requirements.txt
```

安装后重载插件。若卡片里中文显示为方框，说明运行环境没有中文字体，安装
`fonts-noto-cjk` 后重启 AstrBot 即可。

### 立绘（可选，但强烈建议）

插件本身**不打包**宝可梦官方立绘，以避免仓库体积和版权问题。想要完整卡片效果，
请把 [pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh) 的
`data/images` 目录下载到服务器，并在插件配置的「宝可梦图片目录」填写该目录的绝对路径：

```text
/path/to/pokemon-dataset-zh/data/images
```

目录结构应为 `official/`、`home/` 等子目录。AstrBot 跑在 Docker 里时，先把目录挂载进
容器，再填容器内路径。

不配置也能用：开启「本地立绘缺失时使用网络官方图」后，插件会按需下载官方图并缓存到
插件数据目录；关闭后卡片使用占位图，不影响任何文字功能。

## 玩法说明

### 召唤伙伴

```text
伙伴召唤
```

每天有固定次数（默认 3 次），每次召唤固定数量（默认 1 位）。这两个数字都可以在配置里改：

- 想一次抽 5 位，就把「每次召唤的伙伴数量」设为 5；
- 想每天只能抽 1 次，就把「每日召唤次数」设为 1。

**每次先按稀有度权重决定档位，再在该档位里等概率选择伙伴。** 默认概率与卡池规模：

| 稀有度 | 默认概率 | 种族值总和 | 卡池数量（默认形态） |
| --- | --- | --- | --- |
| SSR 传说 | 3% | ≥ 580 | 92 |
| SR 史诗 | 12% | 480–579 | 331 |
| R 稀有 | 30% | 400–479 | 216 |
| C 普通 | 55% | < 400 | 386 |

概率是权重归一化后的结果，不要求相加等于 100；例如 `55/30/12/3` 就是
55% / 30% / 12% / 3%。把某一档设为 0 就不再产出该档。

首次获得的伙伴会写入图鉴；重复伙伴折算星尘。开启「把地区形态/超级进化等形态计入卡池」
后，卡池从 1025 项扩展到 1320 项，每项独立收藏。

### 保底

- **SSR 保底**：默认连续 30 次没出 SSR，下一次必出 SSR。计数按用户单独保存，出 SSR 后清零。
- **新伙伴保底**：默认关闭。设置成 N 后，连续 N 次抽到重复伙伴，下一次强制未拥有。
- 两套保底互相独立，不会因为一个触发而重置另一个。

### 星尘与兑换

重复伙伴按稀有度折算星尘（默认 C 2 / R 6 / SR 20 / SSR 80），闪光重复再乘倍率
（默认 3 倍）。星尘用于定向兑换：

```text
伙伴兑换 皮卡丘
伙伴兑换 皮卡丘 闪光
```

普通与闪光分开收藏，所以已经拥有普通皮卡丘时仍可兑换闪光皮卡丘。闪光兑换价格默认是
普通价格的 4 倍。

### 收集里程碑

收集数达到 10 / 30 / 60 / 100 / 200 / 400 / 600 / 800 位时，自动发放星尘；100 位起
还会增加当天的额外召唤次数。集齐全部伙伴有一次性奖励。

### 全部指令

| 指令 | 说明 |
| --- | --- |
| `伙伴召唤` | 消耗今日次数召唤伙伴 |
| `伙伴图鉴 [页码]` | 查看自己的收藏，每页 24 位 |
| `伙伴资料` | 收集进度、闪光数、星尘、剩余次数、保底进度、会话排名 |
| `伙伴兑换 <名称> [闪光]` | 用星尘定向兑换未拥有的伙伴 |
| `伙伴排行` | 当前群/私聊的收集排行 |
| `伙伴概率` | 概率、卡池规模与规则说明卡 |
| `伙伴帮助` | 指令总览 |
| `伙伴重置` | 管理员重置自己的今日次数 |
| `伙伴加次数 [数量]` | 管理员给自己增加今日次数 |
| `伙伴重置群` | 管理员重置本群所有人的今日次数 |

别名：`召唤伙伴`、`抽伙伴`、`伙伴收藏`、`伙伴进度`、`伙伴换取`、`伙伴规则`、`伙伴菜单`。

### 图鉴与对战

| 指令 | 说明 |
| --- | --- |
| `宝可梦 <编号/名称>` | 图鉴卡片（含冠军赛名次） |
| `宝可梦 <名称> 招式表 [等级]` | 可学招式与推荐配招 |
| `宝可梦 <名称> 能力值 [等级 个体 性格]` | 官方公式计算等级能力值 |
| `特性 <名称>` / `招式 <名称>` / `道具 <名称>` | 数据查询 |
| `性格 [名称]` | 25 种性格修正与口味速查 |
| `相性 <属性1> [属性2]` | 防守方向相性 |
| `相性 打 <属性>` | 进攻方向相性 |
| `克制 <宝可梦>` | 推荐克制它的候选 |
| `克制 <A> <B>` | 两只宝可梦多维度对比 |
| `对战 <A> <B> [等级]` | 简化回合制对战模拟 |
| `队伍分析 <名称...>` | 共同弱点、全队抗性与属性覆盖 |
| `筛选 速度>100 特攻>=120` | 按种族值筛选 |
| `宝冠军 [关键词]` | 冠军赛数据一站式查询 |
| `许愿星 <性格> [个体] [TID SID]` | 许愿星基拉祈种子筛选 |

群聊中 `@机器人` 直接提问时，模型可以自动调用图鉴工具（需要模型支持 function calling）。
工具采用「一个动作一个函数」的原子设计，方便模型组合，例如先分析队伍，再逐只查询配置。

## 配置说明

### 召唤与概率

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `gacha_enabled` | `true` | 关闭后不加载抽卡系统，其他功能不受影响 |
| `draws_per_command` | `1` | 每次召唤固定抽取几位（1–10） |
| `daily_draw_limit` | `3` | 每日基础次数（0–100） |
| `draw_cooldown_seconds` | `3.0` | 两次召唤的最短间隔 |
| `rarity_weights` | `C55/R30/SR12/SSR3` | 四档稀有权重，按比例归一化 |
| `rare_min_bst` | `400` | R 档最低种族值总和 |
| `super_rare_min_bst` | `480` | SR 档最低种族值总和 |
| `ultra_rare_min_bst` | `580` | SSR 档最低种族值总和 |
| `ssr_pity_after` | `30` | 连续多少次未出 SSR 后必出；0 关闭 |
| `new_pity_after` | `0` | 连续多少次重复后必出新；0 关闭 |
| `allow_duplicate_draw` | `true` | 关闭后优先抽未拥有的伙伴 |
| `include_alternate_forms` | `false` | 是否把形态计入卡池 |
| `shiny_chance_percent` | `1.5` | 闪光概率 |
| `duplicate_dust` | `2/6/20/80` | 重复伙伴折算星尘 |
| `spark_costs` | `20/60/150/400` | 星尘兑换价格 |
| `shiny_dust_multiplier` | `3.0` | 闪光重复的星尘倍率 |
| `shiny_spark_multiplier` | `4.0` | 闪光兑换的价格倍率 |

三个稀有度阈值必须严格递增，否则插件会记录错误并停用伙伴系统，图鉴功能仍然可用。

### 图片与网络

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `images_dir` | 空 | pokemon-dataset-zh 的 `data/images` 目录 |
| `enable_image_card` | `true` | 关闭后所有图片卡片（含伙伴卡片）返回纯文本 |
| `fallback_web_image` | `true` | 本地缺图时使用网络官方图 |
| `pokeapi_enabled` | `true` | PokéAPI 补充查询 |
| `pokeapi_timeout` | `15` | PokéAPI 超时秒数 |

### 冠军赛

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `champions_db_path` | 空 | `champions.db` 路径；留空用插件数据目录 |
| `champions_default_format` | `single` | 默认单打/双打 |
| `champions_top_n` | `10` | 默认排名条数 |

## 数据目录

插件使用独立标识符，数据默认位于：

```text
data/plugin_data/astrbot_plugin_pokemon_partner/
├── gacha/
│   ├── state.json          # 各会话的收藏、星尘、次数、保底
│   └── state.json.bak      # 上一次成功写入的备份
├── artwork/                # 自动下载的立绘缓存
└── champions.db            # 可选：冠军赛数据库
```

`state.json` 使用临时文件 + `os.replace` 原子写入，并保留一份 `.bak`。写入失败不会破坏
旧文件。备份或迁移时请复制整个 `astrbot_plugin_pokemon_partner` 数据目录。

群聊按群隔离，私聊按用户隔离：同一个人在 A 群和 B 群的收藏互不影响。更换群号或清空
数据目录都会重置对应收藏，操作前请备份。

### 冠军赛数据

冠军赛查询依赖 `champions.db`，插件运行时只读，不联网。抓取脚本需要额外的 Playwright：

```bash
pip install -r requirements-scraper.txt
playwright install chromium
python champions_fetch.py                 # 最新赛季，单打+双打
python champions_fetch.py --top 50        # 多抓配置明细
python champions_fetch.py --season M-6    # 指定历史赛季
```

脚本默认写入 `data/plugin_data/astrbot_plugin_pokemon_partner/champions.db`，与插件读取
路径一致。站点改版导致选择器失效时，可用 `--headed` 查看实际渲染并调整
`champions_fetch.py` 顶部的 `JS_*` 常量。

## 常见问题

**Q：抽卡没有图片，只有文字。**
检查是否安装了 Pillow、系统是否有中文字体，以及 AstrBot 日志里是否有「渲染失败」。
渲染失败会自动回退文字，不影响抽卡结果。

**Q：卡片上的宝可梦没有立绘。**
没配置 `images_dir`，且网络图下载失败或已关闭 `fallback_web_image`。配置本地图片目录
即可彻底离线。

**Q：概率和我想的不一样。**
权重是归一化后的比例，不是百分比总和必须为 100。可在「伙伴概率」里看到实际百分比
和每档池内数量。

**Q：为什么抽到的都是同一只？**
默认 `allow_duplicate_draw=true`，重复是正常结果并会折算星尘。想更快集齐可以关闭它，
或设置 `new_pity_after`。

**Q：和 `astrbot_plugin_pokedex` 能同时装吗？**
可以。插件标识、数据目录、注册类名与指令命名空间都不同；但两者的 `宝可梦` 等通用指令
会重叠，建议只保留一个，或在 Dashboard 的指令管理中禁用其中一个。

**Q：数据会随插件更新丢失吗？**
不会。更新只替换插件代码，不改 `data/plugin_data/astrbot_plugin_pokemon_partner/`。
卸载插件时是否删除数据取决于 AstrBot 的卸载选项，重要数据请先备份。

## 开发与测试

```bash
pip install -r requirements.txt
python tests.py
```

测试不依赖 AstrBot 运行时：核心模块直接测试，`main.py` 通过 stub 导入，检查指令/工具
注册与 docstring 参数标注。当前共 184 项检查。

代码结构：

```text
├── main.py             # 插件入口：指令、LLM 工具、事件编排
├── gacha.py            # 抽卡规则与数据模型（无 AstrBot 依赖）
├── gacha_store.py      # 收藏/次数/星尘的线程安全存储
├── partner_service.py  # 业务编排：卡池 + 存储 + 素材
├── partner_cards.py    # 伙伴系统 Pillow 卡片
├── artwork.py          # 立绘查找、下载与缓存
├── pokedex_db.py       # 本地 TSV 图鉴查询
├── battle_engine.py    # 能力值计算与对战模拟
├── card_renderer.py    # 图鉴卡片渲染
├── champions_db.py     # 冠军赛 SQLite 只读查询
├── champions_fetch.py  # 冠军赛数据抓取脚本
└── data/*.tsv          # 中文图鉴数据
```

## 致谢与许可

- 图鉴数据：[42arch/pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh)（MIT）
- 原始图鉴插件与设计参考：[ca1e/astrbot_plugin_pokedex](https://github.com/ca1e/astrbot_plugin_pokedex)（MIT）
- 抽卡玩法参考：[Whereis-Alice/astrbot_plugin_kirby_catalog](https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)（MIT）
- 官方立绘与宝可梦名称版权归任天堂 / Game Freak / The Pokémon Company 所有；本仓库不分发立绘资源。

本项目以 MIT 许可发布，详见 [LICENSE](LICENSE)。
