"""Daily draw routing through AstrBot's real SDK, without external messages or draws."""

import asyncio
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

# AstrBot imports create runtime files; keep them outside the checkout.
_runtime = tempfile.TemporaryDirectory(prefix="partner-command-sdk-")
os.environ.setdefault("ASTRBOT_ROOT", _runtime.name)

import main
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Image, Plain, Reply
from astrbot.core.pipeline.waking_check.stage import WakingCheckStage
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.permission import PermissionType, PermissionTypeFilter
from astrbot.core.star.star import star_map
from astrbot.core.star.star_handler import star_handlers_registry


class Event(AstrMessageEvent):
    def __init__(self, text, *, private=False, components=None, sender="trainer"):
        message = SimpleNamespace(
            type=MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE,
            message=components if components is not None else [Plain(text)],
            sender=SimpleNamespace(user_id=sender, nickname="小智"),
            self_id="bot",
            group_id="" if private else "room",
            raw_message={},
        )
        super().__init__(
            text, message, PlatformMetadata("aiocqhttp", "test", "test"), "room"
        )
        self.sent = []

    async def send(self, message):
        self.sent.append(message)


class DailyDrawCommandTests(unittest.TestCase):
    def setUp(self):
        self.cfg = {
            "wake_prefix": ["/"],
            "admins_id": [],
            "plugin_set": ["*"],
            "platform_settings": {"no_permission_reply": False},
        }
        self.plugin = object.__new__(main.PokemonPartnerPlugin)
        self.plugin.context = SimpleNamespace(get_config=lambda umo: self.cfg)
        self.draw = Mock(return_value=object())
        self.plugin.partner_service = SimpleNamespace(
            store=SimpleNamespace(draw=self.draw)
        )

        async def card(event, outcome, nickname):
            yield event.plain_result("卡片")

        self.plugin._send_draw_card = card
        self.command = star_handlers_registry.get_handler_by_full_name(
            "main_cmd_partner_draw"
        )
        self.bare = star_handlers_registry.get_handler_by_full_name(
            "main_cmd_partner_draw_bare"
        )
        self.command_filter = next(
            f for f in self.command.event_filters if isinstance(f, CommandFilter)
        )
        self.patches = [
            patch.object(
                star_handlers_registry, "_handlers", [self.command, self.bare]
            ),
            patch.object(
                self.command, "event_filters", list(self.command.event_filters)
            ),
            patch.object(self.command, "enabled", True),
            patch.object(star_map["main"], "activated", True),
            patch(
                "astrbot.core.star.session_plugin_manager.sp.get_async",
                new=AsyncMock(return_value={}),
            ),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def dispatch(self, event, *, reverse=False, continue_on_error=False):
        async def run():
            waking = WakingCheckStage()
            await waking.initialize(
                SimpleNamespace(
                    astrbot_config=self.cfg,
                    db_helper=None,
                    astrbot_config_id="isolated-test",
                )
            )
            await waking.process(event)
            active = event.get_extra("activated_handlers", [])
            self.active_names = [handler.handler_name for handler in active]
            results = []
            if not event.is_stopped():
                for handler in reversed(active) if reverse else active:
                    try:
                        results.extend(
                            [
                                result
                                async for result in getattr(
                                    self.plugin, handler.handler_name
                                )(event)
                            ]
                        )
                    except RuntimeError:
                        if not continue_on_error:
                            raise
            return results

        return asyncio.run(run())

    def test_bare_group_command_without_wake_prefix(self):
        event = Event("今日宝可梦")
        results = self.dispatch(event)
        self.assertEqual(self.active_names, ["cmd_partner_draw_bare"])
        self.assertFalse(event.is_at_or_wake_command)
        self.assertTrue(event.is_wake)
        self.assertEqual(len(results), 1)
        self.draw.assert_called_once_with("group:room", "trainer", "小智")

    def test_private_and_empty_prefix_both_handlers_draw_only_once_in_any_order(self):
        for private, prefixes in ((True, ["/"]), (False, ["", "/"])):
            for reverse in (False, True):
                with self.subTest(private=private, prefixes=prefixes, reverse=reverse):
                    self.draw.reset_mock()
                    self.cfg["wake_prefix"] = prefixes
                    results = self.dispatch(
                        Event("今日宝可梦", private=private), reverse=reverse
                    )
                    self.assertEqual(
                        set(self.active_names),
                        {"cmd_partner_draw", "cmd_partner_draw_bare"},
                    )
                    self.assertEqual(len(results), 1)
                    self.draw.assert_called_once()

    def test_prefixed_and_at_bot_commands_still_work(self):
        for event in (
            Event("/今日宝可梦"),
            Event("今日宝可梦", components=[At(qq="bot"), Plain("今日宝可梦")]),
        ):
            with self.subTest(event=event.message_str):
                self.draw.reset_mock()
                self.assertEqual(len(self.dispatch(event)), 1)
                self.draw.assert_called_once()

    def test_surrounding_whitespace_and_multiple_plain_components(self):
        results = self.dispatch(
            Event(" \n今日宝可梦\t", components=[Plain(" \n今日"), Plain("宝可梦\t")])
        )
        self.assertEqual(len(results), 1)
        self.draw.assert_called_once()

    def test_mentions_and_extra_text_do_not_draw_even_with_prefix_or_private_chat(self):
        for text in (
            "我想看看今日宝可梦",
            "今日宝可梦 2",
            "今日宝可梦 帮我抽一下",
            "今日宝可梦\n请抽卡",
            "今日宝可梦！",
        ):
            for private, prefix in ((False, ""), (False, "/"), (True, "")):
                with self.subTest(text=text, private=private, prefix=prefix):
                    self.assertEqual(
                        self.dispatch(Event(prefix + text, private=private)), []
                    )
        self.draw.assert_not_called()

    def test_bare_input_rejects_non_text_components_and_quoted_or_caption_text(self):
        for components in (
            [At(qq="someone"), Plain("今日宝可梦")],
            [Reply(id="old", sender_id="someone", message_str="今日宝可梦")],
            [Image(file="test.png"), Plain("今日宝可梦")],
            [Plain("其他文字")],
            [],
        ):
            with self.subTest(components=components):
                event = Event("今日宝可梦", components=components)
                self.assertEqual(self.dispatch(event), [])
                self.assertFalse(event.is_wake)
                self.assertEqual(self.active_names, [])
        self.draw.assert_not_called()

    def test_bare_input_rejects_bot_self_messages(self):
        event = Event("今日宝可梦", sender="bot")
        self.assertEqual(self.dispatch(event), [])
        self.assertFalse(event.is_wake)
        self.draw.assert_not_called()

    def test_command_disabled_in_console_disables_bare_draw(self):
        self.command.enabled = False
        event = Event("今日宝可梦")
        self.assertEqual(self.dispatch(event), [])
        self.assertFalse(event.is_wake)
        self.assertEqual(self.active_names, [])
        self.draw.assert_not_called()

    def test_command_admin_permission_applies_to_bare_draw(self):
        self.command.event_filters.insert(0, PermissionTypeFilter(PermissionType.ADMIN))
        event = Event("今日宝可梦")
        self.assertEqual(self.dispatch(event), [])
        self.assertFalse(event.is_wake)
        self.assertEqual(self.active_names, [])
        self.draw.assert_not_called()
        self.cfg["admins_id"] = ["trainer"]
        self.assertEqual(len(self.dispatch(Event("今日宝可梦"))), 1)
        self.draw.assert_called_once()

    def test_command_rename_removing_original_name_disables_bare_draw(self):
        with (
            patch.object(self.command_filter, "command_name", "每日抽卡"),
            patch.object(self.command_filter, "alias", set()),
            patch.object(self.command_filter, "_cmpl_cmd_names", None),
        ):
            event = Event("今日宝可梦")
            self.assertEqual(self.dispatch(event), [])
            self.assertFalse(event.is_wake)
            self.assertEqual(self.active_names, [])
        self.draw.assert_not_called()

    def test_global_plugin_and_session_disabling_still_apply(self):
        for mode in ("global", "plugin_set", "session"):
            with self.subTest(mode=mode):
                if mode == "global":
                    with patch.object(star_map["main"], "activated", False):
                        self.assertEqual(self.dispatch(Event("今日宝可梦")), [])
                elif mode == "plugin_set":
                    self.cfg["plugin_set"] = []
                    self.assertEqual(self.dispatch(Event("今日宝可梦")), [])
                    self.cfg["plugin_set"] = ["*"]
                else:
                    session_config = {
                        Event("今日宝可梦").unified_msg_origin: {
                            "disabled_plugins": [main.PLUGIN_ID]
                        }
                    }
                    with patch(
                        "astrbot.core.star.session_plugin_manager.sp.get_async",
                        new=AsyncMock(return_value=session_config),
                    ):
                        self.assertEqual(self.dispatch(Event("今日宝可梦")), [])
        self.draw.assert_not_called()

    def test_failed_draw_and_failed_card_do_not_retry_via_other_handler(self):
        self.draw.side_effect = RuntimeError("isolated draw failure")
        self.assertEqual(len(self.dispatch(Event("今日宝可梦", private=True))), 1)
        self.draw.assert_called_once()
        self.draw.reset_mock(side_effect=True)

        async def failed_card(event, outcome, nickname):
            raise RuntimeError("isolated card failure")
            yield

        self.plugin._send_draw_card = failed_card
        self.assertEqual(
            self.dispatch(Event("今日宝可梦", private=True), continue_on_error=True), []
        )
        self.draw.assert_called_once()

    def test_existing_aliases_keep_their_command_registration_and_behavior(self):
        self.assertEqual(self.command_filter.alias, {"抽取宝可梦", "抽宝可梦"})
        for name in ("抽取宝可梦", "抽宝可梦"):
            self.draw.reset_mock()
            self.assertEqual(len(self.dispatch(Event("/" + name))), 1)
            self.draw.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
