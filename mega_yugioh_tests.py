"""Public-output checks for Mega portraits, titles and the twelve-star rail."""
from __future__ import annotations

import io
import os
import tempfile
import unicodedata
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="partner-mega-yugioh-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

from PIL import Image, ImageChops, ImageDraw

from gacha import DrawItem, GachaRules, build_partners
from partner_cards import render_draw_card
from pokedex_db import PokedexDB
from rarity_visual_tests import portrait
import yugioh_cards


CONTEXT = dict(nickname="测试训练家", remaining=2, daily_limit=3, dust=128,
               pity_ssr=4, ssr_pity_after=30, style="yugioh")


def overlaps(first, second):
    return (max(first[0], second[0]) < min(first[2], second[2]) and
            max(first[1], second[1]) < min(first[3], second[3]))


class MegaYugiohTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pool = build_partners(
            PokedexDB(), GachaRules.from_config({"include_alternate_forms": True}))

    def item(self, dex=6, form="超级喷火龙Y", *, shiny=False):
        expected = unicodedata.normalize("NFKC", form)
        partner = next(p for p in self.pool if p.dex == dex and
            unicodedata.normalize("NFKC", str(p.row.get("form_name") or "")) == expected)
        return DrawItem(partner.key, partner.dex, partner.dex_raw, partner.name,
                        partner.types, partner.bst, partner.rarity,
                        shiny, True, 0, 1, partner.row)

    def render(self, items, loader=None, **options):
        before = deepcopy(items)
        raw = render_draw_card(items, image_loader=loader, **dict(CONTEXT, **options))
        self.assertEqual(items, before, "Mega presentation must not alter collection identity")
        with Image.open(io.BytesIO(raw)) as image:
            self.assertEqual(image.format, "JPEG")
            if len(items) == 1:
                self.assertEqual(image.size, (1080, 1680))
            self.assertLessEqual(image.width, 1200)
            self.assertLessEqual(image.height, 5000)
            image.verify()
        self.assertLess(len(raw), 8_000_000)
        return raw

    def header(self, item):
        names, boxes, sources, retained_sources = [], {}, {}, []
        original_name = yugioh_cards._paint_name
        original_composite = Image.Image.alpha_composite

        def symbol(label, original):
            def render(*args, **kwargs):
                image = original(*args, **kwargs)
                if image is not None:
                    sources[id(image)] = label
                    retained_sources.append(image)
                return image
            return render

        def name(base, value, x, y, size, kind, **kwargs):
            face = kwargs.get("font") or yugioh_cards._name_font(size, value)
            metrics = yugioh_cards.card_name_artwork_metrics(value, size)
            width, height = metrics or (ImageDraw.Draw(base).textlength(value, font=face),
                                       face.getbbox(value, anchor="lt")[3])
            names.append(value)
            boxes.setdefault("name", []).append((x, y, x+width, y+height))
            return original_name(base, value, x, y, size, kind, **kwargs)

        def composite(target, source_image, dest=(0, 0), **kwargs):
            label = sources.get(id(source_image))
            if label and target.size == (1080, 1680) and dest[1] < 253:
                bounds = source_image.getchannel("A").getbbox()
                if bounds:
                    box = tuple(value+dest[axis % 2]
                                for axis, value in enumerate(bounds))
                    boxes.setdefault(label, []).append(box)
            return original_composite(target, source_image, dest=dest, **kwargs)

        with patch.object(yugioh_cards, "_paint_name", name), \
             patch.object(yugioh_cards, "render_mega_emblem", symbol("emblem", yugioh_cards.render_mega_emblem)), \
             patch.object(yugioh_cards, "render_mega_stone", symbol("stone", yugioh_cards.render_mega_stone)), \
             patch.object(yugioh_cards, "render_attribute_icon", symbol("attribute", yugioh_cards.render_attribute_icon)), \
             patch.object(yugioh_cards, "render_level_star", symbol("star", yugioh_cards.render_level_star)), \
             patch.object(Image.Image, "alpha_composite", composite):
            self.render([item], lambda row: portrait())
        return names, boxes

    def test_mega_title_has_only_the_form_name_and_emblem_fits(self):
        for dex, form in ((6, "超级喷火龙X"), (6, "超级喷火龙Y"),
                          (150, "超级超梦X"), (150, "超级超梦Y"),
                          (384, "超级烈空坐")):
            with self.subTest(form=form):
                item = self.item(dex, form)
                names, boxes = self.header(item)
                self.assertEqual(names, [item.row["form_name"]])
                self.assertEqual(len(boxes.get("emblem", [])), 1)
                self.assertGreaterEqual(len(boxes.get("attribute", [])), 1)
                emblem = boxes["emblem"][0]
                self.assertGreaterEqual(emblem[0], boxes["name"][0][2])
                self.assertTrue(66 <= emblem[1] < emblem[3] <= 147)
                for attribute in boxes["attribute"]:
                    self.assertFalse(overlaps(emblem, attribute), form)
                    self.assertFalse(overlaps(boxes["name"][0], attribute), form)

    def test_shiny_mega_stone_does_not_cover_any_of_twelve_stars(self):
        for dex, form in ((6, "超级喷火龙X"), (6, "超级喷火龙Y"),
                          (150, "超级超梦X"), (150, "超级超梦Y")):
            with self.subTest(form=form):
                item = self.item(dex, form, shiny=True)
                _names, boxes = self.header(item)
                self.assertEqual(len(boxes.get("star", [])), 12)
                self.assertEqual(len(boxes.get("stone", [])), 1)
                stone = boxes["stone"][0]
                self.assertTrue(159 <= stone[1] < stone[3] <= 253)
                for star in boxes["star"]:
                    self.assertFalse(overlaps(stone, star), form)
                    self.assertFalse(overlaps(boxes["emblem"][0], star), form)

    def test_rayquaza_has_its_mega_emblem_without_an_invented_stone(self):
        item = self.item(384, "超级烈空坐", shiny=True)
        _names, boxes = self.header(item)
        self.assertEqual(len(boxes.get("emblem", [])), 1)
        self.assertFalse(boxes.get("stone"))
        self.assertEqual(len(boxes.get("star", [])), 12)

    def test_non_mega_forms_keep_their_original_presentation(self):
        for dex, form in ((6, ""), (26, "阿罗拉雷丘"), (382, "原始盖欧卡")):
            with self.subTest(form=form):
                item = self.item(dex, form)
                with patch.object(yugioh_cards, "paint_mega_outer", wraps=yugioh_cards.paint_mega_outer) as outer, \
                     patch.object(yugioh_cards, "paint_mega_dark_stock", wraps=yugioh_cards.paint_mega_dark_stock) as stock, \
                     patch.object(yugioh_cards, "finish_mega_outer", wraps=yugioh_cards.finish_mega_outer) as finish:
                    names, boxes = self.header(item)
                self.assertEqual(names, [item.name])
                self.assertFalse(boxes.get("emblem"))
                self.assertFalse(boxes.get("stone"))
                outer.assert_not_called()
                stock.assert_not_called()
                finish.assert_not_called()

    def test_gigantamax_has_no_mega_stone_even_when_not_in_draw_pool(self):
        # Gigantamax rows do not have independent stats and are intentionally
        # excluded from the draw pool. Keep the actual row and use its base
        # species' collection metadata to exercise the public renderer.
        row = next(row for row in PokedexDB().table("pokemon")
                   if row.get("form_name") == "超极巨化喷火龙")
        base = self.item(6, "")
        form = str(row["form_name"])
        item = replace(base, row=row, key=f"{base.dex}:{form}",
                       name=f"{row['name_zh']}（{form}）",
                       types=str(row.get("types") or base.types))
        with patch.object(yugioh_cards, "paint_mega_outer", wraps=yugioh_cards.paint_mega_outer) as outer, \
             patch.object(yugioh_cards, "paint_mega_dark_stock", wraps=yugioh_cards.paint_mega_dark_stock) as stock, \
             patch.object(yugioh_cards, "finish_mega_outer", wraps=yugioh_cards.finish_mega_outer) as finish:
            names, boxes = self.header(item)
        self.assertEqual(names, [item.name])
        self.assertFalse(boxes.get("emblem"))
        self.assertFalse(boxes.get("stone"))
        outer.assert_not_called()
        stock.assert_not_called()
        finish.assert_not_called()

    def test_shiny_loader_preserves_exact_form_identity_and_source_row(self):
        item = self.item(shiny=True)
        requested = []

        def loader(row):
            requested.append(deepcopy(row))
            row["name_zh"] = "加载器局部改名"
            return portrait()

        self.render([item], loader)
        self.assertEqual(len(requested), 1)
        self.assertEqual(requested[0]["id"], item.row["id"])
        self.assertEqual(requested[0]["form_name"], item.row["form_name"])
        self.assertTrue(requested[0].get("_artwork_shiny"))
        self.assertNotIn("_artwork_shiny", item.row)

    def test_missing_mega_artwork_has_a_readable_fallback(self):
        texts = []
        original = ImageDraw.ImageDraw.text

        def record(draw, xy, value, *args, **kwargs):
            texts.append(str(value))
            return original(draw, xy, value, *args, **kwargs)

        with patch.object(ImageDraw.ImageDraw, "text", record):
            self.render([self.item()], lambda row: None)
        self.assertIn("立绘暂未缓存", texts)

    def test_mega_profile_black_bold_option_does_not_leak(self):
        item = self.item(shiny=True)
        normal = self.render([item], lambda row: portrait())
        bold = self.render([item], lambda row: portrait(), yugioh_profile_black_bold=True)
        self.assertNotEqual(normal, bold)
        self.assertEqual(normal, self.render([item], lambda row: portrait()))
        with Image.open(io.BytesIO(normal)) as first, Image.open(io.BytesIO(bold)) as second:
            self.assertEqual(first.crop((0, 0, 1080, 1180)).tobytes(),
                             second.crop((0, 0, 1080, 1180)).tobytes())

    def test_ordinary_mega_and_shiny_mega_mix_in_one_draw(self):
        items = [self.item(6, ""), self.item(), self.item(shiny=True)]
        self.render(items, lambda row: portrait())

    def test_outer_finish_replaces_black_and_retains_grey_reflection(self):
        # Exercise retained production stock, rather than fabricating a flat
        # test colour: the user's requested region contains the original scan.
        base = yugioh_cards.render_stock("SSR", 1080, 1680)
        yugioh_cards.paint_mega_dark_stock(base, "SSR")
        yugioh_cards.paint_mega_outer(base, "SSR")
        original = base.copy()
        yugioh_cards.finish_mega_outer(base)
        self.assertEqual(original.crop((32, 32, 1048, 1648)).tobytes(),
                         base.crop((32, 32, 1048, 1648)).tobytes())
        changed_black = retained_reflections = 0
        for box in ((0, 0, 1080, 32), (0, 1648, 1080, 1680),
                    (0, 32, 32, 1648), (1048, 32, 1080, 1648)):
            for old, new in zip(original.crop(box).getdata(), base.crop(box).getdata()):
                if max(old[:3]) >= 100:
                    retained_reflections += 1
                    self.assertEqual(old, new)
                if max(old[:3]) <= 20 and old[3] == 255:
                    changed_black += 1
                    self.assertNotEqual(old, new)
                    luminance = sum(c*w for c, w in zip(new[:3], (.299, .587, .114)))
                    self.assertGreaterEqual(luminance, 39.5,
                        "The former black ink must become visible material")
                    self.assertGreaterEqual(max(new[:3])-min(new[:3]), 12,
                        "The replacement must have visible colour, not grey ink")
        self.assertGreater(changed_black, 100)
        self.assertGreater(retained_reflections, 100)
        self.assertIsNotNone(ImageChops.difference(original, base).convert("RGB").getbbox())


if __name__ == "__main__":
    unittest.main()
