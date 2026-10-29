"""Daily cards keep their data, original reading order, and distinct materials."""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import replace
from unittest.mock import patch

os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="partner-rarity-layout-"))

from PIL import Image, ImageDraw

from rarity_visual_tests import (
    TextRecord,
    assert_original_elements,
    capture_render,
    scene_edges,
)
from card_layout_tests import assert_text_is_separate
from gacha import DrawItem
from partner_cards import render_draw_card
from rarity_cards import _wrap

CONTEXT = dict(
    nickname="版式验证训练家",
    remaining=2,
    daily_limit=3,
    dust=1280,
    pity_ssr=24,
    ssr_pity_after=30,
)
ROW = {
    "id": "25",
    "id_raw": "0025",
    "category": "鼠宝可梦",
    "height": "0.4m",
    "weight": "6.0kg",
    "abilities": "静电",
    "hidden_abilities": "避雷针",
    "catch_rate": "190",
    "weak_to": "地面×2",
    "resist_to": "电×0.5",
    "immune_to": "测试免疫",
    "egg_groups": "陆上/妖精",
    "gender_ratio": "♂50%♀50%",
    "evolution": "皮丘 → 皮卡丘 → 雷丘",
    "profile": "性格温顺，喜欢与训练家散步。",
    "stats": "HP35/攻击55/防御40/特攻50/特防50/速度90（总和320）",
}
BASE = DrawItem("25", 25, "0025", "皮卡丘", "电", 320, "C", False, True, 0, 1, ROW)


def render(items, *, art=True, capture_composites=None, **context):
    # A distinctive opaque portrait tests that the actual image reaches the final JPEG.
    sprite = Image.new("RGBA", (128, 256), (233, 17, 193, 255))
    image, observations = capture_render(
        items,
        (lambda _: sprite.copy()) if art else None,
        record_composites=capture_composites,
        **dict(CONTEXT, **context),
    )
    return image, [(record.value, record.box, record.size) for record in observations]


def artwork_bounds(image):
    pixels = image.load()
    coords = [
        (x, y)
        for y in range(0, image.height, 4)
        for x in range(0, image.width, 4)
        if abs(pixels[x, y][0] - 233) < 12
        and abs(pixels[x, y][1] - 17) < 12
        and abs(pixels[x, y][2] - 193) < 12
    ]
    assert len(coords) > 500, "立绘未出现在最终图片中"
    return (
        min(x for x, _ in coords),
        min(y for _, y in coords),
        max(x for x, _ in coords),
        max(y for _, y in coords),
    )


def main():
    measurement = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    for width in (160, 220, 390):
        for value, units in (
            (
                "弱点：飞行×2、火×2、超能力×2、冰×2",
                ("飞行×2", "火×2", "超能力×2", "冰×2"),
            ),
            (
                "抗性：格斗×0.5、水×0.5、草×0.25、电×0.5",
                ("格斗×0.5", "水×0.5", "草×0.25", "电×0.5"),
            ),
            ("性别：♂87.5%♀12.5%", ("♂87.5%", "♀12.5%")),
        ):
            lines = _wrap(measurement, value, 26, width)
            assert "".join(lines) == value, "换行改变了资料内容"
            for unit in units:
                assert any(unit in line for line in lines), (
                    "属性倍率或性别被拆开",
                    unit,
                    lines,
                )
    structures = {}
    for rarity, shiny in (
        ("C", False),
        ("R", False),
        ("SR", False),
        ("SSR", False),
        ("SSR", True),
    ):
        circular_assets = []
        image, records = render(
            [replace(BASE, rarity=rarity, shiny=shiny)],
            capture_composites=circular_assets,
        )
        assert_text_is_separate(image, [(text, box) for text, box, _ in records])
        assert image.width == 1080 and image.height < 2600
        texts = [text for text, _, _ in records]
        text = "\n".join(texts)
        for expected in (
            "皮卡丘",
            "0025",
            "0.4m",
            "6.0kg",
            "静电",
            "避雷针",
            "190",
            "地面×2",
            "电×0.5",
            "陆上/妖精",
            "性别",
            "皮丘",
            "性格温顺",
        ):
            assert expected in text.replace(" ", ""), (
                "资料遗漏",
                rarity,
                shiny,
                expected,
            )
        for removed in ("今日宝可梦", "训练家·", "今日剩余", "星尘", "保底", "NEW", "新宝可梦已加入"):
            assert removed not in text, ("抽卡状态未移出卡面", removed)
        assert image.size == (1080, 1800)
        assert ("闪光" in texts) == shiny
        for value, _, size in records:
            if any(
                value.startswith(prefix)
                for prefix in (
                    "鼠宝可梦",
                    "特性",
                    "捕获度",
                    "弱点",
                    "抗性",
                    "免疫",
                    "蛋群",
                    "进化",
                    "性格温顺",
                )
            ):
                assert size >= 28, ("正文过小", value, size)
        observations = [
            TextRecord(value, box, size, 21) for value, box, size in records
        ]
        assert_original_elements(image, observations, circular_assets)
        structures["SHINY" if shiny else rarity] = scene_edges(
            image, observations, artwork_bounds(image)
        )
    # The user's original left/right frame is shared. Remove hue, glyphs and
    # portrait pixels before checking that the scene/disc structures differ.
    from PIL import ImageChops

    for index, (kind, first) in enumerate(structures.items()):
        for other, second in list(structures.items())[index + 1 :]:
            difference = ImageChops.difference(first, second)
            ratio = 1 - difference.histogram()[0] / (
                difference.width * difference.height
            )
            assert ratio >= 0.015, (
                "稀有度只有换色，没有独立场景或圆盘",
                kind,
                other,
                ratio,
            )

    repeat_image, repeat_records = render(
        [replace(BASE, is_new=False, dust=80, shiny=True)], art=False, ssr_pity_after=0
    )
    assert_text_is_separate(
        repeat_image, [(text, box) for text, box, _ in repeat_records]
    )
    texts = [text for text, _, _ in repeat_records]
    assert not any("星尘" in value or "保底" in value or "重复" in value for value in texts)
    assert "立绘暂未缓存" in texts

    reference, _ = render([BASE])
    different_context, _ = render([replace(BASE, is_new=False, dust=120)],
                                  nickname="其他训练家", remaining=0, daily_limit=999,
                                  dust=987654, pity_ssr=888, ssr_pity_after=0)
    assert reference.tobytes() == different_context.tobytes(), "抽取上下文改变了纯卡牌"
    missing, _ = capture_render([BASE], None, **CONTEXT)
    transparent, _ = capture_render([BASE], lambda _: Image.new("RGBA", (128, 256)), **CONTEXT)
    assert missing.tobytes() == transparent.tobytes(), "透明立绘没有按缺图处理"

    editions = [
        replace(BASE, rarity=rarity, shiny=shiny)
        for rarity, shiny in (
            ("C", False),
            ("R", False),
            ("SR", False),
            ("SSR", False),
            ("SSR", True),
        )
    ]
    editions += [
        replace(
            x, name="极长宝可梦名称（特殊地区的特别形态）" * 3, is_new=False, dust=120
        )
        for x in editions
    ]
    image, records = render(editions, art=False)
    assert_text_is_separate(image, [(text, box) for text, box, _ in records])
    # Two columns keep each complete card; bounds are checked after scaling.
    assert image.size == (1080, 4343)
    texts = [text for text, _, _ in records]
    assert sum(value.startswith("#0025") for value in texts) == 10
    assert not any("NEW" in value or "星尘" in value for value in texts)
    assert sum("…" in value for value in texts) >= 5, "长名称应明确显示截断"
    assert "2 / 3" not in texts and "1280" not in texts
    for required in ("宝可梦资料", "图鉴摘要", "弱点", "抗性", "免疫", "HP", "攻击", "速度"):
        assert texts.count(required) == 10, ("十连缺少完整卡片", required)
    overflow_item = replace(BASE, row=dict(ROW, evolution="皮丘 → 皮卡丘 → 雷丘；" * 30))
    notes = []
    capture_render([overflow_item, BASE], None, details_out=notes, **CONTEXT)
    assert any(overflow_item.row["evolution"] in note for note in notes), "多抽未收集完整溢出资料"
    print(
        f"5 editions + repeat/missing-art + mixed 10 draws passed; mixed={image.size}"
    )


if __name__ == "__main__":
    main()
