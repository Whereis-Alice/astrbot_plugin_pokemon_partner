"""收藏挑战和事务战绩回归：python owned_duels_tests.py。"""

import random
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from gacha import GachaError, GachaRules, Partner
from gacha_store import GachaStore
from owned_duels import CHALLENGE_TTL, MAX_HISTORY, OwnedDuelService


def partner(key, dex, name):
    return Partner(key, dex, str(dex).zfill(4), name, "电", 320, "C", {"id": str(dex), "name_zh": name, "types": "电"})


PARTNERS = (partner("25-form", 25, "皮卡丘形态"), partner("1", 1, "妙蛙种子"), partner("25", 25, "皮卡丘"))


class OwnedDuelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="owned-duels-")
        self.addCleanup(self.temp.cleanup)
        self.store = self.new_store()
        self.service = OwnedDuelService(self.store)
        for scope in ("group:a", "group:b"):
            with self.store._transaction():
                for user in ("alice", "bob", "carol"):
                    profile = self.store._profile(scope, user, user)
                    profile["owned"] = {
                        "25-form": {"normal": 0, "shiny": 1},
                        "1": {"normal": 1, "shiny": 0},
                        "25": {"normal": 1, "shiny": 1},
                    }
                self.store._save()

    def new_store(self):
        return GachaStore(Path(self.temp.name), PARTNERS, GachaRules.from_config({}), random.Random(1))

    def challenge(self, **kwargs):
        return self.service.challenge("group:a", "alice", "bob", 2, now=1000, **kwargs)

    def start(self):
        self.challenge()
        return self.service.accept("group:a", "bob", 1, now=1001)

    @staticmethod
    def result(match, winner="challenger"):
        labels = [match[side]["name"] for side in ("challenger", "target")]
        if labels[0] == labels[1]:
            labels = [labels[0] + "（甲）", labels[1] + "（乙）"]
        return {"setup": [{"name": name} for name in labels],
                "winner": "平局" if winner == "draw" else labels[0 if winner == "challenger" else 1],
                "turns": 3, "reason": "一方倒下", "final": {labels[0]: {"hp": 20, "max": 110}}}

    def test_index_is_the_exact_collection_sort_and_shiny_requires_ownership(self):
        owned = self.store.owned_partners("group:a", "alice")
        pending = self.challenge(shiny=True)
        self.assertEqual(pending["challenger"]["partner_key"], owned[1].partner.key)
        self.assertTrue(pending["challenger"]["shiny"])
        self.service.cancel("group:a", "alice", now=1001)
        pending = self.service.challenge("group:a", "alice", "bob", 3, now=1002)
        self.assertEqual(pending["challenger"]["partner_key"], "25-form")
        self.assertTrue(pending["challenger"]["shiny"], "只有闪光时应自动选择闪光")
        self.service.cancel("group:a", "alice", now=1003)
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "alice", "bob", 1, shiny=True, now=1004)

    def test_unowned_index_empty_opponent_self_and_private_are_rejected(self):
        for index in (0, -1, 99, True, "1.5", "abc"):
            with self.assertRaises(GachaError):
                self.service.challenge("group:a", "alice", "bob", index, now=1000)
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "alice", "nobody", 1, now=1000)
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "alice", "alice", 1, now=1000)
        with self.assertRaises(GachaError):
            self.service.challenge("private:alice", "alice", "bob", 1, now=1000)
        self.assertEqual(self.service.history("group:a"), [])

    def test_selection_is_frozen_even_if_new_draw_changes_indices(self):
        pending = self.challenge()
        with self.store._transaction():
            profile = self.store._profile("group:a", "alice")
            profile["owned"].pop("1")
            self.store._save()
        accepted = self.service.accept("group:a", "bob", 1, now=1001)
        self.assertEqual(accepted["challenger"]["partner_key"], pending["challenger"]["partner_key"])

    def test_accept_rechecks_frozen_challenger_and_target_ownership(self):
        self.challenge()
        with self.store._transaction():
            self.store._profile("group:a", "alice")["owned"].pop("25")
            self.store._save()
        with self.assertRaises(GachaError):
            self.service.accept("group:a", "bob", 1, now=1001)
        self.assertEqual(self.service.profile("group:a", "alice")["total"], 0)
        self.service.cancel("group:a", "alice", now=1002)
        self.service.challenge("group:a", "alice", "bob", 1, now=1003)
        with self.assertRaises(GachaError):
            self.service.accept("group:a", "bob", 1, shiny=True, now=1004)

    def test_one_active_match_per_participant_and_scope_isolation(self):
        self.challenge()
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "carol", "alice", 1, now=1001)
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "bob", "carol", 1, now=1001)
        self.service.challenge("group:b", "alice", "bob", 1, now=1001)
        with self.assertRaises(GachaError):
            self.service.accept("group:a", "carol", 1, now=1002)

    def test_expiry_reject_and_cancel_do_not_change_stats_or_collection(self):
        before = self.store.snapshot()["scopes"]["group:a"]["users"]
        self.challenge()
        with self.assertRaises(GachaError):
            self.service.accept("group:a", "bob", 1, now=1000 + CHALLENGE_TTL)
        self.service.challenge("group:a", "alice", "bob", 2, now=2000)
        self.service.reject("group:a", "bob", now=2001)
        self.service.challenge("group:a", "alice", "bob", 2, now=2002)
        self.service.cancel("group:a", "alice", now=2003)
        self.assertEqual(self.service.profile("group:a", "alice")["total"], 0)
        self.assertEqual(before, self.store.snapshot()["scopes"]["group:a"]["users"])

    def test_cross_instance_concurrent_accept_reserves_only_once(self):
        self.challenge()
        services = [OwnedDuelService(self.new_store()), OwnedDuelService(self.new_store())]
        def accept(service):
            try:
                return service.accept("group:a", "bob", 1, now=1001)
            except GachaError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(accept, services))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(len(self.store.snapshot()["scopes"]["group:a"]["duels"]["pending"]), 1)

    def test_complete_records_both_sides_once_and_persists_across_reload(self):
        match = self.start()
        result = self.result(match)
        record = self.service.complete("group:a", match["match_id"], result, now=1002)
        again = self.service.complete("group:a", match["match_id"], result, now=1003)
        self.assertEqual(record, again)
        restored = OwnedDuelService(self.new_store())
        self.assertEqual(restored.profile("group:a", "alice")["wins"], 1)
        self.assertEqual(restored.profile("group:a", "bob")["losses"], 1)
        self.assertEqual(restored.profile("group:b", "alice")["total"], 0)
        self.assertEqual(len(restored.history("group:a", "alice")), 1)
        self.assertEqual(restored.ranking("group:a")[0]["user_id"], "alice")

    def test_concurrent_complete_is_idempotent_across_instances(self):
        match = self.start()
        services = [OwnedDuelService(self.new_store()), OwnedDuelService(self.new_store())]
        with ThreadPoolExecutor(max_workers=2) as pool:
            records = list(pool.map(lambda service: service.complete(
                "group:a", match["match_id"], self.result(match), now=1002), services))
        self.assertEqual(records[0], records[1])
        self.assertEqual(self.service.profile("group:a", "alice")["wins"], 1)
        self.assertEqual(self.service.profile("group:a", "bob")["losses"], 1)
        self.assertEqual(len(self.service.history("group:a")), 1)

    def test_draw_and_same_species_labels_map_to_distinct_trainers(self):
        self.challenge()
        match = self.service.accept("group:a", "bob", 2, now=1001)
        self.service.complete("group:a", match["match_id"], self.result(match, "target"), now=1002)
        self.assertEqual(self.service.profile("group:a", "bob")["wins"], 1)
        self.service.challenge("group:a", "alice", "bob", 2, now=2000)
        match = self.service.accept("group:a", "bob", 2, now=2001)
        self.service.complete("group:a", match["match_id"], self.result(match, "draw"), now=2002)
        self.assertEqual(self.service.profile("group:a", "alice")["draws"], 1)
        self.assertEqual(self.service.profile("group:a", "bob")["draws"], 1)

    def test_write_failure_rolls_back_both_stats_and_running_reservation(self):
        match = self.start()
        before = self.store.snapshot()
        with patch.object(self.store, "_write_snapshot", side_effect=OSError("full disk")):
            with self.assertRaises(OSError):
                self.service.complete("group:a", match["match_id"], self.result(match), now=1002)
        self.assertEqual(self.store.state, before)
        self.assertEqual(self.store.snapshot(), before)
        self.assertTrue(self.service.abort("group:a", match["match_id"]))
        self.assertEqual(self.service.profile("group:a", "alice")["total"], 0)

    def test_deleted_scope_cannot_be_resurrected_by_old_match(self):
        match = self.start()
        self.store.delete_scope("group:a")
        with self.assertRaises(GachaError):
            self.service.complete("group:a", match["match_id"], self.result(match), now=1002)
        self.assertFalse(self.service.abort("group:a", match["match_id"]))
        self.assertNotIn("group:a", self.store.snapshot()["scopes"])

    def test_records_are_bounded_and_invalid_results_are_not_counted(self):
        match = self.start()
        with self.assertRaises(GachaError):
            self.service.complete("group:a", match["match_id"], {"winner": "alice", "turns": 1}, now=1002)
        self.assertEqual(self.service.profile("group:a", "alice")["total"], 0)
        self.service.complete("group:a", match["match_id"], self.result(match), now=1003)
        for number in range(MAX_HISTORY + 2):
            timestamp = 2000 + number * 10
            self.service.challenge("group:a", "alice", "bob", 2, now=timestamp)
            match = self.service.accept("group:a", "bob", 1, now=timestamp + 1)
            self.service.complete("group:a", match["match_id"], self.result(match), now=timestamp + 2)
        data = self.store.snapshot()["scopes"]["group:a"]["duels"]
        self.assertEqual(len(data["records"]), MAX_HISTORY)
        self.assertEqual(self.service.profile("group:a", "alice")["wins"], MAX_HISTORY + 3)
        self.assertEqual(len(self.service.history("group:a", limit=999)), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
