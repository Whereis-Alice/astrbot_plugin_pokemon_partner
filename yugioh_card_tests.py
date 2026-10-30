"""Public-output regressions for the Yu-Gi-Oh-inspired Pokémon collection cards.

Exercises the actual card renderer without downloads or production collection
state. Glyph contrast is sampled before painting, and distinctive portrait
extremities detect cropping/overlays in the final JPEG.
"""

from __future__ import annotations

import io
import math
import os
import re
import tempfile
import unittest
from colorsys import rgb_to_hsv
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
    TextRecord,
    assert_portrait,
    capture_render,
    coalesce_shadows,
    contrast_ratio,
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


def title_mask(name, face):
    artwork = yugioh_cards.load_card_name_mask(name, face.size)
    if artwork is not None:
        mask = Image.new('L', (artwork.width + 12, artwork.height + 12))
        mask.paste(artwork, (6, 6))
        return mask
    # Sample the actual supersampled glyph footprint, including its sidebearing.
    # Cropping to textbbox and drawing at zero would sample background pixels
    # for the inscription face, whose first ink pixel follows its left bearing.
    size = (math.ceil(face.getlength(name)) + 12, face.getbbox(name, anchor='lt')[3] + 12)
    mask = Image.new('L', (size[0]*3, size[1]*3))
    ImageDraw.Draw(mask).text((18, 18), name, font=face.font_variant(size=face.size*3),
                             anchor='lt', fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def capture_card(items, loader):
    """Include foil ink from final card pixels, not its temporary white mask."""
    foil_records = []
    original_foil = yugioh_cards._paint_name
    original_text = ImageDraw.ImageDraw.text

    def foil(base, name, x, y, size, kind, **kwargs):
        before = base.copy()
        # Mask coordinates and white paint are not the final title's position
        # or colour. All ordinary card text keeps the shared instrumentation.
        with patch.object(ImageDraw.ImageDraw, "text", original_text):
            original_foil(base, name, x, y, size, kind, **kwargs)
            mask = Image.new("L", base.size)
            local = title_mask(name, kwargs.get('font') or yugioh_cards._name_font(size, name))
            mask.paste(local, (round(x)-6, round(y)-6))
        box = mask.getbbox()
        if box is None:
            raise AssertionError(f"Title contains no visible glyphs: {name}")
        points = [(xx, yy) for yy in range(box[1], box[3], 2)
                  for xx in range(box[0], box[2], 2) if mask.getpixel((xx, yy)) >= 240]
        if not points:
            raise AssertionError(f"Title contains no opaque glyphs: {name}")
        contrast = min(contrast_ratio(base.getpixel(point), before.getpixel(point))
                       for point in points)
        foil_records.append(TextRecord(name, box, size, contrast))

    with patch.object(yugioh_cards, "_paint_name", foil):
        image, records = capture_render(items, loader, style="yugioh")
    return image, records + foil_records


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
        original_name = yugioh_cards._paint_name

        def capture(draw, xy, text, *args, **kwargs):
            values.append(str(text))
            return original(draw, xy, text, *args, **kwargs)

        def name(base, value, *args, **kwargs):
            values.append(value)
            return original_name(base, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", capture), \
             patch.object(yugioh_cards, "_paint_name", name):
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

    def test_rarity_stars_are_two_four_six_eight_twelve_and_do_not_cover_text(self):
        expected = (2, 4, 6, 8, 12)
        original_star = yugioh_cards._rarity_star
        original_composite = Image.Image.alpha_composite
        original_frame = yugioh_cards.paint_frame
        for (kind, shiny), count in zip(EDITIONS, expected):
            with self.subTest(rarity=kind, shiny=shiny):
                boxes, drawing_star, frames = [], False, {}

                def frame(base, name, box, kind):
                    frames[name] = box
                    return original_frame(base, name, box, kind)

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
                     patch.object(yugioh_cards, "paint_frame", frame), \
                     patch.object(Image.Image, "alpha_composite", composite):
                    image, records = capture_card([self.item("151", kind, shiny)],
                                                  lambda row: portrait())
                self.assertEqual(len(boxes), count)
                content = [r for r in coalesce_shadows(records) if meaningful(r) and r.value.strip()]
                rail_top = min(box[1] for box in boxes)
                rail_bottom = max(box[3] for box in boxes)
                self.assertGreaterEqual(rail_bottom-rail_top, 60,
                                        'The larger level orbs should remain readable at preview size')
                top_gap = rail_top-frames['title'][3]
                bottom_gap = frames['picture'][1]-rail_bottom
                self.assertGreaterEqual(min(top_gap, bottom_gap), 12)
                self.assertLessEqual(abs(top_gap-bottom_gap), 6,
                                     'Balance the visible space above and below the level orbs')
                self.assertFalse([record.value for record in content
                                  if record.box[1] < rail_bottom and record.box[3] > rail_top],
                                 "The level rail must contain stars without a written rarity label")
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

    def test_ssr_title_is_gold_and_shiny_title_contains_multiple_foil_colours(self):
        for shiny in (False, True):
            with self.subTest(shiny=shiny):
                item = self.item("857", "SSR", shiny)
                image, records = capture_card([item], lambda row: portrait())
                title = next(record for record in records if record.value == item.name)
                face = yugioh_cards._name_font(title.size, item.name)
                mask = title_mask(item.name, face)
                mask = mask.crop(mask.getbbox())
                colours = [image.getpixel((x + title.box[0], y + title.box[1]))
                           for y in range(0, mask.height, 2) for x in range(0, mask.width, 2)
                           if mask.getpixel((x, y)) >= 248]
                self.assertGreater(len(colours), 200, "The entire title must remain printed")
                if not shiny:
                    # Champagne-gold foil includes pale highlights. Warm ink
                    # and a reflected light range distinguish it from white,
                    # while the old saturated-yellow ratio rejected real foil.
                    gold = sum(r >= g and g-b >= 12 and r-b >= 25 for r, g, b in colours)
                    self.assertGreater(gold / len(colours), .65,
                                       "SSR title should visibly use warm gold ink, not plain white")
                    light = sorted(sum(colour)/3 for colour in colours)
                    self.assertGreater(light[len(light)*9//10]-light[len(light)//10], 20,
                                       "Gold foil should retain a visible reflection and shaded crease")
                else:
                    hues = {int(h * 12) for r, g, b in colours
                            for h, s, v in (rgb_to_hsv(r / 255, g / 255, b / 255),)
                            if s > .3 and v > .3}
                    self.assertGreaterEqual(len(hues), 4,
                                            "Shiny title needs a multicolour foil spectrum")

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
                render_icon = yugioh_cards.render_attribute_icon

                def icon(name, size):
                    icons.append(name)
                    return render_icon(name, size)

                with patch.object(yugioh_cards, "render_attribute_icon", icon):
                    _raw, values = self.texts(item, lambda row: portrait())
                self.assertEqual(values.count("未知"), 2,
                                 "Unknown type data must keep all matchup groups explicitly unknown")
                self.assertIn("免疫未知", "".join(values))
                self.assertEqual(icons, ["未知"],
                                 "Own attributes belong only to the top-right header")
                for label in ("弱点", "抗性"):
                    self.assertIn(label, values)

    def test_valid_full_width_type_separators_preserve_the_same_card_and_matchups(self):
        base = self.item("1", "C")
        canonical = self.render([base])
        for supplied in ("草／毒", "草，毒", " 草 ／ 毒 "):
            with self.subTest(types=supplied):
                item = replace(base, types=supplied, row={**base.row, "types": supplied})
                self.assertEqual(self.render([item]), canonical)

    def test_all_portrait_extremities_remain_visible_and_clear_of_text(self):
        image, records = capture_card([self.item("1", "C")], lambda row: portrait())
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
        for label in ("HP", "攻/", "守/", "特攻", "特防", "速度", "总和"):
            self.assertIn(label, visible)
        for number in ("45", "49", "65", "318"):
            self.assertRegex(visible, rf"(?<!\d){number}(?!\d)")

    def test_descriptor_uses_species_gender_and_immunity_without_repeating_own_types(self):
        for key in ("1", "82", "871"):
            with self.subTest(species=key):
                item = self.item(key, "SSR")
                records, active = [], False
                render_descriptor = yugioh_cards._descriptor
                render_icon = yugioh_cards.render_attribute_icon
                render_text = yugioh_cards._text

                def descriptor(*args, **kwargs):
                    nonlocal active
                    active = True
                    try:
                        return render_descriptor(*args, **kwargs)
                    finally:
                        active = False

                def icon(name, size):
                    if active:
                        records.append(("icon", name))
                    return render_icon(name, size)

                def text(draw, x, y, value, *args, **kwargs):
                    if active:
                        records.append(("text", str(value)))
                    return render_text(draw, x, y, value, *args, **kwargs)

                with patch.object(yugioh_cards, "_descriptor", descriptor), \
                     patch.object(yugioh_cards, "render_attribute_icon", icon), \
                     patch.object(yugioh_cards, "_text", text):
                    self.render([item], lambda row: portrait())
                self.assertEqual([value for kind, value in records if kind == "icon"],
                                 [name for name, _ in matchup_groups(item.row,item.types)["immune"]])
                wording = [value for kind, value in records if kind == "text"]
                self.assertTrue(wording[0].startswith("【"))
                self.assertIn(item.row["category"], wording[0])
                self.assertEqual(wording[-1], "】")
                expected_gender = "无性别" if item.row["gender_ratio"] == "♂0% ♀0%" \
                    else item.row["gender_ratio"]
                self.assertIn(expected_gender, wording[0])
                self.assertTrue(all(value not in item.types.split("/") for value in wording),
                                "The descriptor must not repeat an icon's type name as separate text")

    def test_reference_data_rail_combat_line_and_complete_footer_keep_their_hierarchy(self):
        for key in ("82", "871", "110:伽勒尔双弹瓦斯"):
            with self.subTest(species=key):
                item = self.item(key, "SSR")
                image, records = capture_card([item], lambda row: portrait())
                text = "".join(record.value for record in records)
                for field in ("abilities", "hidden_abilities", "height", "weight",
                              "catch_rate", "egg_groups"):
                    if item.row.get(field):
                        self.assertIn(item.row[field], text)
                self.assertEqual(text.count(f"#{item.dex_raw}"), 1)
                ability = next(record for record in records if record.value.startswith("特性 "))
                metadata = next(record for record in records
                                if record.value == f"{item.row['height']} · {item.row['weight']}")
                self.assertLessEqual(abs((ability.box[1]+ability.box[3])/2-
                                         (metadata.box[1]+metadata.box[3])/2), 1,
                                     'CJK ability text and Latin measurements need the same visual centre')
                descriptor = next(record for record in records
                                  if record.value.startswith("【") and item.row["category"] in record.value)
                combat = next(record for record in records if record.value.startswith("攻/"))
                footer = next(record for record in records if record.value.startswith("捕获度 "))
                serial = next(record for record in records if record.value == f"#{item.dex_raw}")
                self.assertLess(ability.box[3], descriptor.box[1])
                self.assertIn("守/", combat.value)
                self.assertGreater(combat.box[0], image.width / 2,
                                   "ATK and DEF belong together at the bottom right")
                self.assertLess(combat.box[3], footer.box[1])
                for text in (footer, serial):
                    top_gap = text.box[1] - yugioh_cards.READING_BOX[3]
                    bottom_gap = yugioh_cards.FOOTER_BOTTOM - text.box[3]
                    self.assertLessEqual(abs(top_gap - bottom_gap), 1,
                                         'Centre actual footer ink in the exposed paper')

    def test_matchups_pair_every_actual_type_jewel_with_its_multiplier_only(self):
        for key in ("6", "82", "302"):
            with self.subTest(species=key):
                item = self.item(key, "SSR")
                events, in_matchups = [], False
                render_icon = yugioh_cards.render_attribute_icon
                render_text = yugioh_cards._text
                render_matchups = yugioh_cards._matchups

                def icon(name, size):
                    if in_matchups:
                        events.append(("icon", name))
                    return render_icon(name, size)

                def text(draw, x, y, value, *args, **kwargs):
                    if in_matchups:
                        events.append(("text", str(value)))
                    return render_text(draw, x, y, value, *args, **kwargs)

                def matchups(*args, **kwargs):
                    nonlocal in_matchups
                    in_matchups = True
                    try:
                        return render_matchups(*args, **kwargs)
                    finally:
                        in_matchups = False

                with patch.object(yugioh_cards, "render_attribute_icon", icon), \
                     patch.object(yugioh_cards, "_text", text), \
                     patch.object(yugioh_cards, "_matchups", matchups):
                    _raw, values = self.texts(item)
                visible = "".join(values)
                self.assertIn("弱点", visible)
                self.assertIn("抗性", visible)
                expected = []
                for name, label in (("resist", "抗性"), ("weak", "弱点")):
                    entries = matchup_groups(item.row, item.types)[name]
                    expected.append(("text", label))
                    if not entries:
                        expected.append(("text", "无"))
                    for name, multiplier in entries:
                        expected.extend((("icon", name), ("text", f"×{multiplier:g}")))
                expected.append(("text", f"总和 {item.bst}"))
                self.assertEqual(events, expected)

    def test_effect_box_order_rule_alignment_and_long_descriptor_stay_inside_the_card(self):
        for key, long_category in (("82", False), ("110:伽勒尔双弹瓦斯", False), ("302", True)):
            with self.subTest(species=key, long_category=long_category):
                item = self.item(key, "SSR")
                item = replace(item, row={**item.row,
                    "profile": "资料段落验证。",
                    "evolution": "前身 → 成体。",
                    **({"category": "特别长的分类名称" * 40} if long_category else {})})
                rules, icon_boxes, matchup_icons = [], [], []
                in_descriptor, in_matchups = False, False
                original_line = ImageDraw.ImageDraw.line
                original_composite = Image.Image.alpha_composite
                original_descriptor = yugioh_cards._descriptor
                original_matchups = yugioh_cards._matchups

                def line(draw, xy, *args, **kwargs):
                    if draw._image.size == (CARD_W, CARD_H) and len(xy) == 4 \
                            and all(isinstance(value, (int, float)) for value in xy) \
                            and xy[1] == xy[3] and xy[0] <= 93 and xy[2] >= 988:
                        rules.append(tuple(xy))
                    return original_line(draw, xy, *args, **kwargs)

                def descriptor(*args, **kwargs):
                    nonlocal in_descriptor
                    in_descriptor = True
                    try:
                        return original_descriptor(*args, **kwargs)
                    finally:
                        in_descriptor = False

                def composite(target, source_image, dest=(0, 0), **kwargs):
                    if (in_descriptor or in_matchups) and source_image.width == source_image.height \
                            and source_image.width <= 80:
                        bounds = source_image.getchannel("A").getbbox()
                        if bounds:
                            boxes = icon_boxes if in_descriptor else matchup_icons
                            boxes.append(tuple(value + dest[axis % 2]
                                               for axis, value in enumerate(bounds)))
                    return original_composite(target, source_image, dest=dest, **kwargs)

                def matchups(*args, **kwargs):
                    nonlocal in_matchups
                    in_matchups = True
                    try:
                        return original_matchups(*args, **kwargs)
                    finally:
                        in_matchups = False

                with patch.object(ImageDraw.ImageDraw, "line", line), \
                     patch.object(Image.Image, "alpha_composite", composite), \
                     patch.object(yugioh_cards, "_descriptor", descriptor), \
                     patch.object(yugioh_cards, "_matchups", matchups):
                    image, records = capture_card([item], lambda row: portrait())
                body = [record for record in records if record.value.strip()]
                profile = next(record for record in body if record.value == "资料段落验证。")
                evolution = next(record for record in body if record.value.startswith("进化："))
                resist = next(record for record in body if record.value == "抗性")
                weak = next(record for record in body if record.value == "弱点")
                for earlier, later in zip((profile, evolution, resist), (evolution, resist, weak)):
                    self.assertLess(earlier.box[3], later.box[1], (earlier.value, later.value))

                total = next(record for record in body if record.value == f"总和 {item.bst}")
                combat = next(record for record in body if record.value.startswith("攻/"))
                rule = next(rule for rule in rules if total.box[3] < rule[1] < combat.box[1])
                self.assertLessEqual(abs(total.box[2] - rule[2]), 3,
                                     "The species total should align with the rule's right end")
                self.assertLessEqual(rule[1] - total.box[3], 16,
                                     "The total belongs immediately above the bottom rule")
                combat_center = (combat.box[1] + combat.box[3]) / 2
                self.assertGreaterEqual(combat.box[1] - rule[1], 6)
                self.assertGreaterEqual(yugioh_cards.READING_PAPER[3] - combat.box[3], 6)
                self.assertLessEqual(abs(combat.box[1] - (rule[1]+2) -
                                         (yugioh_cards.READING_PAPER[3]-combat.box[3])), 1,
                                     'Keep equal clearance above and below the combat ink')
                for label in ("HP ", "特攻 ", "特防 ", "速度 "):
                    stat = next(record for record in body if record.value.startswith(label))
                    self.assertLessEqual(abs((stat.box[1] + stat.box[3]) / 2 - combat_center), 1,
                                         "Small stats and ATK/DEF should share a visual centre")
                    self.assertLess(stat.box[2], combat.box[0])

                prefix = next(record for record in body if record.value.startswith("【"))
                close = next(record for record in body if record.value == "】")
                separator = max(rule[1] for rule in rules if rule[1] < resist.box[1])
                matchup_boxes = [record.box for record in body
                                 if separator < record.box[1] < rule[1]
                                 and not record.value.startswith('总和 ')] + matchup_icons
                top_gap = min(box[1] for box in matchup_boxes) - (separator + 1)
                bottom_gap = rule[1] - max(box[3] for box in matchup_boxes)
                self.assertLessEqual(abs(top_gap-bottom_gap), 2,
                                     'Centre matchup rows without counting the independently placed total')
                descriptor_bottom = max(prefix.box[3], close.box[3],
                                        max((box[3] for box in icon_boxes), default=0))
                self.assertLessEqual(abs(profile.box[1]-descriptor_bottom -
                                         (separator-evolution.box[3])), 2,
                                     'Centre the actual paragraph ink, excluding its trailing line spacing')
                self.assertLessEqual(close.box[2], image.width - 76)
                if long_category:
                    self.assertIn("…", prefix.value)
                    self.assertIn(item.row['gender_ratio'], prefix.value)
                    self.assertIn('免疫', prefix.value)
                previous_right = prefix.box[2]
                for left, top, right, bottom in icon_boxes:
                    self.assertGreaterEqual(left, previous_right - 1)
                    self.assertLessEqual(right, close.box[0] + 1)
                    self.assertGreaterEqual(top, prefix.box[1] - 5)
                    self.assertLessEqual(bottom, prefix.box[3] + 5)
                    previous_right = right

    def test_long_form_title_and_practical_text_have_safe_readable_glyphs(self):
        values = [self.item("857", kind, shiny) for kind, shiny in EDITIONS]
        values += [self.item("555:伽勒尔达摩狒狒（达摩模式）", kind, shiny)
                   for kind, shiny in EDITIONS]
        values.append(self.item("82", "SSR"))
        for item in values:
            with self.subTest(key=item.key, rarity=item.rarity, shiny=item.shiny):
                image, records = capture_card([item], lambda row: portrait())
                records = [r for r in coalesce_shadows(records) if meaningful(r) and r.value.strip()]
                self.assertIn(item.name, "".join(r.value for r in records))
                for record in records:
                    left, top, right, bottom = record.box
                    self.assertTrue(18 <= left < right <= image.width - 18, record)
                    self.assertTrue(18 <= top < bottom <= image.height - 12, record)
                    self.assertGreaterEqual(record.size, 24, record)
                    if record.box[1] < 160 and (item.shiny or item.rarity == "SSR"):
                        # Specular foil deliberately includes highlights close
                        # to the paper's luminance. Its final glyph geometry,
                        # hue and full-/phone-size images are checked separately.
                        continue
                    required = 3 if record.size >= 48 else 4.5
                    self.assertGreaterEqual(record.contrast, required - .03, record)
                for index, a in enumerate(records):
                    for b in records[index + 1:]:
                        overlap = max(a.box[0], b.box[0]) < min(a.box[2], b.box[2]) and \
                                  max(a.box[1], b.box[1]) < min(a.box[3], b.box[3])
                        self.assertFalse(overlap, (a.value, b.value))

    def test_long_data_stays_on_the_card_without_appending_chat_details(self):
        item = self.item("857", "C")
        item = replace(item, row={**item.row,
            "abilities": "很长的特性名称、" * 50 + "特性末尾标记",
            "evolution": "非常长的进化链 → " * 20 + "进化末尾标记",
            "profile": "图鉴说明。" * 1200 + "摘要末尾标记"})
        details = []
        _raw, values = self.texts(item, details_out=details)
        self.assertEqual(details, [])
        self.assertIn("…", "".join(values))
        self.assertIn(item.name, "".join(values))

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
            "profile": "简短摘要。",
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
