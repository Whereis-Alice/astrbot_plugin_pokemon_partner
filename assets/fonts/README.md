# 卡片字体

游戏王收藏卡面的姓名使用基于 [LXGW WenKai（霞鹜文楷）](https://github.com/lxgw/LxgwWenKai) Regular 的楷体子集，让撇捺、收笔和字形重心更接近简体中文制卡常见的楷书风格。正文与攻击／防御横栏使用 [Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc) 的静态字体子集，分别采用 500 和 800 字重，保持小字清晰度。

字库均使用 SIL Open Font License 1.1，可以随插件再分发。姓名与正文的修改后字族分别名为 **Partner Duel Kai** 和 **Partner Card Serif**；原作者、版权和许可信息保留在字体元数据及 [LXGW WenKai 许可](OFL-LXGWWenKai.txt)、[Noto Serif SC 许可](OFL-NotoSerifSC.txt)中。文楷源于 Fontworks Klee One；此处使用新的字族名称遵守原字体的保留名称要求。

正文字库保留插件 Python、图鉴 TSV 与页面文案使用的全部可用字形，并加入 GB2312 标准字符集、ASCII、常用标点、属性倍率、方向箭头与性别符号。常用简体中文及当前图鉴的名称、特殊形态、特性、进化条件、摘要均可离线显示。

姓名字库 `PartnerDuelKai-Regular.ttf` 保留全部 1320 条图鉴／形态数据使用的名称、属性单字、ASCII 及必要标点，共 1151 个码点，约 489 KiB。「椪」、两种中点及性别符号均由文楷原生字形覆盖，无需混入其他字体轮廓。实际覆盖字符记录在 `name-coverage.txt`。

属性宝珠使用独立的 `symbol` 字体角色：`PartnerDuelSymbols-Regular.ttf`，源于 [ZCOOL XiaoWei](https://github.com/google/fonts/tree/main/ofl/zcoolxiaowei)，仅保留 18 个属性单字和问号，共 19 个码点。修改后字族名为 **Partner Duel Symbols**，许可见 [ZCOOL XiaoWei 许可](OFL-ZCOOLXiaoWei.txt)。它保持预制属性图标的原有字形，重新生成图标时不会随卡名字体变化。

姓名的测量和绘制均传入完整文字。遇到姓名子集之外的自定义文字时，整行回退到包含常用简体中文的 Noto 500 字库，避免缺字或同一姓名内混用字形；普通资料仍始终使用正文字库。

`PartnerCardSerif-Medium.ttf` 为 500 字重，`PartnerCardSerif-ExtraBold.ttf` 为 800 字重。两者是普通静态 TTF，Windows 和 Linux 均直接通过 Pillow 读取，不依赖系统安装字体或联网下载；运行时无需 fontTools。

新增图鉴文字时，应在开发环境以 fontTools 重新检查字形覆盖并生成子集。字体文件不得被归入代码的 MIT 许可，其许可始终为 OFL。

字形研究依据是公开中文 DIY 制卡器的配置与字库元数据，并非 Konami 官方字体声明。例如 [yamiyang/ygo-card 的简中模板](https://github.com/yamiyang/ygo-card/blob/HEAD/source/config/cnSimplifyConfig.js) 将卡名、描述、种族／类型共同映射到 `cn-simplify`，对应字库名为 `DFPKaiW5-GB`（华康楷体 W5）；本插件只借鉴其姓名的楷体方向，不打包该商业字体，也不宣称文楷是官方卡牌字体。
