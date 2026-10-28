"""单抽卡片的布局回归：真实文本边界、长名称和内容自适应画布。"""

from __future__ import annotations

import io
import os
import tempfile
from dataclasses import replace
from unittest.mock import patch

# A locally installed AstrBot SDK may initialize logging when fonts are imported.
os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="partner-card-layout-"))

from PIL import Image, ImageDraw

from gacha import DrawItem
from partner_cards import PAD, W, render_draw_card


def render_with_text_bounds(item: DrawItem):
    bounds = []
    original = ImageDraw.ImageDraw.text

    def record(draw, xy, text, *args, **kwargs):
        font = kwargs.get("font")
        box = draw.textbbox(xy, text, font=font, anchor=kwargs.get("anchor"))
        bounds.append((str(text), box))
        return original(draw, xy, text, *args, **kwargs)

    with patch.object(ImageDraw.ImageDraw, "text", record):
        data = render_draw_card(
            [item], nickname="布局验证训练家", remaining=1, daily_limit=3,
            dust=1200, pity_ssr=28, ssr_pity_after=30,
        )
    return Image.open(io.BytesIO(data)), bounds


def assert_text_is_separate(image, bounds):
    hero = [(text, box) for text, box in bounds if box[1] >= 214]
    for text, (left, top, right, bottom) in hero:
        assert PAD <= left < right <= W - PAD, ("文本越过卡片边界", text)
        assert top < bottom < image.height - 12, ("文本被画布裁切", text)
    for index, (text, a) in enumerate(hero):
        for other, b in hero[index + 1:]:
            overlaps = max(a[0], b[0]) < min(a[2], b[2]) and max(a[1], b[1]) < min(a[3], b[3])
            assert not overlaps, ("文字相互遮挡", text, other)


def main():
    row = {
        "id": "25", "id_raw": "0025", "category": "鼠宝可梦",
        "height": "0.4m", "weight": "6.0kg", "abilities": "静电",
        "hidden_abilities": "避雷针", "profile": "性格温顺，喜欢与训练家散步。",
        "stats": "HP35/攻击55/防御40/特攻50/特防50/速度90（总和320）",
    }
    short = DrawItem("25", 25, "0025", "皮卡丘", "电", 320, "C", False, True, 0, 1, row)
    image, bounds = render_with_text_bounds(short)
    assert_text_is_separate(image, bounds)
    assert image.height < 1200, ("短资料仍有大幅留白", image.height)

    long_row = dict(row, category="鼠宝可梦" * 10, abilities="静电、避雷针、发光、同步" * 4,
                    weak_to="地面×2、火×2、冰×2、格斗×2" * 3,
                    resist_to="电×0.5、钢×0.5、毒×0.5" * 4,
                    egg_groups="陆上/妖精", gender_ratio="♂50%♀50%",
                    evolution="皮丘 →（亲密度提升等级）皮卡丘 →（雷之石）雷丘；" * 5,
                    profile="皮卡丘是一只电气鼠，全身皮毛是黄色，喜欢在树林里活动。" * 20)
    long = replace(short, name="超级长名称宝可梦（特殊地区与额外形态）" * 3,
                   types="草/毒/飞行/龙/恶", shiny=True, rarity="SSR", row=long_row)
    long_image, long_bounds = render_with_text_bounds(long)
    assert_text_is_separate(long_image, long_bounds)
    assert long_image.height > image.height, "长资料未扩展画布"
    assert long_image.height < 1800, "摘要与进化链未按行数限制"
    assert any("进化：" in text for text, _ in long_bounds), "进化链未绘制"
    assert any(text == "闪光" for text, _ in long_bounds), "闪光标记未绘制"

    empty = replace(short, row={})
    empty_image, empty_bounds = render_with_text_bounds(empty)
    assert_text_is_separate(empty_image, empty_bounds)
    assert any(text == "暂无图鉴摘要" for text, _ in empty_bounds), "缺少资料占位"
    print(f"3 layouts passed: short={image.height}, long={long_image.height}, empty={empty_image.height}")


if __name__ == "__main__":
    main()
