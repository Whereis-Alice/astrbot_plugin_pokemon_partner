# 游戏王卡面参考与设计

[卡片预览](CARDS.md#游戏王收藏卡面) · [卡面设置](CONFIGURATION.md)

游戏王卡片用姓名凹槽、属性圆章、星标、近方形画窗、描述框和右下角攻击／防御形成清晰的阅读顺序。游戏王的卡纸颜色区分卡片类型，稀有工艺则通过银字、金字和闪箔等表现。

本插件以用户提供的五张参考素材为主要依据，采用 1080×1680 的卡牌布局。加宽的灰色外沿、姓名框、金属画窗和介绍框保留参考素材的印刷纹理、明暗压边与接触阴影；正文承载面延续同类云纹，并减弱对比以便阅读。姓名、正文与数字采用随插件附带的开放衬线字体。

| 收藏档位 | 参考与实现 |
| --- | --- |
| C | 橙棕云纹纸面、浅色姓名与金属画窗，参考暖色怪兽卡 |
| R | 玫红云纹纸面、银白姓名与淡粉介绍栏，参考陷阱卡纸色 |
| SR | 融合紫云纹纸面、深色姓名与淡紫介绍栏，参考融合怪兽卡 |
| SSR | 近黑底材、轻金箔与金色姓名，配浅色介绍栏，参考黑色怪兽卡和金字工艺 |
| 闪光 | 白银云纹上叠加虹彩反射，姓名使用彩色箔字，画窗保留细碎闪箔质感 |

这些是本插件的视觉组合，不与游戏王实际卡片类型或稀有等级一一对应。C、R、SR、SSR、闪光依次使用 2、4、6、8、12 星，表示收藏档位。卡面不再在星标旁写稀有度说明，抽取结果仍会在聊天文字中标明稀有度。

## 信息布局

- 右上角与属性相性使用游戏王风格的单字圆珠图标，18 种宝可梦属性各自独立；图标旁不再重复属性名称。
- 画窗下方显示特性（含隐藏特性）、身高和体重。
- 介绍栏以 `【种类／性别／免疫】` 开头；免疫使用属性图标，无性别种族显示“无性别”，其余显示图鉴中的性别比例。自身属性只在右上角出现。
- 简介保留最多空间，与随后的进化资料在正文区域内上下居中；下方依次显示抗性、弱点。相性图标后的数字保留本篇游戏的实际倍率。
- 种族值总和靠近分隔线右端；四维以小浅字置于底行左侧，`ATK / 数值  DEF / 数值` 放在同一底行右侧。页脚显示捕获度、蛋群和图鉴编号，不附加角标。

过长的卡内资料在卡内截断，不追加到消息文字。聊天仍使用原有抽卡提示，包含次数、NEW、星尘和保底。卡面切换不改变抽卡、收藏或对战规则。

## 官方参考

- [游戏王官方入门与稀有度说明](https://www.yugioh-card.com/en/about/new-to-ygo/)
- [官方规则书](https://www.yugioh-card.com/en/downloads/rulebook/SD_RuleBook_EN_10.pdf)：怪兽卡各信息区及普通、效果、仪式、融合、同调、超量等卡纸
- [25th Anniversary Rarity Collection II](https://www.yugioh-card.com/en/products/ra02/)：闪箔、Collector、3D 浮雕和 Quarter Century 工艺
- [Quarter Century Bonanza](https://www.yugioh-card.com/en/products/qcb/)：全卡稀有工艺与印刷分层
- 官方数据库卡图：[青眼白龙](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4007&request_locale=en)、[黑魔术师](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4041&request_locale=en)、[星尘龙](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=7734&request_locale=en)、[黑魔术女孩](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4766&request_locale=en)

五档底材取自用户提供的 `C.png`、`R.png`、`SR.png`、`SSR.png`、`闪光.png` 中不含文字、角色或标记的局部背景。边框与星球图标取自 `C.png` 的干净局部，画窗光线来自 `C.png` 和 `R.png`。属性圆章另行绘制，排版由 Pillow 合成。插件未打包完整参考卡、原卡角色或发行商标识；取样范围与资产来源见[素材说明](../assets/yugioh/README.md)。宝可梦立绘继续通过原有素材缓存读取。

## 字体

姓名采用楷体方向的独立字库，SSR 与闪光另叠加薄箔微纹、窄反光和压印阴影；攻击／防御使用厚重衬线数字，正文使用中等字重。字体随插件离线提供，覆盖当前图鉴与形态名称；来源、字形覆盖和开放许可见[字库说明](../assets/fonts/README.md)。

公开中文 DIY 制卡器有以下可核验的字体配置：

| 语言与用途 | 字体 | 配置来源 |
| --- | --- | --- |
| 简体：卡名、描述、种族与类型 | 华康楷体 W5（DFPKaiW5-GB） | [ygo-card 简中配置](https://github.com/yamiyang/ygo-card/blob/HEAD/source/config/cnSimplifyConfig.js) |
| 正体：卡名、分类行与正文 | 华康隶书 W5（DFLiShuW5-B5） | [card-maker 语言配置](https://github.com/linziyou0601/yugioh-card-maker/blob/HEAD/static/lang.card_meta.json)及[字体映射](https://github.com/linziyou0601/yugioh-card-maker/blob/HEAD/static/fonts/font-face.css) |

这些是民间制卡工具的实际用法，尚不能据此确认 Konami 官方采用的字库。本插件借鉴简中楷体方向，用开放许可的霞鹜文楷 Regular 制作姓名子集；正文保留宋体，以适应较多宝可梦资料的小字阅读。

星球图标为 64 像素，星级栏扩大后按可见圆形的边界均衡上下空隙。闪光姓名区使用更克制的银白纸纹，将虹彩集中到画窗与边缘。简介与进化按实际文字高度居中，不把末行额外行距计入；特性和身高体重按各自字形高度对齐。
