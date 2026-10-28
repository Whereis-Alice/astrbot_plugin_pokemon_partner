"""Collection-card numbering and form labels without network or live bot data."""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from copy import deepcopy
from dataclasses import replace
from unittest.mock import patch

_runtime = tempfile.TemporaryDirectory(prefix="partner-dex-collection-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

from PIL import Image, ImageDraw
from gacha import GachaRules, OwnedPartner, build_partners
from partner_cards import PAD, W, render_collection_card
from pokedex_db import PokedexDB


class DexCollectionCardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        partners = build_partners(
            PokedexDB(), GachaRules.from_config({"include_alternate_forms": True})
        )
        cls.partners = {item.key: item for item in partners}

    def owned(self, key, *, shiny=0):
        return OwnedPartner(self.partners[key], 1, shiny, "", "")

    def render(self, items, *, page=1, collected=None):
        texts, bounds = [], []
        original = ImageDraw.ImageDraw.text

        def record(draw, xy, text, *args, **kwargs):
            texts.append(str(text))
            bounds.append(
                draw.textbbox(
                    xy, str(text), font=kwargs.get("font"), anchor=kwargs.get("anchor")
                )
            )
            return original(draw, xy, text, *args, **kwargs)

        before = deepcopy(items)
        with patch.object(ImageDraw.ImageDraw, "text", record):
            data = render_collection_card(
                items,
                nickname="小智",
                page=page,
                page_size=24,
                total=1025,
                collected=len(items) if collected is None else collected,
            )
        self.assertEqual(items, before)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.width, W)
            for left, top, right, bottom in bounds:
                self.assertGreaterEqual(left, PAD)
                self.assertGreaterEqual(top, PAD)
                self.assertLessEqual(right, W - PAD)
                self.assertLessEqual(bottom, image.height - PAD)
        return texts

    def test_sparse_collection_uses_national_numbers_instead_of_positions(self):
        texts = self.render([self.owned("25"), self.owned("151"), self.owned("1025")])
        badges = [text for text in texts if text.startswith("图鉴 #")]
        self.assertEqual(badges, ["图鉴 #0025", "图鉴 #0151", "图鉴 #1025"])
        self.assertFalse(any(text.startswith("序号 ") for text in texts))
        self.assertTrue(any("数字即图鉴编号" in text for text in texts))

    def test_page_changes_do_not_renumber_the_same_owned_species(self):
        item = self.owned("25", shiny=3)
        first = self.render([item], page=1, collected=200)
        later = self.render([item], page=8, collected=200)
        for texts in (first, later):
            self.assertIn("图鉴 #0025", texts)
            self.assertTrue(any("闪光×3" in text for text in texts))
        self.assertTrue(any("第 8/9 页" in text for text in later))

    def test_same_number_forms_keep_complete_distinguishing_names(self):
        keys = ["6:超级喷火龙Ｘ", "6:超级喷火龙Ｙ", "555:伽勒尔达摩狒狒（达摩模式）"]
        items = [
            self.owned(key, shiny=1 if index == 1 else 0)
            for index, key in enumerate(keys)
        ]
        texts = self.render(items)
        content = "".join(texts)
        self.assertIn("超级喷火龙Ｘ", content)
        self.assertIn("超级喷火龙Ｙ", content)
        self.assertIn("伽勒尔达摩狒狒（达摩模式）", content)
        self.assertEqual(texts.count("图鉴 #0006"), 2)
        self.assertFalse(any("…" in text for text in texts))

    def test_generic_form_keeps_species_and_form_without_changing_input_order(self):
        items = [self.owned("386:防御形态"), self.owned("386:攻击形态")]
        texts = self.render(items)
        content = "".join(texts)
        self.assertIn("代欧奇希斯（防御形态）", content)
        self.assertIn("代欧奇希斯（攻击形态）", content)
        self.assertLess(content.index("防御形态"), content.index("攻击形态"))
        self.assertEqual(texts.count("图鉴 #0386"), 2)

    def test_unrenderable_full_form_name_fails_instead_of_losing_its_suffix(self):
        item = self.owned("6:超级喷火龙Ｘ")
        form = "超级喷火龙的特别漫长形态" * 10 + "X"
        changed = replace(
            item.partner, name=form, row={**item.partner.row, "form_name": form}
        )
        item = replace(item, partner=changed)
        before = deepcopy(item)
        with self.assertRaisesRegex(ValueError, "完整形态名"):
            self.render([item])
        self.assertEqual(item, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
