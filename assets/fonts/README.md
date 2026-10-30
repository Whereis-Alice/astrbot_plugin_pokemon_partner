# 卡片字体与名称图像

游戏王收藏卡面的依据是用户指定的[《游戏王：决斗链接》官网卡盒图鉴](https://ygo.163.com/card/)，实际核对了官网的[青眼白龙](https://ygo.res.netease.com/pc/gw/cards/4007.png)、[黑魔术师](https://ygo.res.netease.com/pc/gw/cards/4041.png)和[岩石巨兵](https://ygo.res.netease.com/pc/gw/cards/4080.png)卡图。卡名采用扁阔的隶书方向；属性章采用修长、带尖锐收笔的宋／明体方向。网页 UI 字体与中文 DIY 制卡器的默认字体不作为这两处的依据。

## 完整名称图像

`../yugioh/names.zip` 内是 1595 个完整宝可梦名称或形态短语的 PNG 遮罩及 `manifest.json`，覆盖当前 `pokemon.tsv` 全部 **1320 条**数据的实际抽卡显示名，也包含基础名和单独形态名。它们使用本机 Windows 自带 **LiSu（隶书）**一次性印制；插件不包含该字体程序，不包含可组合单字的位图字库。它的扁阔结构、厚横与波磔笔画用于接近上述官网卡名，未把它宣称为官方指定字体。

名称图使用设计基准字号 198、实际印制字号 264，提供常用卡名字号 66 的三倍采样。PNG 采用 16 级灰度存储抗锯齿覆盖率，运行时恢复为 L 模式遮罩；灰度量化仅改变抗锯齿边缘，不改变原始轮廓。所有图片保持完整词句，按完整名称的 SHA-256 摘要命名，实际索引仍保存原名称文字。四个「卡璞・…」名称的 JIS 中点印制成 LiSu 原生的同形常用中点「·」，索引和显示名称字符串不被改写。

[Microsoft 字体 FAQ](https://learn.microsoft.com/en-us/typography/fonts/font-faq)在游戏／应用图像条目中明确写道：

> Can I include graphic files (eg. GIFs, PNGs or JPEGs) I create using the fonts in my game or apps, say for a logo or banner?
>
> Yes, you can (provided you're using a product that is not specifically licensed for home, student or non-commercial use). The graphic file must be an image of a word, phrase or passage of text. Converting the font to a bitmap font (where each letter is treated individually) is not allowed.

此处只分发完整名称图片，遵守上述图像输出规则。不要把它们拆成可任意拼字的字符资产，也不要随插件复制 `SIMLI.TTF`。新增图鉴名称时，应在具有该 Windows 字体使用权的开发环境重新印制完整短语并更新清单。运行环境无需 Windows、已安装字体、网络或解压目录；加载器在内存中读取 ZIP，图像按需缓存。

## 运行时字库

正文与攻击／防御横栏使用 [Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc) 静态字体子集，分别采用 500 和 800 字重。`PartnerCardSerif-Medium.ttf` 与 `PartnerCardSerif-ExtraBold.ttf` 保留插件 Python、图鉴 TSV 与页面文案使用的全部可用字形，并加入 GB2312、ASCII、常用标点、倍率、箭头与性别符号。常用简体中文及当前图鉴名称、特性、进化条件、摘要均可离线显示。

属性章的独立 `symbol` 角色为 `PartnerDuelSymbols-Regular.ttf`，同样源于 **Noto Serif SC 500**，仅保留 18 个属性单字和问号，共 19 个码点。其直笔、斜横与尖锐收笔对应官网属性章的宋／明体方向；所有 19 个圆章均用此字库重制。字体改变不影响属性章已有的上亮极、暗腰与下反光渐变。

未列入名称图清单的自定义文字使用可随插件再分发的 [LXGW WenKai Regular（霞鹜文楷）](https://github.com/lxgw/LxgwWenKai) 姓名子集 `PartnerDuelKai-Regular.ttf`，共 1151 个码点，覆盖记录在 `name-coverage.txt`。文楷仅作为回退字库；已知名称首先使用完整隶书名称图。若自定义文字超出其字符集，则整行回退到 Noto 500，避免同一行缺字或混用字体。

上述 TTF 均使用 **SIL Open Font License 1.1**，可以随插件再分发。修改后的字体族名为 Partner Duel Kai、Partner Card Serif 与 Partner Duel Symbols；版权信息保留在字体元数据及 [LXGW WenKai 许可](OFL-LXGWWenKai.txt)、[Noto Serif SC 许可](OFL-NotoSerifSC.txt)中。TTF 文件始终按 OFL 授权，不归入插件代码的 MIT 许可。运行时只需 Pillow，fontTools 仅用于开发时的子集准备。
