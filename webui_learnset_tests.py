"""API regression checks for the searchable move reference (no network required)."""
import asyncio
import contextlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tests

with contextlib.redirect_stdout(io.StringIO()):
    tests.test_main_imports()

from gacha import GachaRules
from pokedex_db import PokedexDB
from webui import PokemonPartnerWebUI


class LearnsetAPITests(unittest.TestCase):
    def setUp(self):
        self.db = PokedexDB()
        self.ui = PokemonPartnerWebUI(SimpleNamespace(
            db=self.db,
            partner_service=SimpleNamespace(store=SimpleNamespace(rules=GachaRules.from_config({}))),
        ))
        self.ui._artwork_data_url = AsyncMock(return_value="")

    def detail(self, name, form=""):
        with patch("webui._query", side_effect=lambda key, default="": {"key": name, "form": form}.get(key, default)):
            return asyncio.run(self.ui.api_dex_detail())

    def test_details_cover_every_learnable_move_without_changing_legacy_shape(self):
        result = self.detail("皮卡丘")
        self.assertTrue(result["ok"])
        learnset = result["learnset"]
        names = {name for name, _ in learnset["level_up"]} | set(learnset["machine"]) | set(learnset["egg"])
        self.assertEqual(names, set(result["move_details"]))
        self.assertTrue(all(isinstance(name, str) for name in learnset["machine"]))
        thunderbolt = result["move_details"]["十万伏特"]
        self.assertEqual(thunderbolt["type"], "电")
        self.assertEqual(thunderbolt["power"], "90")
        self.assertIn("麻痹", thunderbolt["description"])

    def test_form_learning_source_is_disclosed(self):
        result = self.detail("超级烈咬陆鲨")
        self.assertTrue(result["ok"])
        self.assertIn("默认形态", result["learnset_note"])
        self.assertNotIn("默认形态", self.detail("烈咬陆鲨")["learnset_note"])

    def test_missing_move_record_still_returns_its_name(self):
        with patch.object(self.db, "learnset", return_value={"level_up": [("未收录招式", None)], "machine": [], "egg": []}):
            result = self.detail("皮卡丘")
        self.assertTrue(result["ok"])
        self.assertEqual(result["learnset"]["level_up"], [("未收录招式", None)])
        self.assertEqual(result["move_details"]["未收录招式"]["description"], "")

    def test_numeric_detail_preserves_the_requested_form(self):
        result = self.detail("0445", "超级烈咬陆鲨")
        self.assertTrue(result["ok"])
        self.assertEqual(result["row"]["form_name"], "超级烈咬陆鲨")
        self.assertEqual(result["bst"], 700)
        self.assertEqual(self.detail("0445")["bst"], 600)
        self.assertFalse(self.detail("0445", "不存在形态")["ok"])

    def test_list_thumbnails_keep_form_identity(self):
        with patch("webui._query", side_effect=lambda key, default="": {"q": "烈咬陆鲨", "forms": "1"}.get(key, default)), patch.object(self.ui, "_local_thumbnail", side_effect=lambda row: row.get("form_name") or "default"):
            result = asyncio.run(self.ui.api_dex_list())
        self.assertTrue(result["ok"])
        self.assertGreater(len(result["items"]), 1)
        for item in result["items"]:
            self.assertEqual(item["artwork"], item["form_name"] or "default")

    def test_pokedex_browsing_survives_disabled_draws_and_invalid_draw_weights(self):
        self.ui.plugin.partner_service = None
        self.ui.plugin.config = {
            "gacha_enabled": False,
            "rarity_weights": {"C": 0, "R": 0, "SR": 0, "SSR": 0},
            "rare_min_bst": 300, "super_rare_min_bst": 400, "ultra_rare_min_bst": 500,
        }
        with patch("webui._query", side_effect=lambda key, default="": {"q": "皮卡丘"}.get(key, default)), patch.object(self.ui, "_local_thumbnail", return_value=""):
            result = asyncio.run(self.ui.api_dex_list())
        self.assertTrue(result["ok"])
        self.assertEqual(result["items"][0]["rarity"], "R")
        detail = self.detail("皮卡丘")
        self.assertTrue(detail["ok"])
        self.assertEqual(detail["rarity"], "R")
        self.assertIsNone(self.ui.plugin.partner_service)

    def test_invalid_thresholds_do_not_block_read_only_pokedex(self):
        self.ui.plugin.partner_service = None
        self.ui.plugin.config = {
            "gacha_enabled": False,
            "rare_min_bst": 500, "super_rare_min_bst": 400, "ultra_rare_min_bst": 300,
        }
        result = self.detail("皮卡丘")
        self.assertTrue(result["ok"])
        self.assertEqual(result["rarity"], "C")
        self.assertIsNone(self.ui.plugin.partner_service)


if __name__ == "__main__":
    unittest.main()
