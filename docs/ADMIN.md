# 安装与管理

[返回首页](../README.md) · [玩法与指令](COMMANDS.md)

这份说明供机器人管理员安装插件、调整抽卡规则和管理收藏数据。日常玩法请查看[玩法与指令](COMMANDS.md)。

[安装](#安装) · [宝可梦图片](#宝可梦图片) · [管理台](#管理台) · [配置说明](#配置说明) · [备份数据](#数据目录) · [冠军赛数据](#准备冠军赛数据) · [常见问题](#常见问题)

## 安装准备

### 环境要求

- AstrBot `>=4.16,<5`
- Python 依赖：`Pillow`、`aiohttp`（见 [requirements.txt](../requirements.txt)）
- 中文系统字体（Windows 自带微软雅黑；Linux 建议安装 `fonts-noto-cjk`）

### 安装

在 AstrBot WebUI 的插件管理中选择「从仓库安装」，填入：

```text
https://github.com/Whereis-Alice/astrbot_plugin_pokemon_partner
```

也可以手动安装。下面的 Python 命令要在 AstrBot 使用的 Python 环境中执行；Docker 部署则在容器内执行：

```bash
cd /path/to/AstrBot/data/plugins
git clone https://github.com/Whereis-Alice/astrbot_plugin_pokemon_partner.git
python -m pip install -r astrbot_plugin_pokemon_partner/requirements.txt
```

安装后重载插件。若卡片里中文显示为方框，说明运行环境没有中文字体，安装
`fonts-noto-cjk` 后重启 AstrBot 即可。

### 宝可梦图片

插件运行时按需读取或下载宝可梦图片。想在没有外网的环境里使用完整卡片，
请把 [pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh) 的
`data/images` 目录下载到服务器，并在插件配置的「宝可梦图片目录」填写该目录的绝对路径：

```text
/path/to/pokemon-dataset-zh/data/images
```

目录结构应为 `official/`、`home/` 等子目录。AstrBot 跑在 Docker 里时，先把目录挂载进
容器，再填容器内路径。

不配置也能用：开启「本地立绘缺失时使用网络官方图」后，插件会按需下载官方图并缓存到
插件数据目录；关闭后卡片使用占位图，不影响任何文字功能。图片查找顺序为：配置的本地
目录 → 插件缓存 → PokéAPI 官方立绘 / HOME 图 / 普通精灵图。

闪光抽卡优先读取真实异色立绘：配置目录中的 `shiny/<四位编号>.png` 或数据集对应闪光图，
再读取独立的 `artwork/shiny/` 缓存，并按网络开关预取官方闪光立绘 / HOME 图 / 精灵图。
缺少异色图时可使用已缓存的普通图；闪光主题和收藏标记仍然保留，后续抽到时会继续尝试
补齐异色图。形态只接受精确匹配的本地图，避免将默认形态的图片套给地区形态或 Mega。

### 一键缓存全部立绘

管理员在群聊或私聊中发送：

```text
下载立绘
```

别名是 `下载全部立绘`、`缓存立绘`。插件会以 8 路并发下载当前卡池中缺失的图片，每 40 张
处理一批，并在开始、阶段性进度和完成时回报结果。任务可以重复执行：已有图片自动跳过，
中断后再次发送即可继续补齐。

缓存默认写入 AstrBot 的插件数据目录：

```text
data/plugin_data/astrbot_plugin_pokemon_partner/artwork/
```

如果配置中关闭了 `fallback_web_image`，一键下载也会拒绝执行，避免绕过网络开关。

## 管理台

安装并重载插件后，打开 AstrBot Dashboard 的插件页面，选择「宝可梦图鉴管理台」。无需额外填写服务器地址或端口，使用 AstrBot 的登录状态即可访问。

顶部可切换深色、浅色或跟随 Dashboard；桌面侧栏可折叠成图标栏，手机使用收纳菜单。
删除与丢弃修改通过页内确认框操作，兼容 Dashboard 的 iframe 沙箱；删除前明确显示目标会话或成员。

| 工作区 | 用途 |
| --- | --- |
| 数据概览 | 查看训练家数、图鉴收录、闪光数、累计抽取、今日活跃、卡池构成与数据健康状态 |
| 宝可梦图鉴 | 按名称、属性、世代、稀有度筛选 1025 只默认形态及 295 个特殊形态；详情抽屉提供立绘、种族值、特性、相性和资料，并可按升级/学习器/遗传来源搜索招式，展开查看威力、命中、PP与效果 |
| 宝可梦管理 | 按群聊/私聊会话查看成员收藏，调整星尘、今日次数和保底，导出会话 JSON 备份，重置或删除指定数据 |
| 概率与规则 | 同时查看配置权重与剔除空卡池后的实际概率、种族值阈值、经济表与各项上限 |
| 设置 | 在线修改核心参数；服务端先校验，重载失败时回滚旧配置，不会写入不可用状态 |

界面采用精灵球红、纸白和深墨色的图鉴研究站主题，支持浅色、深色与跟随系统。图鉴列表读取
本地/缓存缩略图，不为整页批量联网下载；可切换立绘网格与紧凑列表，按 `/` 快速聚焦搜索。
选中宝可梦后从右侧打开大立绘详情，并按配置补齐缺失图片。抽屉可通过关闭按钮、点击遮罩或 `Esc` 收起；移动端会占据几乎
整个屏幕，正文独立滚动。管理台与群聊指令共用同一个 `GachaStore` 文件锁和写入逻辑，因此
不会绕过会话隔离，也不会产生两份互相覆盖的数据。

「导出会话收藏」包含导出时间、会话收藏与读取这些数据所需的格式信息，不包含插件配置或密钥。
导出仅提供备份文件，暂不提供自动导入。存档写入失败时回滚本次内存与随机状态；损坏立绘
会被跳过，并在允许联网时重新缓存。

## 配置说明

### 抽卡与概率

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `gacha_enabled` | `true` | 启用抽卡、收藏和群友挑战；关闭后仍可查图鉴和进行普通对战模拟 |
| `draws_per_command` | `1` | 每次召唤固定抽取几位（1–10） |
| `daily_draw_limit` | `3` | 每日基础次数（0–100） |
| `draw_cooldown_seconds` | `3.0` | 两次召唤的最短间隔 |
| `rarity_weights` | `C55/R30/SR12/SSR3` | 四档稀有权重，按比例归一化 |
| `rare_min_bst` | `400` | R 档最低种族值总和 |
| `super_rare_min_bst` | `480` | SR 档最低种族值总和 |
| `ultra_rare_min_bst` | `580` | SSR 档最低种族值总和 |
| `ssr_pity_after` | `30` | 连续多少次未出 SSR 后必出；0 关闭 |
| `new_pity_after` | `0` | 连续多少次重复后必出新；0 关闭 |
| `allow_duplicate_draw` | `true` | 关闭后优先抽未拥有的宝可梦 |
| `include_alternate_forms` | `false` | 是否把地区形态、Mega 等计入卡池；缺少种族值的形态会跳过 |
| `shiny_chance_percent` | `1.5` | 闪光概率 |
| `duplicate_dust` | `2/6/20/80` | 重复宝可梦折算星尘 |
| `spark_costs` | `20/60/150/400` | 星尘兑换价格 |
| `shiny_dust_multiplier` | `3.0` | 闪光重复的星尘倍率 |
| `shiny_spark_multiplier` | `4.0` | 闪光兑换的价格倍率 |

三个稀有度阈值必须严格递增，否则插件会记录错误并停用宝可梦系统，图鉴功能仍然可用。

### 图片、网络与战报

想离线使用时，关闭「启用 PokéAPI 补充查询」和「本地立绘缺失时使用网络官方图」。本地图鉴、抽卡和对战仍然可用；已准备好的本地图片或缓存也会继续显示。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `images_dir` | 空 | pokemon-dataset-zh 的 `data/images` 目录 |
| `enable_image_card` | `true` | 关闭后所有图片卡片（含宝可梦卡片）返回纯文本 |
| `fallback_web_image` | `true` | 本地缺图时使用网络官方图 |
| `pokeapi_enabled` | `true` | PokéAPI 补充查询 |
| `pokeapi_timeout` | `15` | PokéAPI 超时秒数 |
| `battle_output_mode` | `card` | 对战战报：`card` 三张卡片、`card_forward` 图片合并转发、`forward` 文字合并转发、`text` 文本 |
| `wishmaker_style` | `image` | 许愿星结果：`image` 图片、`markdown` Markdown 文本 |

卡片战报由封面、双方配招与效果长图、完整对战过程长图组成，全部回合保留在同一张过程图中。
希望群聊只显示一条战报，可将「对战发送方式」设为「图片合并转发」。此方式适用于 QQ / OneBot，
会将所有战报图片收进一条合并转发；不支持的平台或转发失败时改发普通卡片，图片生成失败时回退文本。
命令末尾追加 `图片转发`（或 `卡片转发`）可以只对本次对局启用；`转发` 仍表示文字合并转发。

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
├── artwork/                # 普通图片缓存；shiny/ 为闪光图片缓存
└── champions.db            # 可选：冠军赛数据库
```

`state.json` 使用临时文件 + `os.replace` 原子写入，并保留一份 `.bak`。写入失败不会破坏
旧文件。备份或搬到其他服务器时请复制整个 `astrbot_plugin_pokemon_partner` 数据目录。

如果 `state.json` 损坏，插件会自动从 `.bak` 恢复（可能丢失最后一次写入），并把损坏
文件另存为 `state.corrupt-<时间>.json`；主文件与备份都无法读取时会停止写入并提示管理员
检查，而不是用空数据覆盖。恢复后不会再把损坏文件复制成新的备份。

写入时会额外加一把跨进程文件锁（POSIX `flock` / Windows `msvcrt`），并在落盘前合并
磁盘上的较新数据。因此插件热重载瞬间新旧实例并存、或同一份数据被两个进程打开时，
不会互相覆盖已收集的宝可梦。

群聊按群隔离，私聊按用户隔离：同一个人在 A 群和 B 群的收藏互不影响。更换群号或清空
数据目录都会重置对应收藏，操作前请备份。群聊优先使用平台群 ID；若适配器没有提供
`group_id`，会继续读取原始事件群标识或 AstrBot 会话 ID，避免误归入私聊数据。

## 准备冠军赛数据

冠军赛查询依赖 `champions.db`，插件运行时只读，不联网。抓取脚本需要额外的 Playwright：

在插件目录中，使用 AstrBot 的 Python 环境执行：

```bash
pip install -r requirements-scraper.txt
playwright install chromium
python champions_fetch.py                 # 最新赛季，单打+双打
python champions_fetch.py --top 50        # 多抓配置明细
python champions_fetch.py --season M-6    # 指定历史赛季
```

脚本默认写入 `data/plugin_data/astrbot_plugin_pokemon_partner/champions.db`，与插件读取
路径一致。如果抓取脚本使用的是其他 Python 环境，可用 `--db <数据库绝对路径>` 明确指定位置，并把管理台的「冠军赛数据库路径」设置为同一路径。
站点页面变化导致选择器失效时，可用 `--headed` 查看实际渲染并调整
`champions_fetch.py` 顶部的 `JS_*` 常量。

## 常见问题

### 抽卡只有文字，没有图片
先检查是否安装了 Pillow、系统是否有中文字体，以及 AstrBot 日志里是否有「渲染失败」。
图片下载失败时会使用占位图；整张卡片渲染失败才会回退文字，不影响抽卡结果。

### 卡片有文字，但没有宝可梦图片
没配置 `images_dir`，且网络图下载失败或已关闭 `fallback_web_image`。配置本地图片目录
即可彻底离线。

### 概率和设置的数字不一致
权重是归一化后的比例，不是百分比总和必须为 100。可在 `抽取概率` 或管理台「概率与规则」
里看到实际百分比和每档池内数量。

### 经常抽到重复的宝可梦
默认 `allow_duplicate_draw=true`，重复是正常结果并会折算星尘。想更快集齐可以关闭它，
或设置 `new_pity_after`。

### 同时安装其他图鉴插件
插件标识、数据目录、注册类名与收藏指令都不同，可以在 AstrBot 中共存；但两个插件都可能
注册通用指令 `宝可梦 <名称>`。如果需要同时安装，请在 Dashboard 的指令管理中只保留其中
一个的图鉴查询指令，避免前缀冲突。

### 更新插件时，收藏会丢失吗
不会。更新只替换插件代码，不改 `data/plugin_data/astrbot_plugin_pokemon_partner/`。
卸载插件时是否删除数据取决于 AstrBot 的卸载选项，重要数据请先备份。
