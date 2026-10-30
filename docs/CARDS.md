# 卡片预览

[返回首页](../README.md) · [玩法与指令](COMMANDS.md) · [安装与管理](ADMIN.md)

`今日宝可梦` 会根据稀有度显示不同的背景与材质。可在[卡面设置](CONFIGURATION.md)中选择基础卡面、[TCG 收藏卡面](#tcg-收藏卡面)或[游戏王收藏卡面](#游戏王收藏卡面)。三套均使用加长卡牌，文字区与对应的纸、晶体或金属材质自然融合。下面是插件实际生成的卡面，点击图片可放大查看。

[查看五种卡面对照图](images/cards/preview.jpg)

## C · 普通

妙蛙种子，森林背景与象牙纸材圆盘。

<a href="images/cards/common.jpg"><img src="images/cards/common.jpg" alt="C 普通：妙蛙种子卡片" width="480"></a>

## R · 稀有

妙蛙草，冰蓝晶簇与折射透镜圆盘。

<a href="images/cards/rare.jpg"><img src="images/cards/rare.jpg" alt="R 稀有：妙蛙草卡片" width="480"></a>

## SR · 史诗

卡璞·蝶蝶，紫晶金属圆盘与舞台聚光。

<a href="images/cards/epic.jpg"><img src="images/cards/epic.jpg" alt="SR 史诗：卡璞·蝶蝶卡片" width="480"></a>

## SSR · 传说

烈空坐，黑曜金质浮雕、金箔反射与星图。

<a href="images/cards/legendary.jpg"><img src="images/cards/legendary.jpg" alt="SSR 传说：烈空坐卡片" width="480"></a>

## 闪光

闪光梦幻，珍珠全息圆盘与光谱折射，优先显示真实异色立绘。

<a href="images/cards/shiny.jpg"><img src="images/cards/shiny.jpg" alt="闪光：蓝色梦幻卡片" width="480"></a>

闪光宝可梦也有自身的稀有度，闪光外观会优先显示。多抽时使用对应主题的网格卡面。

## TCG 收藏卡面

保留实体卡的金属边框、姓名与 HP 标题栏、属性能量图标、插画区和稀有度标记；加长布局让立绘、资料和相性有更充足的空间。卡内的 HP 与能力值均为本插件的种族值，抽取和对战规则照常使用。

![TCG 五种卡面对照](images/tcg/preview.jpg)

| 卡面 | 材质与大图 |
| --- | --- |
| C · 普通 | [黄边纸卡与绘本画窗](images/tcg/common.jpg) |
| R · 稀有 | [银边反闪与属性暗纹](images/tcg/rare.jpg) |
| SR · 史诗 | [全图彩箔与浮雕走线](images/tcg/epic.jpg) |
| SSR · 传说 | [金箔、蚀刻与金属颗粒](images/tcg/legendary.jpg) |
| 闪光 | [珍珠银、虹彩星面与真实异色立绘](images/tcg/shiny.jpg) |

单抽发送文字提示与一张纯卡牌，多抽将实体卡排成两列。次数、星尘、新获得或重复转化的结果统一显示在消息文字中，卡牌内不放抽卡状态或图鉴摘要续文。弱点、抗性和免疫使用属性能量图标与本篇游戏倍率，特性可能改变实际效果。

视觉参考取自公开的 Pokémon TCG 实卡图：经典[皮卡丘](https://api.tcgdex.net/v2/en/cards/base1-58)、现代[红莲铠骑](https://api.tcgdex.net/v2/en/cards/sv01-041)、[喷火龙 ex 全图](https://api.tcgdex.net/v2/en/cards/sv03-215)、[金色高稀有卡](https://api.tcgdex.net/v2/en/cards/sv03-228)与[异色皮卡丘](https://api.tcgdex.net/v2/en/cards/sv04.5-131)。卡面边框、图标、场景和印刷材质由插件本地绘制，参考实卡扫描没有加入插件。

## 游戏王收藏卡面

参考游戏王卡面的加厚外沿、凹槽姓名框、右上属性圆章、星标、金属画窗、介绍栏和右下角攻防。卡牌为 1080×1680，采用楷体姓名、薄箔字和细腻云纹；星标为 64 像素，上下留白均衡。介绍栏按种类／性别／免疫、简介、进化、抗性、弱点排列，种族值总和靠横线右端；卡内资料不会追加到消息文字。

合照用于比较样式。查看文字、材质与图标细节，请打开下表的原尺寸单卡链接。

![游戏王五种卡面对照](images/yugioh/preview.jpg)

| 卡面 | 材质与大图 |
| --- | --- |
| C · 普通 | [橙棕云纹与暖色纸面](images/yugioh/common.jpg) |
| R · 稀有 | [玫红云纹、银白姓名与淡粉介绍栏](images/yugioh/rare.jpg) |
| SR · 史诗 | [融合紫云纹与金属画窗](images/yugioh/epic.jpg) |
| SSR · 传说 | [近黑底材、轻金箔与金色姓名](images/yugioh/legendary.jpg) |
| 闪光 | [白银云纹、虹彩姓名与真实异色立绘](images/yugioh/shiny.jpg) |

18 种属性使用独立的单字圆章图标，介绍栏按 `【种类／性别／免疫】` 排列。画窗下显示特性、身高与体重；六项种族值保留，攻击／防御写在介绍栏右下角，页脚显示捕获度、蛋群和图鉴编号。

C、R、SR、SSR、闪光分别显示 2、4、6、8、12 星，不表示对战等级。单抽与多抽继续使用文字提示和纯卡牌，次数、NEW、星尘和保底留在文字中；过长的卡内资料使用省略号。

[查看参考资料与设计说明](YUGIOH_DESIGN.md)。

宝可梦形象与名称的版权归任天堂 / Game Freak / The Pokémon Company 所有。

阿罗拉猫老大的形态摘要取自[官方中文图鉴](https://www.pokemon.cn/play/pokedex/0053_1)。
