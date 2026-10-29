# 游戏王卡面参考与设计

[卡片预览](CARDS.md#游戏王收藏卡面) · [卡面设置](CONFIGURATION.md)

游戏王卡片用姓名凹槽、属性圆章、星轨、近方形画窗、描述框和底部攻击／防御横栏形成清晰的阅读顺序。游戏王的怪兽卡色区分卡片类型，稀有工艺则通过银字、金字、闪箔、浮雕和全卡棱镜体现。

本插件保留这些构图要素，并针对宝可梦中文资料加长卡牌。标题和画窗使用有方向的金属压边，正文使用与外框配套的纸、晶体或珍珠底色；纹理在文字区减弱，仍保留缩小后可辨的纸压纹、雕版、晶体或闪箔，保持小屏阅读。姓名、正文与数字采用随插件附带的开放衬线字体。

| 收藏档位 | 参考与实现 |
| --- | --- |
| C | 普通怪兽的暖赭纸面、细纤维和深褐古画框；低光泽 |
| R | 效果怪兽的古铜纸色、银字与金属丝纹；画窗保持克制的反射 |
| SR | 仪式怪兽的蓝色卡纸、玻璃折射、环形法阵与银质压边 |
| SSR | 超量怪兽的黑色卡纸、金字与高稀有工艺；用天球金刻线和星点拉开空间 |
| 闪光 | Starlight、Prismatic 与收藏工艺的全卡虹箔、横纵光线和稀疏钻点；正文使用低振幅铂色与深紫字 |

这些是本插件的视觉组合，不与游戏王实际卡片类型或稀有等级一一对应。五档星标依次为 2、4、6、8、10，表示收藏稀有度，属性圆章表示宝可梦真实属性；底部攻击／防御显示真实种族值。原有抽卡和对战计算继续使用宝可梦规则。

## 官方参考

- [游戏王官方入门与稀有度说明](https://www.yugioh-card.com/en/about/new-to-ygo/)
- [官方规则书](https://www.yugioh-card.com/en/downloads/rulebook/SD_RuleBook_EN_10.pdf)：怪兽卡各信息区及普通、效果、仪式、融合、同调、超量等卡纸
- [25th Anniversary Rarity Collection II](https://www.yugioh-card.com/en/products/ra02/)：闪箔、Collector、3D 浮雕和 Quarter Century 工艺
- [Quarter Century Bonanza](https://www.yugioh-card.com/en/products/qcb/)：全卡稀有工艺与印刷分层
- 官方数据库卡图：[青眼白龙](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4007&request_locale=en)、[黑魔术师](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4041&request_locale=en)、[星尘龙](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=7734&request_locale=en)、[黑魔术女孩](https://www.db.yugioh-card.com/yugiohdb/card_search.action?ope=2&cid=4766&request_locale=en)

边框、星标、场景和材质由 Pillow 本地绘制。参考卡片扫描与发行商认证标记没有加入插件；立绘继续通过原有宝可梦素材缓存读取。

## 字体

标题与攻击／防御数字使用厚重宋明衬线，正文使用中等字重，保持实体卡的印刷感和缩略图可读性。字库基于 [Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc)，按插件实际中文资料和 GB2312 常用字符制作静态子集，改名为 Partner Card Serif；附带 [OFL 许可](../assets/fonts/OFL-NotoSerifSC.txt)和[字库说明](../assets/fonts/README.md)，两种字重随插件离线使用。
