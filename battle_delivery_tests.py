"""Battle delivery regressions: same simulation, three modes and safe fallbacks."""
import asyncio
import contextlib
import io
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import tests

with contextlib.redirect_stdout(io.StringIO()):
    tests.test_main_imports()

import main
from battle_report import build_battle_report, format_battle_report_data
from pokedex_db import PokedexDB
from webui import PokemonPartnerWebUI


class Event:
    def __init__(self, raw="", platform="webchat", fail=False):
        self.message_str = raw
        self.platform = platform
        self.fail = fail
        self.sent = []

    def plain_result(self, text):
        return ("text", text)

    def chain_result(self, chain):
        return ("chain", chain)

    def get_platform_name(self):
        return self.platform

    def get_self_id(self):
        return "123456"

    def get_sender_id(self):
        return "78910"

    async def send(self, chain):
        if self.fail:
            raise RuntimeError("OneBot forward unavailable")
        self.sent.append(chain)


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.db = PokedexDB()
        self.plugin = object.__new__(main.PokemonPartnerPlugin)
        self.plugin.config = {}
        self.plugin.db = self.db
        self.plugin.artwork = None
        self.plugin.renderer = SimpleNamespace(images_dir=None)
        self.battle = build_battle_report(self.db, self.db.pokemon("皮卡丘"), self.db.pokemon("喷火龙"), 50, seed=7, detailed=True)
        self.report = format_battle_report_data(self.battle)

    def output(self, event, *, mode=None):
        async def run():
            return [result async for result in self.plugin._send_battle_report(event, self.battle, mode=mode)]
        return asyncio.run(run())

    def test_config_default_override_and_image_switch(self):
        self.assertEqual(self.plugin._battle_output_mode("对战 A B"), "card")
        self.plugin.config["battle_output_mode"] = "forward"
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 50 详细 种子=7"), "forward")
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 转发 文本"), "text")
        self.plugin.config["enable_image_card"] = False
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 卡片"), "text")
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 转发"), "forward")

    def test_text_preserves_original_report(self):
        result = self.output(Event("对战 A B 文本"))
        self.assertEqual("\n".join(item[1] for item in result), self.report)

    def test_explicit_card_mode_also_respects_image_switch(self):
        self.plugin.config["enable_image_card"] = False
        with patch.object(self.plugin, "_battle_card_pages", AsyncMock(side_effect=AssertionError("images disabled"))):
            result = self.output(Event(), mode="card")
        self.assertEqual("\n".join(item[1] for item in result), self.report)

    def test_card_pages_send_in_order_without_text_duplicates(self):
        with patch.object(self.plugin, "_battle_card_pages", AsyncMock(return_value=[b"one", b"two"])), \
             patch.object(main.Comp.Image, "fromBytes", side_effect=lambda content: ("image", content)):
            result = self.output(Event(), mode="card")
        self.assertEqual(result, [("chain", [("image", b"one")]), ("chain", [("image", b"two")])])

    def test_card_failure_reuses_same_report(self):
        with patch.object(self.plugin, "_battle_card_pages", AsyncMock(side_effect=RuntimeError("render error"))), \
             patch("main.build_battle_report", side_effect=AssertionError("must not simulate again")):
            result = self.output(Event(), mode="card")
        text = "\n".join(item[1] for item in result)
        self.assertIn("已改为文本战报", text)
        self.assertTrue(text.endswith(self.report))

    def test_image_component_failure_happens_before_first_page(self):
        with patch.object(self.plugin, "_battle_card_pages", AsyncMock(return_value=[b"one", b"two"])), \
             patch.object(main.Comp.Image, "fromBytes", side_effect=[("image", b"one"), ValueError("bad image")]):
            result = self.output(Event(), mode="card")
        self.assertTrue(all(item[0] == "text" for item in result))

    def test_unsupported_forward_platform_returns_text(self):
        event = Event(platform="webchat")
        result = self.output(event, mode="forward")
        self.assertIn("当前平台不支持合并转发", result[0][1])
        self.assertEqual(event.sent, [])

    def test_onebot_forward_uses_nodes_and_no_second_reply(self):
        event = Event(platform="aiocqhttp")
        with patch.object(main.Comp, "Node", create=True, side_effect=lambda **kw: SimpleNamespace(**kw)), \
             patch.object(main.Comp, "Nodes", create=True, side_effect=lambda **kw: SimpleNamespace(**kw)), \
             patch.object(main.Comp, "Plain", side_effect=lambda text: text):
            result = self.output(event, mode="forward")
        self.assertEqual(result, [])
        self.assertEqual(len(event.sent), 1)
        nodes = event.sent[0].chain[0].nodes
        self.assertGreaterEqual(len(nodes), 5)  # overview, each moveset, result, and each turn
        self.assertTrue(all(node.uin == "123456" for node in nodes))
        text = "\n\n".join(node.content[0] for node in nodes)
        self.assertIn("模拟效果", text)
        self.assertIn("第1回合", text)
        self.assertIn("种子=7", text)

    def test_onebot_forward_failure_returns_same_text(self):
        event = Event(platform="aiocqhttp", fail=True)
        with patch.object(main.Comp, "Node", create=True, side_effect=lambda **kw: SimpleNamespace(**kw)), \
             patch.object(main.Comp, "Nodes", create=True, side_effect=lambda **kw: SimpleNamespace(**kw)):
            result = self.output(event, mode="forward")
        self.assertIn("合并转发暂时无法发送", result[0][1])
        self.assertTrue("\n".join(item[1] for item in result).endswith(self.report))

    def test_command_builds_once_and_preserves_seed(self):
        event = Event("对战 皮卡丘 喷火龙 50 详细 种子=7 文本")
        async def run():
            return [result async for result in self.plugin.cmd_battle(event, "皮卡丘", "喷火龙", "50")]
        with patch("main.build_battle_report", return_value=self.battle) as build, \
             patch.object(self.plugin, "_champ_ranks_line", return_value=""), \
             patch.object(self.plugin, "_battle_text", side_effect=AssertionError("old path recalculates")):
            result = asyncio.run(run())
        build.assert_called_once()
        self.assertEqual(build.call_args.kwargs["seed"], 7)
        self.assertTrue(build.call_args.kwargs["detailed"])
        self.assertEqual("\n".join(item[1] for item in result), self.report)

    def test_gacha_disabled_still_loads_artwork_and_renders(self):
        fake_artwork = SimpleNamespace(images_dir=None, prefetch=AsyncMock(), load=Mock(return_value=None))
        renderer = Mock(return_value=[b"page"])
        module = types.ModuleType("battle_cards")
        module.render_battle_cards = renderer
        self.plugin.config["fallback_web_image"] = False
        with patch("main.ArtworkCache", return_value=fake_artwork) as cache, \
             patch.object(self.plugin, "_partner_state_dir", return_value=Path("isolated-data")), \
             patch.dict(sys.modules, {"battle_cards": module}):
            pages = asyncio.run(self.plugin._battle_card_pages(self.battle))
        self.assertEqual(pages, [b"page"])
        self.assertFalse(cache.call_args.kwargs["allow_download"])
        fake_artwork.prefetch.assert_awaited_once()
        self.assertIn("image_loader", renderer.call_args.kwargs)

    def test_schema_validates_only_three_known_modes(self):
        schema = json.loads((Path(__file__).parent / "_conf_schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["battle_output_mode"]["default"], "card")
        PokemonPartnerWebUI._validate_settings({"battle_output_mode": "forward"}, {"battle_output_mode": "forward"})
        with self.assertRaises(ValueError):
            PokemonPartnerWebUI._validate_settings({"battle_output_mode": "invalid"}, {"battle_output_mode": "invalid"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
