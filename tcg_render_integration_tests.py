"""Card-style routing and persisted settings without live draws or visual assumptions."""

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

_runtime = tempfile.TemporaryDirectory(prefix="partner-tcg-integration-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

import main
import partner_cards
import rarity_cards
from webui import PokemonPartnerWebUI


class CardRoutingTests(unittest.TestCase):
    def setUp(self):
        self.items = (object(),)
        self.loader = lambda row: None
        self.options = dict(nickname="小智", remaining=2, daily_limit=3, dust=10,
                            pity_ssr=1, ssr_pity_after=30, image_loader=self.loader)
        self.classic = Mock(return_value=b"classic")
        self.tcg = Mock(return_value=b"tcg")
        self.patches = (
            patch.object(rarity_cards, "render_rarity_draw", self.classic),
            patch.dict(sys.modules, {"tcg_cards": SimpleNamespace(render_tcg_draw=self.tcg)}),
        )
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def test_default_keeps_existing_renderer_and_all_draw_context(self):
        self.assertEqual(partner_cards.render_draw_card(self.items, **self.options), b"classic")
        self.classic.assert_called_once_with(self.items, **self.options)
        self.tcg.assert_not_called()

    def test_tcg_uses_independent_renderer_with_the_same_context(self):
        self.assertEqual(partner_cards.render_draw_card(self.items, style="tcg", **self.options), b"tcg")
        self.tcg.assert_called_once_with(self.items, **self.options)
        self.classic.assert_not_called()

    def test_unknown_style_falls_back_to_existing_renderer(self):
        self.assertEqual(partner_cards.render_draw_card(self.items, style="unavailable", **self.options), b"classic")
        self.tcg.assert_not_called()

    def test_both_styles_share_the_same_overflow_collection(self):
        for style, renderer in (("classic", self.classic), ("tcg", self.tcg)):
            with self.subTest(style=style):
                notes = []
                partner_cards.render_draw_card(self.items, style=style, details_out=notes, **self.options)
                self.assertIs(renderer.call_args.kwargs["details_out"], notes)


class DailyCardSendTests(unittest.TestCase):
    def setUp(self):
        self.plugin = object.__new__(main.PokemonPartnerPlugin)
        self.plugin.config = {"enable_image_card": True}
        self.loader = lambda row: None
        self.plugin.partner_service = SimpleNamespace(
            recovery_notice=lambda: "", artwork=self.loader,
            store=SimpleNamespace(rules=SimpleNamespace(ssr_pity_after=30)),
        )
        self.plugin._ensure_artwork = AsyncMock()
        item = SimpleNamespace(row={}, shiny=True, is_new=True, dust=0,
                               dex_raw="0151", name="梦幻", rarity="SSR", rarity_name="传说")
        self.outcome = SimpleNamespace(items=(item,), remaining=2, daily_limit=3,
            dust=10, pity_ssr=1, capped=False, requested=1, milestones=())
        self.event = SimpleNamespace(chain_result=lambda chain: chain, plain_result=lambda text: text)

    def send(self):
        async def run():
            return [result async for result in self.plugin._send_draw_card(self.event, self.outcome, "小智")]
        return asyncio.run(run())

    def test_style_is_read_again_for_the_next_card(self):
        with patch.object(main, "render_draw_card", Mock(return_value=b"card")) as render:
            self.assertEqual(len(self.send()), 1)
            self.assertEqual(render.call_args.kwargs["style"], "classic")
            self.plugin.config["daily_card_style"] = "tcg"
            self.assertEqual(len(self.send()), 1)
            self.assertEqual(render.call_args.kwargs["style"], "tcg")
            self.assertIs(render.call_args.args[0], self.outcome.items)
            self.assertIs(render.call_args.kwargs["image_loader"], self.loader)
            self.assertEqual(self.plugin._ensure_artwork.call_args.args[0],
                             [{"_artwork_shiny": True}, {}])

    def test_render_failure_preserves_the_existing_draw_as_text(self):
        self.plugin.config["daily_card_style"] = "tcg"
        with patch.object(main, "render_draw_card", side_effect=RuntimeError("render failed")) as render:
            results = self.send()
        render.assert_called_once()
        self.assertEqual(len(results), 1)
        self.assertIn("0151", results[0])
        self.assertIn("闪光", results[0])
        self.assertIn("今日剩余 2/3", results[0])

    def test_success_sends_notice_then_one_pure_card_for_either_style(self):
        for style in ("classic", "tcg"):
            with self.subTest(style=style), patch.object(main, "render_draw_card", return_value=b"card"):
                self.plugin.config["daily_card_style"] = style
                results = self.send()
                self.assertEqual(len(results), 1)
                self.assertEqual(len(results[0]), 2)
                notice, image = results[0]
                self.assertIsInstance(notice, main.Comp.Plain)
                self.assertIsInstance(image, main.Comp.Image)
                for value in ("今日宝可梦", "小智", "0151", "梦幻", "闪光", "今日剩余 2/3", "星尘 10", "SSR 保底进度 1/30", "新宝可梦已加入图鉴"):
                    self.assertIn(value, notice.text)
                self.assertIn("base64://", image.file)

    def test_missing_card_details_are_added_once_without_repeating_the_draw(self):
        value = "#0151 梦幻：进化：完整的长进化资料。"
        def render(*args, **kwargs):
            kwargs["details_out"].extend((value, value))
            return b"card"
        with patch.object(main, "render_draw_card", side_effect=render):
            results = self.send()
        text = results[0][0].text
        self.assertEqual(text.count(value), 1)
        self.assertEqual(text.count("今日剩余 2/3"), 1)
        self.assertNotIn("图鉴摘要（续）", text)

    def test_capped_draw_and_milestone_are_reported_once_in_text(self):
        self.outcome.capped, self.outcome.requested = True, 10
        self.outcome.milestones = (SimpleNamespace(threshold=10, dust=100, bonus=1),)
        with patch.object(main, "render_draw_card", return_value=b"card"):
            results = self.send()
        text = results[0][0].text
        self.assertEqual(text.count("本次请求 10 次"), 1)
        self.assertEqual(text.count("收集达到 10 位"), 1)
        self.assertIn("额外 1 次", text)

    def test_duplicate_dust_is_text_and_does_not_claim_a_new_entry(self):
        self.outcome.items[0].is_new = False
        self.outcome.items[0].dust = 120
        with patch.object(main, "render_draw_card", return_value=b"card"):
            results = self.send()
        self.assertIn("+120星尘", results[0][0].text)
        self.assertNotIn("新宝可梦已加入图鉴", results[0][0].text)

    def test_disabled_ssr_pity_does_not_show_old_pity_progress(self):
        self.plugin.partner_service.store.rules.ssr_pity_after = 0
        with patch.object(main, "render_draw_card", return_value=b"card"):
            results = self.send()
        self.assertNotIn("SSR 保底进度", results[0][0].text)

    def test_long_extra_details_keep_the_image_and_all_text_within_message_limit(self):
        tail = "完整进化资料末尾"
        value = "进化：" + "长进化条件，" * 400 + tail
        def render(*args, **kwargs):
            kwargs["details_out"].append(value)
            return b"card"
        with patch.object(main, "render_draw_card", side_effect=render):
            results = self.send()
        self.assertGreater(len(results), 1)
        self.assertTrue(all(isinstance(part, str) and len(part) <= 1800 for part in results[:-1]))
        self.assertIsInstance(results[-1][1], main.Comp.Image)
        self.assertLessEqual(len(results[-1][0].text), 1800)
        all_text = "".join(results[:-1]) + results[-1][0].text
        self.assertIn(value, all_text)
        self.assertIn(tail, all_text)

    def test_disabling_collection_before_delivery_still_sends_committed_result(self):
        self.plugin.partner_service = None
        self.plugin._partner_init_error = "宝可梦系统已关闭"
        with patch.object(main, "render_draw_card") as render:
            results = self.send()
        self.assertEqual(len(results), 1)
        self.assertIn("0151", results[0])
        self.assertIn("闪光", results[0])
        self.assertIn("今日剩余 2/3", results[0])
        render.assert_not_called()
        self.plugin._ensure_artwork.assert_not_awaited()


class CardSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="partner-tcg-settings-")
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "settings.json"

        class Config(dict):
            def save_config(config):
                self.path.write_text(json.dumps(config), encoding="utf-8")

        self.config = Config(daily_card_style="classic", daily_draw_limit=3)
        self.config.save_config()
        plugin = SimpleNamespace(config=self.config, partner_service=object(), _init_partners=lambda: None)
        self.ui = PokemonPartnerWebUI(plugin)

    def save(self, value):
        with patch.object(self.ui, "_body", AsyncMock(return_value={"config": {"daily_card_style": value}})):
            return asyncio.run(self.ui.api_settings_save())

    def test_both_styles_save_and_are_returned_without_changing_other_settings(self):
        for style in ("tcg", "classic"):
            with self.subTest(style=style):
                self.assertEqual(self.save(style), {"ok": True})
                saved = json.loads(self.path.read_text(encoding="utf-8"))
                self.assertEqual(saved, {"daily_card_style": style, "daily_draw_limit": 3})
                self.assertEqual(asyncio.run(self.ui.api_settings())["config"]["daily_card_style"], style)

    def test_invalid_styles_are_rejected_without_writing(self):
        before = self.path.read_bytes()
        for style in ("invalid", "TCG", True, 1):
            with self.subTest(style=style):
                self.assertFalse(self.save(style)["ok"])
                self.assertEqual(self.config["daily_card_style"], "classic")
                self.assertEqual(self.path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
