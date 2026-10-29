"""WebUI member mutations against an isolated persistent store."""

import asyncio
import contextlib
import io
import json
import random
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import tests

with contextlib.redirect_stdout(io.StringIO()):
    tests.test_main_imports()

from gacha import GachaRules, Partner
from gacha_store import GachaStore, today_key
from webui import PokemonPartnerWebUI


class SettingsActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="webui-settings-")
        self.addCleanup(self.temp.cleanup)
        self.config_path = Path(self.temp.name) / "config.json"

        class Config(dict):
            def save_config(config):
                self.config_path.write_text(json.dumps(config), encoding="utf-8")

        self.config = Config(battle_output_mode="card", daily_draw_limit=3)
        self.config.save_config()
        self.plugin = SimpleNamespace(
            config=self.config,
            partner_service=object(),
            _init_partners=lambda: None,
        )
        self.ui = PokemonPartnerWebUI(self.plugin)

    def save(self, value):
        with patch.object(
            self.ui, "_body", AsyncMock(return_value={"config": {"battle_output_mode": value}})
        ):
            return asyncio.run(self.ui.api_settings_save())

    def test_all_battle_modes_persist_and_are_read_back_by_settings(self):
        for mode in ("card_forward", "card", "forward", "text"):
            with self.subTest(mode=mode):
                self.assertEqual(self.save(mode), {"ok": True})
                persisted = json.loads(self.config_path.read_text(encoding="utf-8"))
                self.assertEqual(persisted["battle_output_mode"], mode)
                self.assertEqual(persisted["daily_draw_limit"], 3)
                result = asyncio.run(self.ui.api_settings())
                self.assertEqual(result["config"]["battle_output_mode"], mode)

    def test_invalid_battle_modes_fail_before_mutating_saved_settings(self):
        before = self.config_path.read_bytes()
        for invalid in ("invalid", "图片转发", True, 1):
            with self.subTest(value=invalid):
                result = self.save(invalid)
                self.assertFalse(result["ok"])
                self.assertEqual(self.config["battle_output_mode"], "card")
                self.assertEqual(self.config_path.read_bytes(), before)

    def test_partial_nested_setting_preserves_other_custom_values(self):
        original = {"C": 70, "R": 20, "SR": 8, "SSR": 2}
        self.config["rarity_weights"] = original
        self.config.save_config()
        with patch.object(self.ui, "_body", AsyncMock(return_value={"config": {"rarity_weights": {"SSR": 5}}})):
            result = asyncio.run(self.ui.api_settings_save())
        self.assertEqual(result, {"ok": True})
        expected = {"C": 70, "R": 20, "SR": 8, "SSR": 5}
        self.assertEqual(self.config["rarity_weights"], expected)
        self.assertEqual(json.loads(self.config_path.read_text())["rarity_weights"], expected)
        self.assertEqual(original, {"C": 70, "R": 20, "SR": 8, "SSR": 2})

    def test_invalid_nested_patch_and_failed_reload_keep_original_config(self):
        original = {"C": 70, "R": 20, "SR": 8, "SSR": 2}
        self.config["rarity_weights"] = original
        self.config.save_config()
        before = self.config_path.read_bytes()
        for values in ({"unknown": 5}, {"SSR": True}):
            with self.subTest(values=values), patch.object(self.ui, "_body", AsyncMock(return_value={"config": {"rarity_weights": values}})):
                result = asyncio.run(self.ui.api_settings_save())
                self.assertFalse(result["ok"])
                self.assertEqual(self.config_path.read_bytes(), before)
        self.plugin._init_partners = lambda: (_ for _ in ()).throw(RuntimeError("模拟重载失败"))
        with patch.object(self.ui, "_body", AsyncMock(return_value={"config": {"rarity_weights": {"SSR": 5}}})):
            result = asyncio.run(self.ui.api_settings_save())
        self.assertFalse(result["ok"])
        self.assertEqual(self.config["rarity_weights"], original)
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_settings_read_waits_for_an_in_flight_save(self):
        cleared, release = threading.Event(), threading.Event()
        original_clear = self.config.clear

        def paused_clear():
            original_clear()
            cleared.set()
            release.wait(3)

        self.config.clear = paused_clear

        async def concurrent_read():
            save = asyncio.create_task(self.ui.api_settings_save())
            self.assertTrue(await asyncio.to_thread(cleared.wait, 2))
            read = asyncio.create_task(self.ui.api_settings())
            try:
                await asyncio.sleep(.02)
                self.assertFalse(read.done(), "读取不应看到 clear/update 之间的空配置")
            finally:
                release.set()
                saved, loaded = await asyncio.gather(save, read)
            self.assertEqual(saved, {"ok": True})
            self.assertEqual(loaded["config"], {"battle_output_mode": "text", "daily_draw_limit": 3})

        with patch.object(self.ui, "_body", AsyncMock(return_value={"config": {"battle_output_mode": "text"}})):
            asyncio.run(concurrent_read())


class MemberActionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="webui-actions-")
        self.addCleanup(self.temp.cleanup)
        self.partners = (Partner("25", 25, "0025", "皮卡丘", "电", 320, "C", {"name_zh": "皮卡丘"}),)
        self.rules = GachaRules.from_config({})
        self.store = self.reload_store()
        with self.store._transaction():
            for scope in ("group:a", "group:b"):
                for user in ("alice", "bob"):
                    profile = self.store._profile(scope, user, user)
                    profile.update(owned={"25": {"normal": 1, "shiny": 0}}, dust=100, counts={today_key(): 2})
            self.store._save()
        self.ui = PokemonPartnerWebUI(SimpleNamespace(partner_service=SimpleNamespace(store=self.store)))

    def reload_store(self):
        return GachaStore(Path(self.temp.name), self.partners, self.rules, random.Random(1))

    def call(self, handler, body):
        with patch.object(self.ui, "_body", AsyncMock(return_value=body)):
            return asyncio.run(handler())

    def test_delete_member_preserves_every_other_profile_after_reloading(self):
        before = self.store.snapshot()
        result = self.call(self.ui.api_session_delete, {"scope": "group:a", "user_id": "alice"})
        self.assertEqual(result, {"ok": True, "removed": 1})
        after = self.reload_store().snapshot()
        self.assertNotIn("alice", after["scopes"]["group:a"]["users"])
        self.assertEqual(after["scopes"]["group:a"]["users"]["bob"], before["scopes"]["group:a"]["users"]["bob"])
        self.assertEqual(after["scopes"]["group:b"], before["scopes"]["group:b"])

    def test_deleting_missing_member_is_idempotent(self):
        before = self.store.snapshot()
        self.assertEqual(self.call(self.ui.api_session_delete, {"scope": "group:a", "user_id": "unknown"}), {"ok": True, "removed": 0})
        self.assertEqual(self.reload_store().snapshot(), before)

    def test_missing_scope_never_mutates_data_and_write_errors_reach_ui(self):
        before = self.store.snapshot()
        self.assertFalse(self.call(self.ui.api_session_delete, {"user_id": "alice"})["ok"])
        with patch.object(self.store, "delete_scope", side_effect=OSError("模拟磁盘错误")):
            result = self.call(self.ui.api_session_delete, {"scope": "group:a", "user_id": "alice"})
        self.assertFalse(result["ok"])
        self.assertIn("模拟磁盘错误", result["error"])
        self.assertEqual(self.reload_store().snapshot(), before)

    def test_reset_scope_changes_only_draw_count_and_retains_collections(self):
        before = self.store.snapshot()
        result = self.call(self.ui.api_session_reset, {"scope": "group:a"})
        self.assertEqual(result, {"ok": True, "changed": 2})
        after = self.reload_store().snapshot()
        self.assertEqual(after["scopes"]["group:b"], before["scopes"]["group:b"])
        for user, profile in after["scopes"]["group:a"]["users"].items():
            self.assertNotIn(today_key(), profile["counts"])
            expected = before["scopes"]["group:a"]["users"][user]
            self.assertEqual(profile["owned"], expected["owned"])
            self.assertEqual(profile["dust"], expected["dust"])

    def test_invalid_actions_and_amounts_never_change_member_data(self):
        before = self.store.snapshot()
        requests = [
            {"field": "dust", "action": "unknown", "value": 5},
            *({"field": "dust", "value": value} for value in (True, False, 1.5, -1, None)),
            *({"action": "add_bonus", "value": value} for value in (True, 0, -1, 101)),
            {"field": "pity_ssr", "value": 2.5},
            {"field": "nickname", "value": {"name": "昵称"}},
        ]
        for values in requests:
            with self.subTest(values=values):
                result = self.call(self.ui.api_member_update, {"scope": "group:a", "user_id": "alice", **values})
                self.assertFalse(result["ok"])
                self.assertEqual(self.reload_store().snapshot(), before)

    def test_valid_integer_updates_keep_other_members_and_persist(self):
        before = self.store.snapshot()
        result = self.call(self.ui.api_member_update, {"scope": "group:a", "user_id": "alice", "field": "dust", "value": 0})
        self.assertEqual(result, {"ok": True, "result": {"dust": 0}})
        result = self.call(self.ui.api_member_update, {"scope": "group:a", "user_id": "alice", "action": "add_bonus", "value": 2})
        self.assertEqual(result, {"ok": True, "result": {"bonus_today": 2}})
        after = self.reload_store().snapshot()
        self.assertEqual(after["scopes"]["group:a"]["users"]["alice"]["dust"], 0)
        self.assertEqual(after["scopes"]["group:a"]["users"]["bob"], before["scopes"]["group:a"]["users"]["bob"])
        self.assertEqual(after["scopes"]["group:b"], before["scopes"]["group:b"])

    def test_excluded_form_counts_do_not_hide_members_or_inflate_progress(self):
        with self.store._transaction():
            profiles = self.store.state["scopes"]["group:a"]["users"]
            profiles["alice"]["owned"]["25:excluded"] = {"normal": 1, "shiny": 1}
            profiles["bob"]["owned"] = {"25:excluded": {"normal": 0, "shiny": 1}}
            self.store._save()
        self.ui.plugin.partner_service.pool_counts = lambda: {"C": 1, "R": 0, "SR": 0, "SSR": 0}
        self.ui.plugin.partner_service.effective_weights = self.store.effective_weights
        with patch("webui._query", side_effect=lambda key, default="": {"scope": "group:a"}.get(key, default)):
            members = asyncio.run(self.ui.api_members())
        self.assertTrue(members["ok"])
        self.assertEqual({item["user_id"]: item["collected"] for item in members["items"]}, {"alice": 1, "bob": 0})
        self.assertEqual(sum(item["shiny"] for item in members["items"]), 0)
        sessions = asyncio.run(self.ui.api_sessions())
        group = next(item for item in sessions["items"] if item["scope"] == "group:a")
        self.assertEqual((group["members"], group["collected"]), (2, 1))
        overview = asyncio.run(self.ui.api_overview())
        self.assertTrue(overview["ok"])
        self.assertEqual((overview["stats"]["trainers"], overview["stats"]["collected"], overview["stats"]["shiny"]), (4, 3, 0))
        self.assertIn("25:excluded", self.reload_store().snapshot()["scopes"]["group:a"]["users"]["bob"]["owned"])


if __name__ == "__main__":
    unittest.main()
