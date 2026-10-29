# 开发与测试

[返回首页](../README.md) · [玩法与指令](COMMANDS.md) · [安装与管理](ADMIN.md)

## 本地准备

在仓库目录中安装依赖：

```bash
python -m pip install -r requirements.txt
```

卡片检查需要中文字体。Windows 可使用微软雅黑；Linux 可安装 `fonts-noto-cjk`。
涉及 AstrBot 接口的开发建议使用已安装 AstrBot 的 Python 环境，或另行安装 AstrBot SDK。
前端招式列表检查需要 Node.js；冠军赛抓取的依赖见[安装与管理](ADMIN.md)。

## 按改动选择检查

| 修改内容 | 检查命令 |
| --- | --- |
| 核心图鉴、抽卡、配置与接口注册 | `python tests.py` |
| 双保底、禁止重复、形态开关与收集奖励 | `python gacha_store_regression_tests.py` |
| 今日指令的整句触发、权限与消息去重 | `python daily_draw_command_tests.py` |
| 对战计算和招式效果 | `python battle_regression_tests.py` |
| 对战卡片与消息发送方式 | `python battle_card_tests.py`、`python battle_delivery_tests.py`、`python battle_image_forward_sdk_tests.py` |
| 群友挑战、收藏与指令解析 | `python owned_duels_tests.py`、`python owned_duel_command_tests.py`、`python duel_commands_tests.py` |
| 收藏卡片的图鉴编号与分页显示 | `python dex_collection_card_tests.py` |
| 卡片排版与稀有度样式 | `python card_layout_tests.py`、`python rarity_card_tests.py`、`python rarity_visual_tests.py` |
| 今日卡面切换、配置保存与发送分流 | `python tcg_render_integration_tests.py` |
| 卡内基础属性相性、双属性倍率与免疫 | `python card_matchups_tests.py` |
| TCG 实卡比例、文字边界、闪光请求与十连 | `python tcg_card_tests.py` |
| 游戏王卡面、真实文字对比度、完整立绘、属性相性与十连 | `python yugioh_card_tests.py` |
| 场景、精灵球圆盘与闪光图片缓存 | `python rarity_scene_tests.py`、`python rarity_emblem_tests.py`、`python shiny_artwork_tests.py` |
| 图片下载大小限制与备用图源 | `python image_download_tests.py` |
| 形态立绘匹配、独立缓存与缺图日志 | `python artwork_form_tests.py` |
| 管理台操作、设置保存与招式查询 | `python webui_action_tests.py`、`python webui_learnset_tests.py` |
| 前端招式搜索与展开 | `node --test pages/partner-console/js/learnset.test.mjs` |

这些检查使用本地数据、临时存档和模拟消息，不需要连接在线 AstrBot 服务或向真实群聊发送消息。
核心模块直接测试；入口与部分指令检查使用模拟 AstrBot 接口。已安装的 SDK 可能在导入时
初始化日志，因此测试通过 `ASTRBOT_ROOT` 隔离运行目录。自行指定该变量时，请使用测试专属目录。

卡片测试检查实际文字边界、对比度、立绘末端、长名称和多抽布局，并确认五种材质的差异。
需要生成带真实立绘的样张时，可使用：

```bash
python rarity_visual_tests.py --output /path/to/review --art-dir /path/to/review-art
```

`review-art` 中需准备 `0001.png`、`0025.png`、`0448.png`、`0384.png`，以及
`shiny/0151.png`。此检查只读取现有图片，不自动下载。输出包括样张和验证报告。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `main.py` | 插件入口、指令、模型工具与消息编排 |
| `gacha.py` | 抽卡规则与数据模型 |
| `gacha_store.py` | 收藏、次数、星尘和战绩的存储 |
| `partner_service.py` | 卡池、存储与素材的业务编排 |
| `owned_duels.py`、`duel_commands.py` | 群友挑战事务与指令解析 |
| `partner_cards.py`、`rarity_cards.py`、`tcg_cards.py` | 收藏及抽卡卡片排版，现有卡面与 TCG 卡面分流 |
| `rarity_scenes.py`、`rarity_emblems.py` | 稀有度场景与精灵球圆盘材质 |
| `tcg_materials.py`、`tcg_symbols.py` | TCG 纸面、彩箔、雕纹与 18 种属性能量图标 |
| `yugioh_cards.py`、`yugioh_materials.py` | 游戏王怪兽卡构图、五档纸面／箔材质，复用 18 种属性能量图标 |
| `yugioh_fonts.py`、`assets/fonts/` | 随插件附带的宋明衬线字库、字体加载与 OFL 许可 |
| `artwork.py` | 普通与闪光立绘的查找、预取和缓存 |
| `webui.py`、`pages/` | 管理台接口、页面与样式 |
| `pokedex_db.py`、`data/*.tsv` | 本地中文图鉴数据查询 |
| `battle_engine.py` | 能力值公式与对战模拟 |
| `battle_report.py`、`battle_cards.py` | 等级配招、效果说明、逐回合战报与卡片 |
| `card_renderer.py` | 图鉴卡片与字体查找 |
| `champions_db.py`、`champions_fetch.py` | 冠军赛数据库查询与独立抓取 |
| `wishmaker_card.py` | 许愿星结果卡片 |
| `logo.png`、`logo.svg` | 插件图标 |

卡片渲染接收普通数据，只读图片缓存；网络下载在异步预取阶段完成。
`daily_card_style` 只影响今日宝可梦单抽和多抽，默认 `classic`；`tcg` 和 `yugioh` 使用各自的独立渲染模块。
消息入口每次读取当前配置，切换样式不会重新抽取、迁移收藏或改变闪光判定。
修改玩法时保持群聊/私聊的数据隔离，修改消息输出时保留图片或转发失败后的文本回退。

## 数据来源与许可

- 中文图鉴数据：[42arch/pokemon-dataset-zh](https://github.com/42arch/pokemon-dataset-zh)（MIT）。
- 图鉴插件与设计参考：[ca1e/astrbot_plugin_pokedex](https://github.com/ca1e/astrbot_plugin_pokedex)（MIT）。
- 抽卡玩法参考：[Whereis-Alice/astrbot_plugin_kirby_catalog](https://github.com/Whereis-Alice/astrbot_plugin_kirby_catalog)（MIT）。

本项目代码采用 MIT 许可，详见 [LICENSE](../LICENSE)。宝可梦形象、官方立绘与名称的版权
归任天堂、Game Freak 和 The Pokémon Company 所有。文档预览用于展示插件界面；运行时
立绘由用户提供，或根据插件设置从官方图源缓存。
