"""Public card regressions for imported single-line fields and paragraph breaks."""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace

_runtime = tempfile.TemporaryDirectory(prefix="partner-card-text-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

from PIL import Image, ImageDraw

from gacha import DrawItem
from partner_cards import render_draw_card
import partner_cards
import rarity_cards
import tcg_cards
import yugioh_cards


class CardTextTests(unittest.TestCase):
    def item(self):
        return DrawItem("25", 25, "0025", "皮卡丘 测试 名", "电", 320,
            "R", False, True, 0, 1, {
                "name_zh": "皮卡丘",
                "category": "鼠 宝可梦", "gender_ratio": "♂50% ♀50%",
                "height": "0.4 m", "weight": "6.0 kg",
                "abilities": "静 电", "hidden_abilities": "避 雷针",
                "catch_rate": "190 ", "egg_groups": "陆上 妖精",
                "profile": "简介首段。\n简介次段。",
                "evolution": "前身 →\n测试成体。",
                "stats": "HP35/攻击55/防御40/特攻50/特防50/速度90",
            })

    def render(self, item, style):
        return render_draw_card([item], style=style, nickname="文字验证",
            remaining=2, daily_limit=3, dust=0, pity_ssr=0, ssr_pity_after=30)

    def test_imported_line_breaks_render_like_the_same_clean_fields_in_all_styles(self):
        clean = self.item()
        imported = replace(clean, name="皮卡丘\r\n测试\t名", row={**clean.row,
            "category": "鼠\r\n宝可梦", "gender_ratio": "♂50%\r\n♀50%",
            "height": "0.4\tm", "weight": "6.0\r\nkg",
            "abilities": "静\n电", "hidden_abilities": "避\t雷针",
            "catch_rate": "190\t", "egg_groups": "陆上\r\n妖精",
            "profile": "简介首段。\r\n简介次段。",
            "evolution": "前身 →\r\n测试成体。",
        })
        before = deepcopy(imported)
        for style in ("classic", "tcg", "yugioh"):
            with self.subTest(style=style):
                output = self.render(imported, style)
                self.assertEqual(output, self.render(clean, style),
                    "Imported control characters must not change the printed layout")
                with Image.open(io.BytesIO(output)) as image:
                    self.assertEqual(image.format, "JPEG")
                    image.verify()
                self.assertEqual(imported, before, "Rendering changed the original species data")

    def test_paragraph_line_breaks_survive_windows_and_mac_newlines(self):
        draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        for value in ("首段。\n次段。\n末段。", "首段。\r\n次段。\r末段。"):
            calls = (
                ("classic", lambda: rarity_cards._wrap(draw, value, 28, 900)),
                ("tcg", lambda: tcg_cards._wrap(draw, value, 28, 900)),
                ("yugioh", lambda: yugioh_cards._wrap(draw, value, 28, 900)[0]),
                ("collection", lambda: partner_cards._wrap(draw, value, partner_cards._font(28), 900)),
            )
            for style, call in calls:
                with self.subTest(style=style, value=repr(value)):
                    self.assertEqual(tuple(call()), ("首段。", "次段。", "末段。"))
        value = "首段。\r\n次段。\r末段。"
        for style, call in (
            ("tcg", lambda: tcg_cards._take_lines(draw, value, 28, 900, 2)),
            ("yugioh", lambda: yugioh_cards._wrap(draw, value, 28, 900, 2)),
        ):
            with self.subTest(style=style, truncated=True):
                self.assertEqual(call(), (("首段。", "次段。"), "末段。"))


if __name__ == "__main__":
    unittest.main()
