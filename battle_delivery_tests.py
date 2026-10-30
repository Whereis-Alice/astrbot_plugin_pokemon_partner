"""Battle delivery regressions: shared snapshots, image forwards and safe fallbacks."""

import asyncio
import contextlib
import io
import json
import sys
import types
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from PIL import Image

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
        self.send_attempts = 0

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
        self.send_attempts += 1
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
        self.battle = build_battle_report(
            self.db,
            self.db.pokemon("皮卡丘"),
            self.db.pokemon("喷火龙"),
            50,
            seed=7,
            detailed=True,
        )
        self.report = format_battle_report_data(self.battle)

    def output(self, event, *, mode=None):
        async def run():
            return [
                result
                async for result in self.plugin._send_battle_report(
                    event, self.battle, mode=mode
                )
            ]

        return asyncio.run(run())

    def test_config_default_override_and_image_switch(self):
        self.assertEqual(self.plugin._battle_output_mode("对战 A B"), "card")
        self.plugin.config["battle_output_mode"] = "forward"
        self.assertEqual(
            self.plugin._battle_output_mode("对战 A B 50 详细 种子=7"), "forward"
        )
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 转发 文本"), "text")
        self.plugin.config["battle_output_mode"] = "card_forward"
        self.assertEqual(self.plugin._battle_output_mode("对战 A B"), "card_forward")
        self.assertEqual(
            self.plugin._battle_output_mode("对战 A B 图片转发"), "card_forward"
        )
        self.assertEqual(
            self.plugin._battle_output_mode("对战 A B 卡片转发"), "card_forward"
        )
        self.assertEqual(
            self.plugin._battle_output_mode("对战 A B 图片转发 文本"), "text"
        )
        self.assertEqual(
            self.plugin._battle_output_mode("对战 A B 文本 图片转发"), "card_forward"
        )
        self.assertEqual(
            self.plugin._battle_output_mode("对战 A B 图片转发注释"), "card_forward"
        )  # 配置仍生效，尾文不算覆盖
        self.plugin.config["battle_output_mode"] = "card"
        self.assertEqual(
            self.plugin._battle_output_mode("对战 A B 图片转发注释"), "card"
        )
        self.plugin.config["enable_image_card"] = False
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 卡片"), "text")
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 转发"), "forward")
        self.assertEqual(self.plugin._battle_output_mode("对战 A B 图片转发"), "text")

    def test_text_preserves_original_report(self):
        result = self.output(Event("对战 A B 文本"))
        self.assertEqual("\n".join(item[1] for item in result), self.report)

    def test_explicit_card_mode_also_respects_image_switch(self):
        self.plugin.config["enable_image_card"] = False
        with patch.object(
            self.plugin,
            "_battle_card_pages",
            AsyncMock(side_effect=AssertionError("images disabled")),
        ):
            result = self.output(Event(), mode="card")
        self.assertEqual("\n".join(item[1] for item in result), self.report)

    def test_card_pages_send_in_order_without_text_duplicates(self):
        with (
            patch.object(
                self.plugin,
                "_battle_card_pages",
                AsyncMock(return_value=[b"one", b"two"]),
            ),
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=lambda content: ("image", content),
            ),
        ):
            result = self.output(Event(), mode="card")
        self.assertEqual(
            result, [("chain", [("image", b"one")]), ("chain", [("image", b"two")])]
        )

    def test_image_forward_sends_all_images_as_one_forward_without_normal_replies(self):
        event = Event(platform="aiocqhttp")
        pages = [b"poster", b"moves", b"turns"]
        with (
            patch.object(
                self.plugin, "_battle_card_pages", AsyncMock(return_value=pages)
            ) as render,
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=lambda content: ("image", content),
            ),
            patch.object(
                main.Comp,
                "Node",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
            patch.object(
                main.Comp,
                "Nodes",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
            patch(
                "main.build_battle_report",
                side_effect=AssertionError("must not simulate again"),
            ),
        ):
            result = self.output(event, mode="card_forward")
        self.assertEqual(result, [])
        self.assertEqual(event.send_attempts, 1)
        self.assertEqual(len(event.sent), 1)
        nodes = event.sent[0].chain[0].nodes
        self.assertEqual(
            [node.content for node in nodes], [[("image", page)] for page in pages]
        )
        self.assertEqual(
            [node.name for node in nodes],
            [
                "宝可梦对战 · 对战结果",
                "宝可梦对战 · 双方配招与效果",
                "宝可梦对战 · 对战过程",
            ],
        )
        self.assertTrue(all(node.uin == "123456" for node in nodes))
        render.assert_awaited_once_with(self.battle)

    def test_unsupported_image_forward_returns_same_images_with_notice_on_first_image(
        self,
    ):
        event = Event(platform="webchat")
        pages = [b"poster", b"moves", b"turns"]
        with (
            patch.object(
                self.plugin, "_battle_card_pages", AsyncMock(return_value=pages)
            ) as render,
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=lambda content: ("image", content),
            ),
            patch.object(main.Comp, "Plain", side_effect=lambda text: ("notice", text)),
        ):
            result = self.output(event, mode="card_forward")
        self.assertEqual(len(result), 3)
        self.assertTrue(all(kind == "chain" for kind, _ in result))
        self.assertIn("当前平台不支持图片合并转发", result[0][1][0][1])
        self.assertEqual(
            [content[-1] for _, content in result], [("image", page) for page in pages]
        )
        self.assertEqual(event.send_attempts, 0)
        render.assert_awaited_once_with(self.battle)

    def test_failed_image_forward_reuses_rendered_images_without_text_report_duplicates(
        self,
    ):
        event = Event(platform="aiocqhttp", fail=True)
        pages = [b"poster", b"moves", b"turns"]
        with (
            patch.object(
                self.plugin, "_battle_card_pages", AsyncMock(return_value=pages)
            ) as render,
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=lambda content: ("image", content),
            ) as convert,
            patch.object(
                main.Comp,
                "Node",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
            patch.object(
                main.Comp,
                "Nodes",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
            patch.object(main.Comp, "Plain", side_effect=lambda text: ("notice", text)),
            patch(
                "main.build_battle_report",
                side_effect=AssertionError("must not simulate again"),
            ),
        ):
            result = self.output(event, mode="card_forward")
        self.assertEqual(len(result), 3)
        self.assertTrue(all(kind == "chain" for kind, _ in result))
        self.assertIn("图片合并转发暂时无法发送", result[0][1][0][1])
        self.assertEqual(
            [content[-1] for _, content in result], [("image", page) for page in pages]
        )
        self.assertEqual(event.send_attempts, 1)
        self.assertEqual(event.sent, [])
        self.assertEqual(convert.call_count, 3)
        render.assert_awaited_once_with(self.battle)

    def test_forward_node_construction_failure_falls_back_to_prepared_images(self):
        event = Event(platform="aiocqhttp")
        with (
            patch.object(
                self.plugin,
                "_battle_card_pages",
                AsyncMock(return_value=[b"one", b"two"]),
            ),
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=lambda content: ("image", content),
            ),
            patch.object(
                main.Comp,
                "Node",
                create=True,
                side_effect=RuntimeError("invalid forward node"),
            ),
            patch.object(main.Comp, "Plain", side_effect=lambda text: ("notice", text)),
        ):
            result = self.output(event, mode="card_forward")
        self.assertEqual(len(result), 2)
        self.assertEqual(
            [content[-1] for _, content in result],
            [("image", b"one"), ("image", b"two")],
        )
        self.assertEqual(event.send_attempts, 0)

    def test_image_forward_component_failure_precedes_any_send(self):
        event = Event(platform="aiocqhttp")
        with (
            patch.object(
                self.plugin,
                "_battle_card_pages",
                AsyncMock(return_value=[b"one", b"two"]),
            ),
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=[("image", b"one"), ValueError("bad image")],
            ),
        ):
            result = self.output(event, mode="card_forward")
        self.assertTrue(all(kind == "text" for kind, _ in result))
        self.assertTrue(
            "\n".join(content for _, content in result).endswith(self.report)
        )
        self.assertEqual(event.send_attempts, 0)

    def test_image_forward_fallback_notice_failure_returns_only_same_text(self):
        event = Event(platform="webchat")
        with (
            patch.object(
                self.plugin,
                "_battle_card_pages",
                AsyncMock(return_value=[b"one", b"two"]),
            ),
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=lambda content: ("image", content),
            ),
            patch.object(
                main.Comp, "Plain", side_effect=RuntimeError("notice unavailable")
            ),
        ):
            result = self.output(event, mode="card_forward")
        self.assertTrue(all(kind == "text" for kind, _ in result))
        self.assertTrue(
            "\n".join(content for _, content in result).endswith(self.report)
        )
        self.assertEqual(event.send_attempts, 0)

    def test_explicit_image_forward_respects_disabled_images_without_rendering(self):
        self.plugin.config["enable_image_card"] = False
        event = Event(platform="aiocqhttp")
        with patch.object(
            self.plugin,
            "_battle_card_pages",
            AsyncMock(side_effect=AssertionError("images disabled")),
        ):
            result = self.output(event, mode="card_forward")
        self.assertEqual("\n".join(content for _, content in result), self.report)
        self.assertEqual(event.send_attempts, 0)

    def test_image_failure_keeps_every_turn_even_when_text_preview_was_requested(self):
        self.battle["detailed"] = False
        self.battle["result"]["turns"] = 25
        self.battle["result"]["turn_log"] = [
            [f"第{n}回合：", f"ROUND_MARK_{n:02d} 状态与伤害变化。"]
            for n in range(1, 26)
        ]
        original = deepcopy(self.battle)
        for mode in ("card", "card_forward"):
            with (
                self.subTest(mode=mode),
                patch.object(
                    self.plugin,
                    "_battle_card_pages",
                    AsyncMock(side_effect=RuntimeError("render unavailable")),
                ) as render,
                patch(
                    "main.build_battle_report",
                    side_effect=AssertionError("must not simulate again"),
                ),
            ):
                result = self.output(Event(platform="aiocqhttp"), mode=mode)
            text = "\n".join(content for _, content in result)
            for turn in range(1, 26):
                self.assertIn(f"ROUND_MARK_{turn:02d}", text)
            self.assertEqual(self.battle, original)
            render.assert_awaited_once_with(self.battle)

    def test_card_failure_reuses_same_report(self):
        with (
            patch.object(
                self.plugin,
                "_battle_card_pages",
                AsyncMock(side_effect=RuntimeError("render error")),
            ),
            patch(
                "main.build_battle_report",
                side_effect=AssertionError("must not simulate again"),
            ),
        ):
            result = self.output(Event(), mode="card")
        text = "\n".join(item[1] for item in result)
        self.assertIn("已改为文本战报", text)
        self.assertTrue(text.endswith(self.report))

    def test_image_component_failure_happens_before_first_page(self):
        with (
            patch.object(
                self.plugin,
                "_battle_card_pages",
                AsyncMock(return_value=[b"one", b"two"]),
            ),
            patch.object(
                main.Comp.Image,
                "fromBytes",
                side_effect=[("image", b"one"), ValueError("bad image")],
            ),
        ):
            result = self.output(Event(), mode="card")
        self.assertTrue(all(item[0] == "text" for item in result))

    def test_unsupported_forward_platform_returns_text(self):
        event = Event(platform="webchat")
        result = self.output(event, mode="forward")
        self.assertIn("当前平台不支持合并转发", result[0][1])
        self.assertEqual(event.sent, [])

    def test_onebot_forward_uses_nodes_and_no_second_reply(self):
        event = Event(platform="aiocqhttp")
        with (
            patch.object(
                main.Comp,
                "Node",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
            patch.object(
                main.Comp,
                "Nodes",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
            patch.object(main.Comp, "Plain", side_effect=lambda text: text),
        ):
            result = self.output(event, mode="forward")
        self.assertEqual(result, [])
        self.assertEqual(len(event.sent), 1)
        nodes = event.sent[0].chain[0].nodes
        self.assertGreaterEqual(
            len(nodes), 5
        )  # overview, each moveset, result, and each turn
        self.assertTrue(all(node.uin == "123456" for node in nodes))
        text = "\n\n".join(node.content[0] for node in nodes)
        self.assertIn("模拟效果", text)
        self.assertIn("第1回合", text)
        self.assertIn("种子=7", text)

    def test_onebot_forward_failure_returns_same_text(self):
        event = Event(platform="aiocqhttp", fail=True)
        with (
            patch.object(
                main.Comp,
                "Node",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
            patch.object(
                main.Comp,
                "Nodes",
                create=True,
                side_effect=lambda **kw: SimpleNamespace(**kw),
            ),
        ):
            result = self.output(event, mode="forward")
        self.assertIn("合并转发暂时无法发送", result[0][1])
        self.assertTrue("\n".join(item[1] for item in result).endswith(self.report))

    def test_command_builds_once_and_preserves_seed(self):
        event = Event("对战 皮卡丘 喷火龙 50 详细 种子=7 文本")

        async def run():
            return [
                result
                async for result in self.plugin.cmd_battle(
                    event, "皮卡丘", "喷火龙", "50"
                )
            ]

        with (
            patch("main.build_battle_report", return_value=self.battle) as build,
            patch.object(self.plugin, "_champ_ranks_line", return_value=""),
            patch.object(
                self.plugin,
                "_battle_text",
                side_effect=AssertionError("old path recalculates"),
            ),
        ):
            result = asyncio.run(run())
        build.assert_called_once()
        self.assertEqual(build.call_args.kwargs["seed"], 7)
        self.assertTrue(build.call_args.kwargs["detailed"])
        self.assertEqual("\n".join(item[1] for item in result), self.report)

    def test_gacha_disabled_still_loads_artwork_and_renders(self):
        fake_artwork = SimpleNamespace(
            images_dir=None, prefetch=AsyncMock(), load=Mock(return_value=None)
        )
        renderer = Mock(return_value=[b"page"])
        module = types.ModuleType("battle_cards")
        module.render_battle_cards = renderer
        self.plugin.config["fallback_web_image"] = False
        with (
            patch("main.ArtworkCache", return_value=fake_artwork) as cache,
            patch.object(
                self.plugin, "_partner_state_dir", return_value=Path("isolated-data")
            ),
            patch.dict(sys.modules, {"battle_cards": module}),
        ):
            pages = asyncio.run(self.plugin._battle_card_pages(self.battle))
        self.assertEqual(pages, [b"page"])
        self.assertFalse(cache.call_args.kwargs["allow_download"])
        fake_artwork.prefetch.assert_awaited_once()
        self.assertIn("image_loader", renderer.call_args.kwargs)

    def test_mirror_owned_battle_prefetches_and_loads_distinct_normal_and_shiny_requests(
        self,
    ):
        self.battle = build_battle_report(
            self.db, self.db.pokemon("梦幻"), self.db.pokemon("梦幻"), 50, seed=7
        )
        self.battle["owned_items"] = [
            {"shiny": False},
            SimpleNamespace(shiny=True),
        ]
        original = deepcopy(self.battle)
        artwork = SimpleNamespace(
            images_dir=None, prefetch=AsyncMock(), load=Mock(return_value=None)
        )
        self.plugin.artwork = artwork
        pages = asyncio.run(self.plugin._battle_card_pages(self.battle))
        self.assertEqual(len(pages), 3)
        rows = artwork.prefetch.await_args.args[0]
        self.assertEqual(rows[0]["id"], rows[1]["id"])
        self.assertNotIn("_artwork_shiny", rows[0])
        self.assertIs(rows[1]["_artwork_shiny"], True)
        self.assertIsNot(rows[0], self.battle["row1"])
        self.assertIsNot(rows[1], self.battle["row2"])
        self.assertEqual(artwork.prefetch.await_args.kwargs, {"limit": 2})
        requests = [call.args[0] for call in artwork.load.call_args_list]
        self.assertEqual(len(requests), 2)
        self.assertNotIn("_artwork_shiny", requests[0])
        self.assertIs(requests[1]["_artwork_shiny"], True)
        self.assertEqual(self.battle, original)

    def test_shiny_form_uses_exact_shiny_or_normal_form_and_never_default_artwork(self):
        base_row = self.db.pokemon("喷火龙")
        form_row = {**base_row, "form_name": "超级喷火龙X"}
        request = {**form_row, "_artwork_shiny": True}
        prefix = f"{form_row['id_raw']}-{form_row['name_zh']}"
        for available, expected in (
            ("shiny", (250, 200, 30, 255)),
            ("normal", (30, 80, 200, 255)),
            ("default_only", None),
        ):
            with (
                self.subTest(available=available),
                TemporaryDirectory(prefix="partner-battle-form-") as temporary,
            ):
                root = Path(temporary)
                official = root / "images" / "official"
                home = root / "images" / "home"
                official.mkdir(parents=True)
                home.mkdir(parents=True)
                # A default image must never satisfy a requested Mega form.
                Image.new("RGB", (12, 12), (20, 180, 70)).save(
                    official / f"{prefix}.png"
                )
                if available in {"shiny", "normal"}:
                    Image.new("RGB", (12, 12), (30, 80, 200)).save(
                        official / f"{prefix}-{form_row['form_name']}.png"
                    )
                if available == "shiny":
                    Image.new("RGB", (12, 12), (250, 200, 30)).save(
                        home / f"{prefix}-{form_row['form_name']}-shiny.png"
                    )
                artwork = main.ArtworkCache(
                    root / "cache",
                    db=self.db,
                    images_dir=root / "images",
                    allow_download=False,
                )
                self.plugin.artwork = artwork
                loaded = []

                def render(battle, *, image_loader):
                    image = image_loader(request)
                    loaded.append(image.getpixel((0, 0)) if image is not None else None)
                    return [b"page"]

                module = types.ModuleType("battle_cards")
                module.render_battle_cards = render
                original = deepcopy(self.battle)
                with (
                    patch.dict(sys.modules, {"battle_cards": module}),
                    patch.object(
                        self.db, "find_image", wraps=self.db.find_image
                    ) as lookup,
                    patch.object(artwork, "load", wraps=artwork.load) as load,
                ):
                    pages = asyncio.run(self.plugin._battle_card_pages(self.battle))
                self.assertEqual(pages, [b"page"])
                self.assertEqual(loaded, [expected])
                load.assert_called_once_with(request)
                self.assertEqual(request, {**form_row, "_artwork_shiny": True})
                self.assertEqual(self.battle, original)

    def test_regional_forms_use_downloaded_cache_without_an_images_directory(self):
        with TemporaryDirectory(prefix='partner-battle-downloaded-form-') as temporary:
            root = Path(temporary)
            artwork = main.ArtworkCache(root, db=self.db, allow_download=False)
            row = self.db.pokemon('阿罗拉猫老大')
            for shiny, color in ((False, (40, 90, 160, 255)), (True, (210, 160, 40, 255))):
                request = dict(row)
                if shiny:
                    request['_artwork_shiny'] = True
                folder = root / 'shiny' / 'forms' if shiny else root / 'forms'
                folder.mkdir(parents=True, exist_ok=True)
                Image.new('RGBA', (20, 20), color).save(folder / '10108.png')
                self.plugin.artwork = artwork
                loaded = []

                def render(battle, *, image_loader):
                    image = image_loader(request)
                    loaded.append(image.getpixel((0, 0)) if image is not None else None)
                    return [b'page']

                module = types.ModuleType('battle_cards')
                module.render_battle_cards = render
                with patch.dict(sys.modules, {'battle_cards': module}):
                    self.assertEqual(asyncio.run(self.plugin._battle_card_pages(self.battle)), [b'page'])
                self.assertEqual(loaded, [color])

    def test_schema_validates_four_known_modes(self):
        schema = json.loads(
            (Path(__file__).parent / "_conf_schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["battle_output_mode"]["default"], "card")
        PokemonPartnerWebUI._validate_settings(
            {"battle_output_mode": "forward"}, {"battle_output_mode": "forward"}
        )
        self.assertEqual(
            set(schema["battle_output_mode"]["options"]),
            {"card", "card_forward", "forward", "text"},
        )
        PokemonPartnerWebUI._validate_settings(
            {"battle_output_mode": "card_forward"},
            {"battle_output_mode": "card_forward"},
        )
        with self.assertRaises(ValueError):
            PokemonPartnerWebUI._validate_settings(
                {"battle_output_mode": "invalid"}, {"battle_output_mode": "invalid"}
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
