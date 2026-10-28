"""Owned duels through the actual command handlers, with isolated collection data."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import tests
with contextlib.redirect_stdout(io.StringIO()):
    tests.test_main_imports()
import main
from gacha import GachaRules
from owned_duels import OwnedDuelService
from partner_service import PartnerService
from pokedex_db import PokedexDB
from partner_cards import render_collection_card
from PIL import ImageDraw


class At:
    def __init__(self, qq, name=""):
        self.qq, self.name = qq, name


class Event:
    def __init__(self, user, raw, *, mentions=(), private=False):
        self.user, self.message_str, self.private = user, raw, private
        self.message_obj = SimpleNamespace(message=list(mentions), raw_message={})
    def get_self_id(self): return "999"
    def get_sender_id(self): return self.user
    def get_sender_name(self): return "小智" if self.user == "alice" else "小霞"
    def get_session_id(self): return "room"
    def get_group_id(self): return "" if self.private else "room"
    def is_private_chat(self): return self.private
    def plain_result(self, text): return ("text", text)
    def chain_result(self, chain): return ("chain", chain)


class OwnedDuelCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owned-duel-command-")
        self.addCleanup(self.temp.cleanup)
        self.db = PokedexDB()
        self.plugin = object.__new__(main.PokemonPartnerPlugin)
        self.plugin.db, self.plugin.config = self.db, {}
        self.plugin.partner_service = PartnerService.create(self.db, Path(self.temp.name), GachaRules.from_config({}))
        self.store = self.plugin.partner_service.store
        self.duels = OwnedDuelService(self.store)
        partners = {p.dex: p for p in self.store.partners if not p.row.get("form_name")}
        with self.store._transaction():
            for user, dexes in (("alice", (1, 25)), ("bob", (255,))):
                profile = self.store._profile("group:room", user, user)
                profile["owned"] = {partners[dex].key: {"normal": 1, "shiny": 0} for dex in dexes}
            self.store._save()
        self.patch_at = patch.object(main.Comp, "At", At, create=True)
        self.patch_plain = patch.object(main.Comp, "Plain", side_effect=lambda text: text)
        self.patch_at.start(); self.patch_plain.start()
        self.addCleanup(self.patch_at.stop); self.addCleanup(self.patch_plain.stop)

    def run_command(self, name, event, *args):
        async def collect():
            return [part async for part in getattr(self.plugin, name)(event, *args)]
        return asyncio.run(collect())

    def invite(self):
        return self.run_command("cmd_owned_challenge", Event("alice", "收藏挑战 @bob 2", mentions=[At("bob")]))

    def test_real_mention_selects_own_ordinal_and_invites_target(self):
        result = self.invite()
        self.assertEqual(result[0][0], "chain")
        self.assertEqual(result[0][1][0].qq, "bob")
        self.assertIn("序号 2 · 皮卡丘", result[0][1][1])
        self.assertIn("5 分钟", result[0][1][1])

    def test_accept_reuses_cards_and_records_both_trainers_once(self):
        self.invite()
        captured = []
        async def deliver(event, battle, **kwargs):
            captured.append(battle)
            yield ("card", b"card")
        with patch.object(self.plugin, "_send_battle_report", deliver):
            result = self.run_command("cmd_owned_accept", Event("bob", "接受对战 1 详细 卡片"))
            repeated = self.run_command("cmd_owned_accept", Event("bob", "接受对战 1"))
        self.assertEqual(result, [("card", b"card")])
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["trainer_names"], ["小智", "小霞"])
        self.assertEqual(captured[0]["row1"]["name_zh"], "皮卡丘")
        self.assertEqual(captured[0]["row2"]["name_zh"], "火稚鸡")
        self.assertTrue(captured[0]["detailed"])
        self.assertIn("没有等待", repeated[0][1])
        for user in ("alice", "bob"):
            self.assertEqual(self.duels.profile("group:room", user)["total"], 1)

    def test_simulation_failure_aborts_reservation_without_score(self):
        self.invite()
        with patch("main.build_battle_report", side_effect=RuntimeError("simulation failed")):
            result = self.run_command("cmd_owned_accept", Event("bob", "接受对战 1"))
        self.assertIn("未计入战绩", result[0][1])
        self.assertEqual(self.duels.profile("group:room", "bob")["total"], 0)
        self.assertEqual(self.duels.history("group:room"), [])
        self.assertEqual(self.invite()[0][0], "chain")

    def test_invalid_unowned_and_shiny_choices_never_create_match(self):
        for raw in ("收藏挑战 @bob 9", "收藏挑战 @bob 1 闪光"):
            result = self.run_command("cmd_owned_challenge", Event("alice", raw, mentions=[At("bob")]))
            self.assertEqual(result[0][0], "text")
        self.assertEqual(self.invite()[0][0], "chain")

    def test_private_or_self_challenges_are_rejected(self):
        result = self.run_command("cmd_owned_challenge", Event("alice", "收藏挑战 123 1", private=True))
        self.assertIn("群聊", result[0][1])
        result = self.run_command("cmd_owned_challenge", Event("alice", "收藏挑战 @alice 1", mentions=[At("alice")]))
        self.assertIn("自己", result[0][1])

    def test_cancel_and_reject_handlers_do_not_score(self):
        self.invite()
        self.assertIn("取消", self.run_command("cmd_owned_cancel", Event("alice", "取消对战"))[0][1])
        self.invite()
        self.assertIn("拒绝", self.run_command("cmd_owned_reject", Event("bob", "拒绝对战"))[0][1])
        self.assertEqual(self.duels.profile("group:room", "alice")["total"], 0)

    def test_text_collection_has_the_same_global_ordinals(self):
        self.plugin.config["enable_image_card"] = False
        result = self.run_command("cmd_partner_collection", Event("alice", "我的宝可梦"))
        self.assertIn("1. #", result[0][1])
        self.assertIn("2. #", result[0][1])
        self.assertIn("收藏序号", result[0][1])

    def test_collection_card_numbers_continue_across_pages(self):
        owned = self.store.owned_partners("group:room", "alice")
        labels = []
        original = ImageDraw.ImageDraw.text
        def capture(draw, xy, text, *args, **kwargs):
            labels.append(str(text))
            return original(draw, xy, text, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, "text", capture):
            render_collection_card(owned, nickname="小智", page=2, page_size=24, total=1025, collected=26)
        self.assertIn("序号 25", labels)
        self.assertIn("序号 26", labels)


if __name__ == "__main__":
    unittest.main(verbosity=2)
