"""Exercise real AstrBot OneBot image forwards with an in-memory protocol sink."""

from __future__ import annotations

import asyncio
import base64
import inspect
import io
import json
import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

# Importing the SDK can create runtime files; never use the real bot's data root.
_runtime = tempfile.TemporaryDirectory(prefix="partner-image-forward-sdk-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

import astrbot
import astrbot.api.message_components as Comp
import main
from astrbot.api.event import MessageChain
from astrbot.core.config.default import VERSION
from astrbot.core.platform.sources.aiocqhttp.aiocqhttp_message_event import (
    AiocqhttpMessageEvent,
)
from battle_report import build_battle_report, format_battle_report_data
from PIL import Image
from pokedex_db import PokedexDB


class ProtocolSink:
    """Record SDK protocol calls without contacting a QQ account or network."""

    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []

    async def call_action(self, action, **kwargs):
        self.calls.append((action, kwargs))
        if self.fail:
            raise RuntimeError("isolated OneBot forward failure")


class Event:
    def __init__(self, *, private=False, platform="aiocqhttp", fail=False):
        self.message_str = "对战 皮卡丘 喷火龙 图片转发"
        self.private = private
        self.platform = platform
        self.bot = ProtocolSink(fail=fail)

    def get_platform_name(self):
        return self.platform

    def get_self_id(self):
        return "123456"

    def get_sender_id(self):
        return "78910"

    def chain_result(self, components):
        chain = MessageChain()
        chain.chain.extend(components)
        return chain

    def plain_result(self, text):
        return MessageChain().message(text)

    async def send(self, chain):
        # The real adapter performs component conversion and protocol dispatch.
        # Its transport is an in-memory sink, not a real platform client.
        await AiocqhttpMessageEvent.send_message(
            self.bot,
            chain,
            is_group=not self.private,
            session_id="987654",
        )


class BattleImageForwardSDKTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = PokedexDB()
        cls.pages = []
        for size, color in (((28, 36), "red"), ((28, 52), "blue"), ((28, 78), "gold")):
            stream = io.BytesIO()
            Image.new("RGB", size, color).save(stream, format="JPEG")
            cls.pages.append(stream.getvalue())

    def setUp(self):
        self.plugin = object.__new__(main.PokemonPartnerPlugin)
        self.plugin.config = {"battle_output_mode": "card_forward"}
        self.battle = build_battle_report(
            self.db,
            self.db.pokemon("皮卡丘"),
            self.db.pokemon("喷火龙"),
            50,
            seed=7,
            detailed=False,
        )

    def output(self, event):
        async def run():
            return [
                result
                async for result in self.plugin._send_battle_report(
                    event, self.battle, mode="card_forward"
                )
            ]

        return asyncio.run(run())

    @staticmethod
    def decode_image(value):
        if isinstance(value, Comp.Image):
            value = value.file
        if not value.startswith("base64://"):
            raise AssertionError("SDK output did not retain the in-memory image")
        return base64.b64decode(value[len("base64://") :], validate=True)

    def assert_same_regular_images(self, results):
        self.assertEqual(len(results), 3)
        images = [
            component
            for result in results
            for component in result.chain
            if isinstance(component, Comp.Image)
        ]
        self.assertEqual([self.decode_image(image) for image in images], self.pages)
        self.assertEqual(len(results[0].chain), 2)
        self.assertIsInstance(results[0].chain[0], Comp.Plain)
        self.assertTrue(all(len(result.chain) == 1 for result in results[1:]))

    def test_group_and_private_forward_serialize_three_ordered_jpegs_in_one_action(
        self,
    ):
        for private in (False, True):
            with self.subTest(private=private):
                event = Event(private=private)
                original = deepcopy(self.battle)
                with (
                    patch.object(
                        self.plugin,
                        "_battle_card_pages",
                        AsyncMock(return_value=self.pages),
                    ) as render,
                    patch(
                        "main.build_battle_report",
                        side_effect=AssertionError("must not simulate again"),
                    ),
                ):
                    results = self.output(event)
                self.assertEqual(results, [])
                self.assertEqual(len(event.bot.calls), 1)
                action, payload = event.bot.calls[0]
                self.assertEqual(
                    action,
                    "send_private_forward_msg" if private else "send_group_forward_msg",
                )
                self.assertEqual(
                    payload["user_id" if private else "group_id"], "987654"
                )
                self.assertEqual(len(payload["messages"]), 3)
                encoded_images = []
                for node in payload["messages"]:
                    self.assertEqual(node["type"], "node")
                    self.assertEqual(node["data"]["user_id"], "123456")
                    content = node["data"]["content"]
                    self.assertEqual(len(content), 1)
                    self.assertEqual(content[0]["type"], "image")
                    encoded_images.append(self.decode_image(content[0]["data"]["file"]))
                self.assertEqual(encoded_images, self.pages)
                render.assert_awaited_once_with(self.battle)
                self.assertEqual(self.battle, original)

    def test_unsupported_platform_reuses_images_without_any_protocol_send(self):
        event = Event(platform="webchat")
        with patch.object(
            self.plugin, "_battle_card_pages", AsyncMock(return_value=self.pages)
        ) as render:
            results = self.output(event)
        self.assert_same_regular_images(results)
        self.assertIn("当前平台不支持图片合并转发", results[0].chain[0].text)
        self.assertEqual(event.bot.calls, [])
        render.assert_awaited_once_with(self.battle)

    def test_onebot_failure_reuses_images_and_does_not_render_or_simulate_again(self):
        event = Event(fail=True)
        with (
            patch.object(
                self.plugin, "_battle_card_pages", AsyncMock(return_value=self.pages)
            ) as render,
            patch(
                "main.build_battle_report",
                side_effect=AssertionError("must not simulate again"),
            ),
        ):
            results = self.output(event)
        self.assert_same_regular_images(results)
        self.assertIn("图片合并转发暂时无法发送", results[0].chain[0].text)
        self.assertEqual(len(event.bot.calls), 1)
        render.assert_awaited_once_with(self.battle)

    def test_render_failure_preserves_all_rounds_in_real_plain_components(self):
        self.battle["result"]["turns"] = 25
        self.battle["result"]["turn_log"] = [
            [f"第{turn}回合：", f"ROUND_MARK_{turn:02d} 伤害与状态变化。"]
            for turn in range(1, 26)
        ]
        original = deepcopy(self.battle)
        event = Event()
        with patch.object(
            self.plugin,
            "_battle_card_pages",
            AsyncMock(side_effect=RuntimeError("isolated render failure")),
        ) as render:
            results = self.output(event)
        self.assertTrue(
            all(
                isinstance(component, Comp.Plain)
                for result in results
                for component in result.chain
            )
        )
        text = "\n".join(
            component.text for result in results for component in result.chain
        )
        self.assertTrue(
            text.endswith(format_battle_report_data({**self.battle, "detailed": True}))
        )
        for turn in range(1, 26):
            self.assertIn(f"ROUND_MARK_{turn:02d}", text)
        self.assertEqual(event.bot.calls, [])
        self.assertEqual(self.battle, original)
        render.assert_awaited_once_with(self.battle)


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "astrbot_version": getattr(astrbot, "__version__", VERSION),
                "astrbot_source": getattr(astrbot, "__file__", None),
                "message_components_source": inspect.getsourcefile(Comp.Image),
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    unittest.main(verbosity=2)
