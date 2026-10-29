"""Public-output regressions for the Yu-Gi-Oh-inspired Pokémon collection cards.

Exercises the actual card renderer without downloads or production collection
state. Glyph contrast is sampled before painting, and distinctive portrait
extremities detect cropping/overlays in the final JPEG.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="partner-yugioh-cards-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

from PIL import Image, ImageDraw

from card_matchups import matchup_groups
from gacha import DrawItem, GachaRules, build_partners
from partner_cards import render_draw_card
from pokedex_db import PokedexDB
from rarity_visual_tests import (
    assert_portrait,
    capture_render,
    coalesce_shadows,
    matching_points,
    meaningful,
    portrait,
)
import yugioh_cards
from yugioh_cards import CARD_H, CARD_W


CONTEXT = dict(nickname="测试训练家", remaining=2, daily_limit=3, dust=128,
               pity_ssr=4, ssr_pity_after=30, style="yugioh")
EDITIONS = (("C", False), ("R", False), ("SR", False),
            ("SSR", False), ("SSR", True))


class YugiohCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = {
            p.key: p for p in build_partners(
                PokedexDB(), GachaRules.from_config({"include_alternate_forms": True})
            )
        }

    def item(self, key="25", rarity="R", shiny=False):
        p = self.pool[key]
        return DrawItem(p.key, p.dex, p.dex_raw, p.name, p.types, p.bst,
                        rarity, shiny, True, 0, 1, p.row)

    def render(self, items, loader=None, *, details_out=None, **options):
        before = deepcopy(items)
        raw = render_draw_card(items, image_loader=loader, details_out=details_out,
                               **dict(CONTEXT, **options))
        self.assertEqual(items, before, "Rendering must not modify draw or species data")
        with Image.open(io.BytesIO(raw)) as image:
            if len(items) == 1:
                self.assertEqual(image.size, (CARD_W, CARD_H))
            self.assertLessEqual(image.width, 1200)
            self.assertLessEqual(image.height, 5000)
            self.assertEqual(image.format, "JPEG")
            image.verify()
        self.assertLess(len(raw), 8_000_000)
        return raw

    def texts(self, item, loader=None, *, details_out=None):
        values = []
        original = ImageDraw.ImageDraw.text

        def capture(draw, xy, text, *args, **kwargs):
            values.append(str(text))
            return original(draw, xy, text, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", capture):
            raw = self.render([item], loader, details_out=details_out)
        return raw, values

    def test_five_editions_have_distinct_card_surfaces(self):
        rendered = [self.render([self.item("1", kind, shiny)])
                    for kind, shiny in EDITIONS]
        self.assertEqual(len(set(rendered)), len(EDITIONS))
        # Compare stock away from portraits and names: changing only a badge
        # would not give the requested independent material treatments.
        stock = []
        for raw in rendered:
            with Image.open(io.BytesIO(raw)) as image:
                stock.append(image.crop((12, 260, 46, 890)).tobytes())
        self.assertEqual(len(set(stock)), len(EDITIONS))

    def test_rarity_stars_are_two_four_six_eight_ten_and_do_not_cover_text(self):
        expected = (2, 4, 6, 8, 10)
        original_star = yugioh_cards._rarity_star
        original_composite = Image.Image.alpha_composite
        for (kind, shiny), count in zip(EDITIONS, expected):
            with self.subTest(rarity=kind, shiny=shiny):
                boxes, drawing_star = [], False

                def star(*args, **kwargs):
                    nonlocal drawing_star
                    drawing_star = True
                    try:
                        return original_star(*args, **kwargs)
                    finally:
                        drawing_star = False

                def composite(target, source_image, dest=(0, 0), **kwargs):
                    if drawing_star:
                        bounds = source_image.getchannel("A").getbbox()
                        self.assertIsNotNone(bounds)
                        boxes.append(tuple(value + dest[axis % 2]
                                           for axis, value in enumerate(bounds)))
                    return original_composite(target, source_image, dest=dest, **kwargs)

                with patch.object(yugioh_cards, "_rarity_star", star), \
                     patch.object(Image.Image, "alpha_composite", composite):
                    image, records = capture_render([self.item("151", kind, shiny)],
                                                    lambda row: portrait(), style="yugioh")
                self.assertEqual(len(boxes), count)
                content = [r for r in coalesce_shadows(records) if meaningful(r) and r.value.strip()]
                for left, top, right, bottom in boxes:
                    self.assertTrue(18 <= left < right <= image.width - 18)
                    self.assertTrue(18 <= top < bottom <= image.height - 12)
                    for record in content:
                        overlap = max(left, record.box[0]) < min(right, record.box[2]) and \
                                  max(top, record.box[1]) < min(bottom, record.box[3])
                        self.assertFalse(overlap, ("Star covers card text", kind, record.value))

    def test_pure_card_ignores_transient_collection_state(self):
        item = self.item()
        first, values = self.texts(item)
        second = self.render([replace(item, is_new=False, dust=999)],
                             nickname="另一位训练家", remaining=0, daily_limit=50,
                             dust=99999, pity_ssr=29)
        self.assertEqual(first, second)
        visible = "".join(values)
        for removed in ("全国图鉴", "今日宝可梦", "今日剩余", "星尘", "NEW", "加入图鉴", "图鉴摘要（续）"):
            self.assertNotIn(removed, visible)

    def test_regional_and_shiny_artwork_requests_preserve_identity(self):
        item = self.item("53:阿罗拉猫老大", shiny=True)
        requests = []

        def load(row):
            requests.append(deepcopy(row))
            return portrait()

        self.render([item], load)
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["form_name"], item.row["form_name"])
        self.assertEqual(requests[0]["id"], item.row["id"])
        self.assertTrue(requests[0].get("_artwork_shiny"))
        self.assertNotIn("_artwork_shiny", item.row)

    def test_normal_artwork_loader_can_write_its_copy_without_mutating_species(self):
        item = self.item("1", "C")
        requests = []

        def load(row):
            requests.append(row)
            row["types"] = "火"
            row["name_zh"] = "加载器内部改名"
            row["loader_marker"] = True
            return portrait()

        self.render([item], load)
        self.assertEqual(len(requests), 1)
        self.assertIsNot(requests[0], item.row)
        self.assertNotIn("loader_marker", item.row)
        self.assertEqual(item.row["name_zh"], "妙蛙种子")

    def test_missing_or_invalid_types_are_explicitly_unknown_without_invented_matchups(self):
        base = self.item("1", "C")
        for supplied in ("", None, "不存在的属性", "火／水／草"):
            with self.subTest(types=supplied):
                item = replace(base, types=supplied, row={**base.row, "types": supplied})
                icons = []
                render_icon = yugioh_cards.render_energy_icon

                def icon(name, size):
                    icons.append(name)
                    return render_icon(name, size)

                with patch.object(yugioh_cards, "render_energy_icon", icon):
                    _raw, values = self.texts(item, lambda row: portrait())
                self.assertIn("属性未知", values)
                self.assertEqual(values.count("未知"), 3,
                                 "Unknown type data must keep all matchup groups explicitly unknown")
                self.assertFalse(icons, "Unknown type data must not fabricate a normal/type energy icon")
                for label in ("弱点", "抗性", "免疫"):
                    self.assertIn(label, values)

    def test_valid_full_width_type_separators_preserve_the_same_card_and_matchups(self):
        base = self.item("1", "C")
        canonical = self.render([base])
        for supplied in ("草／毒", "草，毒", " 草 ／ 毒 "):
            with self.subTest(types=supplied):
                item = replace(base, types=supplied, row={**base.row, "types": supplied})
                self.assertEqual(self.render([item]), canonical)

    def test_all_portrait_extremities_remain_visible_and_clear_of_text(self):
        image, records = capture_render([self.item("1", "C")], lambda row: portrait(),
                                        style="yugioh")
        assert_portrait(image, records)

    def test_very_wide_and_tall_artwork_is_contained_without_cropping(self):
        colors = ((249, 18, 37), (28, 242, 47), (31, 53, 247), (22, 233, 249))
        for size in ((700, 180), (180, 700)):
            with self.subTest(size=size):
                source = Image.new("RGBA", size)
                draw = ImageDraw.Draw(source)
                draw.rectangle((8, 8, size[0] - 9, size[1] - 9), fill=(219, 13, 186, 255))
                markers = ((size[0] // 2 - 15, 8, size[0] // 2 + 15, 40),
                           (size[0] - 40, size[1] // 2 - 15, size[0] - 9, size[1] // 2 + 15),
                           (size[0] // 2 - 15, size[1] - 40, size[0] // 2 + 15, size[1] - 9),
                           (8, size[1] // 2 - 15, 40, size[1] // 2 + 15))
                for box, color in zip(markers, colors):
                    draw.rectangle(box, fill=(*color, 255))
                before = source.tobytes()
                raw = self.render([self.item("1", "C")], lambda row: source)
                self.assertEqual(source.tobytes(), before)
                with Image.open(io.BytesIO(raw)) as image:
                    for color in colors:
                        self.assertGreater(len(matching_points(image, color)), 30,
                                           f"Portrait extremity clipped: {size} {color}")

    def test_transparent_art_uses_the_same_missing_art_placeholder(self):
        item = self.item()
        missing = self.render([item], lambda row: None)
        transparent = self.render([item], lambda row: Image.new("RGBA", (475, 475)))
        self.assertEqual(missing, transparent)

    def test_six_species_stats_and_total_are_visible(self):
        item = self.item("1", "C")
        _raw, values = self.texts(item)
        visible = "".join(values)
        for label in ("HP", "攻击", "防御", "特攻", "特防", "速度"):
            self.assertIn(label, visible)
        for number in ("45", "49", "65", "318"):
            self.assertRegex(visible, rf"(?<!\d){number}(?!\d)")

    def test_all_actual_weakness_resistance_and_immunity_tokens_are_visible(self):
        for key in ("6", "82", "302"):
            with self.subTest(species=key):
                item = self.item(key, "SSR")
                icons = []
                render_icon = yugioh_cards.render_energy_icon

                def icon(name, size):
                    icons.append(name)
                    return render_icon(name, size)

                with patch.object(yugioh_cards, "render_energy_icon", icon):
                    _raw, values = self.texts(item)
                visible = "".join(values)
                self.assertIn("弱点", visible)
                self.assertIn("抗性", visible)
                for entries in matchup_groups(item.row, item.types).values():
                    for name, multiplier in entries:
                        self.assertRegex(visible, re.escape(name) + r"\s*×\s*" +
                                         re.escape(f"{multiplier:g}") + r"(?![\d.])")
                        self.assertIn(name, icons, "An actual type icon must accompany each matchup")

    def test_long_form_title_and_practical_text_have_safe_readable_glyphs(self):
        values = [self.item("857", kind, shiny) for kind, shiny in EDITIONS]
        values += [self.item("555:伽勒尔达摩狒狒（达摩模式）", "SR"), self.item("82", "SSR")]
        for item in values:
            with self.subTest(key=item.key, rarity=item.rarity, shiny=item.shiny):
                image, records = capture_render([item], lambda row: portrait(), style="yugioh")
                records = [r for r in coalesce_shadows(records) if meaningful(r) and r.value.strip()]
                self.assertIn(item.name, "".join(r.value for r in records))
                for record in records:
                    left, top, right, bottom = record.box
                    self.assertTrue(18 <= left < right <= image.width - 18, record)
                    self.assertTrue(18 <= top < bottom <= image.height - 12, record)
                    self.assertGreaterEqual(record.size, 24, record)
                    required = 3 if record.size >= 48 else 4.5
                    self.assertGreaterEqual(record.contrast, required - .03, record)
                for index, a in enumerate(records):
                    for b in records[index + 1:]:
                        overlap = max(a.box[0], b.box[0]) < min(a.box[2], b.box[2]) and \
                                  max(a.box[1], b.box[1]) < min(a.box[3], b.box[3])
                        self.assertFalse(overlap, (a.value, b.value))

    def test_overflow_practical_data_is_returned_without_flavor_continuation(self):
        item = self.item("857", "C")
        item = replace(item, row={**item.row,
            "abilities": "很长的特性名称、" * 50 + "特性末尾标记",
            "evolution": "非常长的进化链 → " * 20 + "进化末尾标记",
            "profile": "图鉴说明。" * 1200 + "摘要末尾标记"})
        details = []
        self.render([item], details_out=details)
        returned = "\n".join(details)
        self.assertIn("特性末尾标记", returned)
        self.assertIn("进化末尾标记", returned)
        self.assertNotIn("摘要末尾标记", returned)
        self.assertIn(item.name, returned)

    def test_evolution_deduplicates_identical_branches_and_preserves_nested_conditions(self):
        ponyta = self.item("77", "R")
        before = deepcopy(ponyta.row)
        _raw, values = self.texts(ponyta)
        # The real dataset contains this exact branch twice. A presentation
        # cleanup must not rewrite the saved species row.
        branch = "小火马 →（等级40以上）烈焰马"
        self.assertEqual(ponyta.row["evolution"].count(branch), 2)
        self.assertEqual("".join(values).count(branch), 1)
        self.assertEqual(ponyta.row, before)

        nested = "伊布 →（夜晚（亲密度高）；白昼）月亮伊布"
        different = "伊布 →（白昼）太阳伊布"
        item = replace(ponyta, row={**ponyta.row,
            "evolution": f"{nested}；{nested}；{different}"})
        details = []
        _raw, values = self.texts(item, details_out=details)
        # If the card needs the overflow notice, inspect its complete evolution
        # rather than counting both the printed excerpt and complete notice.
        notices = [text for text in details if "进化：" in text]
        presented = "\n".join(notices) if notices else "".join(values)
        self.assertEqual(presented.count(nested), 1)
        self.assertEqual(presented.count(different), 1)
        self.assertIn("（夜晚（亲密度高）；白昼）", presented)
        self.assertEqual(item.row["evolution"].count(nested), 2)

    def test_ten_draws_keep_every_result_and_shiny_request_with_bounded_output(self):
        items = [self.item(key, kind, shiny) for key, kind, shiny in (
            ("1", "C", False), ("25", "R", False), ("448", "SR", False),
            ("384", "SSR", False), ("151", "SSR", True))] * 2
        requests = []

        def load(row):
            requests.append(deepcopy(row))
            return portrait()

        self.render(items, load)
        self.assertEqual(len(requests), len(items))
        self.assertEqual(sum(bool(r.get("_artwork_shiny")) for r in requests), 2)

    def test_empty_draws_are_rejected(self):
        with self.assertRaises(ValueError):
            self.render([])


if __name__ == "__main__":
    unittest.main(verbosity=2)
