# 卡片衬线字体

游戏王收藏卡面使用基于 [Noto Serif SC](https://github.com/google/fonts/tree/main/ofl/notoserifsc) 的静态字体子集，采用 500 字重正文与 800 字重标题、攻击／防御横栏。原字体属于 Noto / Source Han Serif 宋明衬线字族，来自 Google Fonts 的官方字体库。

字体使用 [SIL Open Font License 1.1](OFL-NotoSerifSC.txt)，可以随插件再分发。修改后的字体族名为 **Partner Card Serif**；原作者、版权和许可信息保留在字体元数据与许可证文件内。

子集保留插件 Python、图鉴 TSV 与页面文案使用的全部可用字形，并加入 GB2312 标准字符集、ASCII、常用标点、属性倍率、方向箭头与性别符号。常用简体中文及当前图鉴的名称、特殊形态、特性、进化条件、摘要均可离线显示。

`PartnerCardSerif-Medium.ttf` 为 500 字重，`PartnerCardSerif-ExtraBold.ttf` 为 800 字重。两者是普通静态 TTF，Windows 和 Linux 均直接通过 Pillow 读取，不依赖系统安装字体或联网下载；运行时无需 fontTools。

新增图鉴文字时，应在开发环境以 fontTools 重新检查字形覆盖并生成子集。字体文件不得被归入代码的 MIT 许可，其许可始终为 OFL。
