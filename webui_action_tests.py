"""WebUI member mutations against an isolated persistent store."""

import asyncio
import contextlib
import io
import random
import tempfile
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


if __name__ == "__main__":
    unittest.main()
