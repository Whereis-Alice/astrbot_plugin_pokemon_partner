# 游戏王卡面参考与设计

[卡片预览](CARDS.md#游戏王收藏卡面) · [卡面设置](CONFIGURATION.md)

游戏王卡片用姓名凹槽、属性圆章、星标、近方形画窗、描述框和右下角攻击／防御形成清晰的阅读顺序。游戏王的卡纸颜色区分卡片类型，稀有工艺则通过银字、金字和闪箔等表现。

本插件以用户提供的五张参考素材为主要依据，采用 1080×1680 的卡牌布局。外框使用参考素材中的细腻印刷云纹，姓名框与画窗保留明暗压边；正文承载面延续同类云纹，并减弱对比以便阅读。姓名、正文与数字采用随插件附带的开放衬线字体。

| 收藏档位 | 参考与实现 |
| --- | --- |
| C | 橙棕云纹纸面、浅色姓名与金属画窗，参考暖色怪兽卡 |
| R | 玫红云纹纸面、银白姓名与淡粉介绍栏，参考陷阱卡纸色 |
| SR | 融合紫云纹纸面、深色姓名与淡紫介绍栏，参考融合怪兽卡 |
| SSR | 近黑底材、轻金箔与金色姓名，配浅色介绍栏，参考黑色怪兽卡和金字工艺 |
| 闪光 | 白银云纹上叠加克制虹彩，姓名使用彩色箔字，画窗保留闪光质感 |

这些是本插件的视觉组合，不与游戏王实际卡片类型或稀有等级一一对应。C、R、SR、SSR、闪光依次使用 2、4、6、8、12 星，表示收藏档位。卡面不再在星标旁写稀有度说明，抽取结果仍会在聊天文字中标明稀有度。

## 信息布局

- 右上角与属性相性使用游戏王风格的单字圆珠图标，18 种宝可梦属性各自独立；图标旁不再重复属性名称。
- 画窗下方显示特性（含隐藏特性）、身高和体重。
- 介绍栏以 `【属性图标／分类／性别】` 开头；无性别种族显示“无性别”，其余显示图鉴中的性别比例。
- 正文容纳图鉴摘要、进化资料与弱点、抗性、免疫。相性图标后的数字保留本篇游戏的实际倍率。
- 六项种族值均保留，其中 `ATK / 数值  DEF / 数值` 放在介绍栏右下角；最下方页脚显示捕获度、蛋群和图鉴编号。

过长的特性、分类、进化等实用资料会完整补充到消息文字。卡牌只显示宝可梦资料，次数、NEW、星尘和保底放在聊天文字中。卡面切换不改变抽卡、收藏或对战规则。

## 官方参考

- [游戏王官方入门与稀有度说明](https://www.yugioh-card.com/en/about/new-to-ygo/)
- [官方规则书](https://www.yugioh-card.com/en/downloads/rulebook/SD_RuleBook_EN_10.pdf)：怪兽卡各信息区及普通、效果、仪式、融合、同调、超量等卡纸
- [25th Anniversary Rarity Collection II](https://www.yugioh-card.com/en/products/ra02/)：闪箔、Collector、3D 浮雕和 Quarter Century 工艺
- [Quarter Century Bonanza](https://www.yugioh-card.com/en/products/qcb/)：全卡稀有工艺与印刷分层
- 官方数据库卡图：[青眼白龙](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4007&request_locale=en)、[黑魔术师](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4041&request_locale=en)、[星尘龙](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=7734&request_locale=en)、[黑魔术女孩](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4766&request_locale=en)

五档底材取自用户提供的 `C.png`、`R.png`、`SR.png`、`SSR.png`、`闪光.png` 中不含文字、角色或标记的局部背景，星球图标取自 `C.png`。属性圆章另行绘制，排版与边框由 Pillow 合成。插件未打包完整参考卡、原卡角色或发行商标识；取样范围与资产来源见[素材说明](../assets/yugioh/README.md)。宝可梦立绘继续通过原有素材缓存读取。

## 字体

标题与攻击／防御数字使用厚重宋明衬线，正文使用中等字重，保持实体卡的印刷感和缩略图可读性。字库基于 [Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc)，按插件实际中文资料和 GB2312 常用字符制作静态子集，改名为 Partner Card Serif；附带 [OFL 许可](../assets/fonts/OFL-NotoSerifSC.txt)和[字库说明](../assets/fonts/README.md)，两种字重随插件离线使用。
