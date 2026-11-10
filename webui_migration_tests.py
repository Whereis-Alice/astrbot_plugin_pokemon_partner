"""Migration Web API checks against isolated persistent collection stores."""
from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
import io
from pathlib import Path
import random
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import tests

with contextlib.redirect_stdout(io.StringIO()):
    tests.test_main_imports()

from gacha import GachaRules, Partner
from gacha_store import GachaStore
from migration import export_bundle, parse_bundle
from webui import PLUGIN_ID, PokemonPartnerWebUI


class MigrationWebAPITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="webui-migration-")
        self.addCleanup(self.temp.cleanup)
        self.partners = (
            Partner("25", 25, "0025", "皮卡丘", "电", 320, "C", {"name_zh": "皮卡丘"}),
        )
        self.rules = GachaRules.from_config({"draw_cooldown_seconds": 0})
        self.source = self.store("source")
        self.target = self.store("target")
        for scope, user in (("group:a", "alice"), ("group:a", "bob"), ("group:b", "bob")):
            self.source.draw(scope, user, today="2026-11-11", now=1000)
        self.source.set_dust("group:a", "alice", 128)
        self.target.draw("group:a", "alice", today="2026-11-11", now=1000)
        self.target.set_dust("group:a", "alice", 999)
        self.target.draw("group:untouched", "charlie", today="2026-11-11", now=1000)
        self.bundle = export_bundle(self.source)
        self.ui = self.webui(self.target)

    def store(self, name):
        return GachaStore(Path(self.temp.name) / name, self.partners, self.rules, random.Random(1))

    def webui(self, store, **kwargs):
        return PokemonPartnerWebUI(SimpleNamespace(
            partner_service=SimpleNamespace(store=store), config={}, **kwargs))

    def call(self, handler, body):
        with patch.object(self.ui, "_body", AsyncMock(return_value=body)):
            return asyncio.run(handler())

    def request(self, **kwargs):
        return {"bundle": self.bundle, **kwargs}

    def assert_no_backups(self):
        self.assertFalse(list((self.target.state_dir / "migration_backups").glob("*.json")))

    def test_import_requires_the_boolean_confirmation_after_preview(self):
        before = self.target.state_path.read_bytes()
        for body in (self.request(), *(self.request(confirmed=value) for value in (False, 1, "true", None))):
            with self.subTest(confirmed=body.get("confirmed")), patch("webui.apply_import") as apply:
                result = self.call(self.ui.api_migration_import, body)
                self.assertFalse(result["ok"])
                self.assertIn("确认", result["error"])
                apply.assert_not_called()
                self.assertEqual(self.target.state_path.read_bytes(), before)
                self.assert_no_backups()

    def test_preview_reports_conflicts_without_mutating_memory_or_disk(self):
        before, raw_before = self.target.snapshot(), self.target.state_path.read_bytes()
        result = self.call(self.ui.api_migration_preview, self.request())
        self.assertTrue(result["ok"])
        self.assertEqual(result["summary"]["users"], 3)
        self.assertEqual(result["import_scopes"], ["group:b"])
        self.assertEqual(result["skip_scopes"], ["group:a"])
        self.assertEqual(self.target.state, before)
        self.assertEqual(self.target.state_path.read_bytes(), raw_before)
        self.assert_no_backups()

    def test_confirmed_import_keeps_existing_sessions_and_creates_a_durable_backup(self):
        before = self.target.snapshot()
        result = self.call(self.ui.api_migration_import, self.request(confirmed=True))
        self.assertTrue(result["ok"])
        self.assertEqual(result["imported_scopes"], 1)
        backup = Path(result["backup_path"])
        self.assertTrue(backup.is_file())
        self.assertEqual(parse_bundle(backup.read_bytes())["state"], before)
        after = self.store("target").snapshot()
        self.assertEqual(after["scopes"]["group:a"], before["scopes"]["group:a"])
        self.assertEqual(after["scopes"]["group:untouched"], before["scopes"]["group:untouched"])
        self.assertEqual(after["scopes"]["group:b"], self.source.snapshot()["scopes"]["group:b"])
        second = self.call(self.ui.api_migration_import, self.request(confirmed=True))
        self.assertTrue(second["ok"])
        self.assertEqual(second["imported_scopes"], 0)
        self.assertIsNone(second["backup_path"])
        self.assertEqual(self.store("target").snapshot(), after)

    def test_replace_and_explicit_identity_mapping_only_change_imported_sessions(self):
        before = self.target.snapshot()
        result = self.call(self.ui.api_migration_import, self.request(
            confirmed=True, mode="replace", scope_map={"group:b": "group:qq_official:new-group"},
            user_map={"alice": "openid-a", "bob": "openid-b"}))
        self.assertTrue(result["ok"])
        after = self.store("target").snapshot()["scopes"]
        self.assertEqual(set(after["group:a"]["users"]), {"openid-a", "openid-b"})
        self.assertEqual(after["group:a"]["users"]["openid-a"]["dust"], 128)
        self.assertEqual(set(after["group:qq_official:new-group"]["users"]), {"openid-b"})
        self.assertNotIn("group:b", after)
        self.assertEqual(after["group:untouched"], before["scopes"]["group:untouched"])

    def test_invalid_bundles_and_mapping_collisions_return_errors_without_writes(self):
        before = self.target.state_path.read_bytes()
        bad_checksum = deepcopy(self.bundle)
        bad_checksum["checksum"] = "corrupted"
        bodies = [
            {}, {"bundle": []}, {"bundle": "{broken"}, {"bundle": bad_checksum},
            self.request(mode="unknown"),
            self.request(scope_map={"group:a": "group:merged", "group:b": "group:merged"}),
            self.request(user_map={"alice": "same-openid", "bob": "same-openid"}),
        ]
        for body in bodies:
            for handler in (self.ui.api_migration_preview, self.ui.api_migration_import):
                with self.subTest(body=body, handler=handler.__name__):
                    result = self.call(handler, {**body, "confirmed": True})
                    self.assertFalse(result["ok"])
                    self.assertTrue(result["error"])
                    self.assertEqual(self.target.state_path.read_bytes(), before)
                    self.assert_no_backups()

    def test_failed_disk_commit_is_reported_and_preserves_live_data(self):
        before, raw_before = self.target.snapshot(), self.target.state_path.read_bytes()
        with patch.object(self.target, "_save", side_effect=OSError("模拟磁盘写入失败")):
            result = self.call(self.ui.api_migration_import, self.request(confirmed=True, mode="replace"))
        self.assertFalse(result["ok"])
        self.assertIn("模拟磁盘写入失败", result["error"])
        self.assertEqual(self.target.state, before)
        self.assertEqual(self.target.state_path.read_bytes(), raw_before)
        backups = list((self.target.state_dir / "migration_backups").glob("*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(parse_bundle(backups[0].read_bytes())["state"], before)

    def test_export_uses_the_selected_scope_and_filters_configuration_credentials(self):
        self.ui.plugin.config = {
            "daily_card_style": "yugioh", "qq_official_template_id": "approved-template",
            "qq_official_app_secret": "hidden-secret", "qq_official_api_key": "hidden-key",
            "qq_official_options": {"token": "hidden-token", "rows": 2},
            "images_dir": "D:/private/old-server",
        }
        with patch("webui._query", return_value="group:a"):
            result = asyncio.run(self.ui.api_migration_export())
        backup = parse_bundle(result)
        self.assertEqual(backup["scope"], "group:a")
        self.assertEqual(set(backup["state"]["scopes"]), {"group:a"})
        self.assertEqual(backup["config"], {"daily_card_style": "yugioh",
            "qq_official_template_id": "approved-template", "qq_official_options": {"rows": 2}})
        with patch("webui._query", return_value="group:missing"):
            error = asyncio.run(self.ui.api_migration_export())
        self.assertFalse(error["ok"])

    def test_migration_routes_use_the_authenticated_dashboard_registration(self):
        register = MagicMock()
        self.ui.plugin.context = SimpleNamespace(register_web_api=register)
        self.ui.register()
        expected = {
            f"/{PLUGIN_ID}/migration/export": (self.ui.api_migration_export, ["GET"]),
            f"/{PLUGIN_ID}/migration/preview": (self.ui.api_migration_preview, ["POST"]),
            f"/{PLUGIN_ID}/migration/import": (self.ui.api_migration_import, ["POST"]),
        }
        registered = {call.args[0]: (call.args[1], call.args[2]) for call in register.call_args_list}
        for route, handler in expected.items():
            self.assertEqual(registered[route], handler)


if __name__ == "__main__":
    unittest.main()
