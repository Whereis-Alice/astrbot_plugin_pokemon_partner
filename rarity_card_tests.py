"""Daily cards keep their data, readable text, and five different compositions."""
from __future__ import annotations

import io
import os
import tempfile
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="partner-rarity-layout-"))

from PIL import Image, ImageDraw

from card_layout_tests import assert_text_is_separate
from gacha import DrawItem
from partner_cards import render_draw_card
from rarity_cards import _wrap

CONTEXT = dict(nickname="版式验证训练家", remaining=2, daily_limit=3,
               dust=1280, pity_ssr=24, ssr_pity_after=30)
ROW = {
    "id": "25", "id_raw": "0025", "category": "鼠宝可梦",
    "height": "0.4m", "weight": "6.0kg", "abilities": "静电", "hidden_abilities": "避雷针",
    "catch_rate": "190", "weak_to": "地面×2", "resist_to": "电×0.5", "immune_to": "测试免疫",
    "egg_groups": "陆上/妖精", "gender_ratio": "♂50%♀50%", "evolution": "皮丘 → 皮卡丘 → 雷丘",
    "profile": "性格温顺，喜欢与训练家散步。",
    "stats": "HP35/攻击55/防御40/特攻50/特防50/速度90（总和320）",
}
BASE = DrawItem("25", 25, "0025", "皮卡丘", "电", 320, "C", False, True, 0, 1, ROW)


def render(items, *, art=True, **context):
    records = []
    original = ImageDraw.ImageDraw.text

    def capture(draw, xy, value, *args, **kwargs):
        font = kwargs.get("font")
        box = draw.textbbox(xy, value, font=font, anchor=kwargs.get("anchor"))
        records.append((str(value), box, getattr(font, "size", 0)))
        return original(draw, xy, value, *args, **kwargs)

    # A distinctive opaque portrait tests that the actual image reaches the final JPEG.
    sprite = Image.new("RGBA", (128, 256), (233, 17, 193, 255))
    with patch.object(ImageDraw.ImageDraw, "text", capture):
        result = render_draw_card(items, image_loader=(lambda _: sprite.copy()) if art else None,
                                  **dict(CONTEXT, **context))
    return Image.open(io.BytesIO(result)).convert("RGB"), records


def artwork_bounds(image):
    pixels = image.load()
    coords = [(x, y) for y in range(0, image.height, 4) for x in range(0, image.width, 4)
              if abs(pixels[x, y][0]-233)<12 and abs(pixels[x, y][1]-17)<12 and abs(pixels[x, y][2]-193)<12]
    assert len(coords) > 500, "立绘未出现在最终图片中"
    return min(x for x, _ in coords), min(y for _, y in coords), max(x for x, _ in coords), max(y for _, y in coords)


def main():
    measurement = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for width in (160, 220, 390):
        for value, units in (("弱点：飞行×2、火×2、超能力×2、冰×2", ("飞行×2", "火×2", "超能力×2", "冰×2")),
                             ("抗性：格斗×0.5、水×0.5、草×0.25、电×0.5", ("格斗×0.5", "水×0.5", "草×0.25", "电×0.5")),
                             ("性别：♂87.5%♀12.5%", ("♂87.5%", "♀12.5%"))):
            lines = _wrap(measurement, value, 26, width)
            assert "".join(lines) == value, "换行改变了资料内容"
            for unit in units:
                assert any(unit in line for line in lines), ("属性倍率或性别被拆开", unit, lines)
    signatures = set()
    for rarity, shiny in (("C", False), ("R", False), ("SR", False), ("SSR", False), ("SSR", True)):
        image, records = render([replace(BASE, rarity=rarity, shiny=shiny)])
        assert_text_is_separate(image, [(text, box) for text, box, _ in records])
        assert image.width == 1080 and image.height < 2600
        texts = [text for text, _, _ in records]
        text = "\n".join(texts)
        for expected in ("皮卡丘", "0025", "0.4m", "6.0kg", "静电", "避雷针", "190", "地面×2",
                         "电×0.5", "测试免疫", "陆上/妖精", "性别", "皮丘", "性格温顺", "1280", "24/30", "2/3"):
            assert expected in text.replace(" ", ""), ("资料遗漏", rarity, shiny, expected)
        assert "新宝可梦已加入图鉴" in texts
        assert ("闪光" in texts) == shiny
        for value, _, size in records:
            if any(value.startswith(prefix) for prefix in ("鼠宝可梦", "特性", "捕获度", "弱点", "抗性", "免疫", "蛋群", "进化", "性格温顺")):
                assert size >= 24, ("正文过小", value, size)
        # Portrait location, title, and stat grid together prove different compositions.
        positions = tuple(box[:2] for value, box, _ in records if value in ("皮卡丘", "HP", "攻击", "速度"))
        signatures.add((artwork_bounds(image), positions))
    assert len(signatures) == 5, "稀有度只有换色，没有独立构图"

    repeat_image, repeat_records = render([replace(BASE, is_new=False, dust=80, shiny=True)], art=False, ssr_pity_after=0)
    assert_text_is_separate(repeat_image, [(text, box) for text, box, _ in repeat_records])
    texts = [text for text, _, _ in repeat_records]
    assert "重复获得 · 转化为 80 星尘" in texts and "SSR 保底关闭" in texts
    assert "立绘暂未缓存" in texts

    editions = [replace(BASE, rarity=rarity, shiny=shiny) for rarity, shiny in
                (("C", False), ("R", False), ("SR", False), ("SSR", False), ("SSR", True))]
    editions += [replace(x, name="极长宝可梦名称（特殊地区的特别形态）"*3, is_new=False, dust=120) for x in editions]
    image, records = render(editions, art=False)
    assert_text_is_separate(image, [(text, box) for text, box, _ in records])
    assert image.width == 1080 and image.height < 2600
    texts = [text for text, _, _ in records]
    assert sum(value == "NEW · 新伙伴" for value in texts) == 5
    assert sum(value == "重复 · +120星尘" for value in texts) == 5
    assert sum("…" in value for value in texts) >= 5, "长名称应明确显示截断"
    assert "2 / 3" in texts and "1280" in texts
    print(f"5 editions + repeat/missing-art + mixed 10 draws passed; mixed={image.size}")


if __name__ == "__main__":
    main()
