"""收藏挑战和事务战绩回归：python owned_duels_tests.py。"""

import random
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from gacha import GachaError, GachaRules, Partner
from gacha_store import GachaStore
from owned_duels import CHALLENGE_TTL, MAX_HISTORY, OwnedDuelService


def partner(key, dex, name):
    names = {25: ("皮卡丘", "Pikachu", "ピカチュウ"), 1: ("妙蛙种子", "Bulbasaur", "フシギダネ"),
             2: ("妙蛙草", "Ivysaur", "フシギソウ")}
    zh, en, ja = names[dex]
    return Partner(key, dex, str(dex).zfill(4), name, "电", 320, "C",
                   {"id": str(dex), "name_zh": zh, "name_en": en, "name_ja": ja, "types": "电",
                    "form_name": name if "form" in key else ""})


PARTNERS = (partner("25-form", 25, "皮卡丘形态"), partner("1", 1, "妙蛙种子"), partner("25", 25, "皮卡丘"),
            partner("2", 2, "妙蛙草"), partner("25-form-b", 25, "皮卡丘形态乙"))


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
        return self.service.challenge("group:a", "alice", "bob", 25, now=1000, **kwargs)

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

    def test_dex_selects_owned_species_and_shiny_requires_ownership(self):
        owned = self.store.owned_partners("group:a", "alice")
        pending = self.challenge(shiny=True)
        self.assertEqual(pending["challenger"]["partner_key"], owned[1].partner.key)
        self.assertEqual(pending["challenger"]["index"], 25)
        self.assertTrue(pending["challenger"]["shiny"])
        self.service.cancel("group:a", "alice", now=1001)
        pending = self.service.challenge("group:a", "alice", "bob", "皮卡丘形态", now=1002)
        self.assertEqual(pending["challenger"]["partner_key"], "25-form")
        self.assertTrue(pending["challenger"]["shiny"], "只有闪光时应自动选择闪光")
        self.service.cancel("group:a", "alice", now=1003)
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "alice", "bob", 1, shiny=True, now=1004)

    def test_full_collection_name_wins_over_shared_form_alias(self):
        # 两个形态共用中文/英语/日语别名，但完整收藏名「皮卡丘」明确指向默认形态。
        pending = self.service.challenge("group:a", "alice", "bob", "皮卡丘", now=1000)
        self.assertEqual((pending["challenger"]["partner_key"], pending["challenger"]["index"]), ("25", 25))
        accepted = self.service.accept("group:a", "bob", "皮卡丘形态", now=1001)
        self.assertEqual(accepted["target"]["partner_key"], "25-form")
        self.assertTrue(accepted["target"]["shiny"])

    def test_owned_language_aliases_work_without_listing_collection_first(self):
        for name in ("Bulbasaur", "bulbasaur", "ＢＵＬＢＡＳＡＵＲ", "フシギダネ"):
            with self.subTest(name=name):
                pending = self.service.challenge("group:a", "alice", "bob", name, now=1000)
                self.assertEqual(pending["challenger"]["partner_key"], "1")
                self.service.cancel("group:a", "alice", now=1001)

    def test_decimal_dex_accepts_fullwidth_padding_and_hash_without_superscripts(self):
        for selection in (25, "25", "0025", "２５", "００２５", "#25", "#0025", "＃２５"):
            with self.subTest(selection=selection):
                pending = self.service.challenge("group:a", "alice", "bob", selection, now=1000)
                self.assertEqual(pending["challenger"]["partner_key"], "25")
                self.assertEqual(pending["challenger"]["index"], 25)
                self.service.cancel("group:a", "alice", now=1001)
        for selection in ("²", "#²", "＃²", "##25", "#25x"):
            with self.subTest(selection=selection), self.assertRaises(GachaError):
                self.service.challenge("group:a", "alice", "bob", selection, now=1002)

    def test_ambiguous_language_alias_gives_dex_and_full_names(self):
        for name in ("Pikachu", "ピカチュウ", "皮卡"):
            with self.subTest(name=name), self.assertRaisesRegex(GachaError, "图鉴编号 25 · 皮卡丘.*图鉴编号 25 · 皮卡丘形态"):
                self.service.challenge("group:a", "alice", "bob", name, now=1000)
        self.assertEqual(self.service.history("group:a"), [])

    def test_sparse_dex_never_falls_back_to_collection_position(self):
        owned = self.store.owned_partners("group:a", "alice")
        self.assertEqual(owned[1].partner.dex, 25)
        before = self.store.snapshot()
        with self.assertRaisesRegex(GachaError, "尚未拥有图鉴编号 2"):
            self.service.challenge("group:a", "alice", "bob", 2, now=1000)
        self.assertEqual(self.store.snapshot(), before)
        pending = self.challenge()
        self.assertEqual(pending["challenger"]["partner_key"], "25")

    def test_new_lower_dex_entries_do_not_change_existing_numeric_selection(self):
        with self.store._transaction():
            self.store._profile("group:a", "alice")["owned"].pop("1")
            self.store._save()
        before = self.challenge()
        self.service.cancel("group:a", "alice", now=1001)
        with self.store._transaction():
            owned = self.store._profile("group:a", "alice")["owned"]
            owned["1"] = {"normal": 1, "shiny": 0}
            owned["2"] = {"normal": 1, "shiny": 0}
            self.store._save()
        after = self.service.challenge("group:a", "alice", "bob", 25, now=1002)
        self.assertEqual(after["challenger"]["partner_key"], before["challenger"]["partner_key"])
        self.assertEqual(after["challenger"]["index"], 25)
        self.assertEqual([item.partner.dex for item in self.store.owned_partners("group:a", "alice")],
                         [1, 2, 25, 25])

    def test_only_owned_form_is_selected_but_multiple_forms_need_full_name(self):
        with self.store._transaction():
            self.store._profile("group:a", "alice")["owned"].pop("25")
            self.store._save()
        pending = self.challenge()
        self.assertEqual(pending["challenger"]["partner_key"], "25-form")
        self.assertTrue(pending["challenger"]["shiny"])
        self.service.cancel("group:a", "alice", now=1001)
        with self.store._transaction():
            self.store._profile("group:a", "alice")["owned"]["25-form-b"] = {"normal": 1, "shiny": 0}
            self.store._save()
        before = self.store.snapshot()
        for shiny in (False, True):
            with self.subTest(shiny=shiny), self.assertRaisesRegex(GachaError, "多个.*完整.*皮卡丘形态.*皮卡丘形态乙"):
                self.service.challenge("group:a", "alice", "bob", 25, shiny=shiny, now=1002)
        self.assertEqual(self.store.snapshot(), before)
        pending = self.service.challenge("group:a", "alice", "bob", "皮卡丘形态乙", now=1003)
        self.assertEqual(pending["challenger"]["partner_key"], "25-form-b")
        self.assertEqual(pending["challenger"]["index"], 25)
        self.assertFalse(pending["challenger"]["shiny"])

    def test_shiny_request_does_not_swap_default_species_for_another_form(self):
        with self.store._transaction():
            self.store._profile("group:a", "alice")["owned"]["25"] = {"normal": 1, "shiny": 0}
            self.store._save()
        with self.assertRaisesRegex(GachaError, "闪光"):
            self.challenge(shiny=True)
        pending = self.challenge()
        self.assertEqual(pending["challenger"]["partner_key"], "25")
        self.assertFalse(pending["challenger"]["shiny"])

    def test_old_pending_index_is_not_reinterpreted_when_accepting(self):
        pending = self.challenge()
        with self.store._transaction():
            stored = self.store.state["scopes"]["group:a"]["duels"]["pending"][pending["match_id"]]
            stored["challenger"]["index"] = 2  # Older snapshots used the collection position.
            old_selection = deepcopy(stored["challenger"])
            self.store._profile("group:a", "alice")["owned"]["2"] = {"normal": 1, "shiny": 0}
            self.store._save()
        restored = OwnedDuelService(self.new_store())
        match = restored.accept("group:a", "bob", 1, now=1001)
        self.assertEqual({key: match["challenger"][key] for key in old_selection}, old_selection)
        self.assertEqual(match["challenger"]["partner_key"], "25")
        self.assertEqual(match["challenger"]["index"], 2)
        record = restored.complete("group:a", match["match_id"], self.result(match), now=1002)
        self.assertEqual(record["challenger"], old_selection)

    def test_old_record_index_is_returned_without_migration(self):
        match = self.start()
        self.service.complete("group:a", match["match_id"], self.result(match), now=1002)
        with self.store._transaction():
            record = self.store.state["scopes"]["group:a"]["duels"]["records"][0]
            record["challenger"]["index"] = 2
            expected = deepcopy(record)
            self.store._save()
        before = self.store.snapshot()
        restored = OwnedDuelService(self.new_store())
        self.assertEqual(restored.history("group:a"), [expected])
        self.assertEqual(restored.profile("group:a", "alice")["wins"], 1)
        self.assertEqual(self.store.snapshot(), before)

    def test_owned_dex_range_follows_the_pool_without_a_generation_cap(self):
        future = Partner("2048", 2048, "2048", "未来伙伴", "一般", 320, "C",
                         {"id": "2048", "name_zh": "未来伙伴", "form_name": ""})
        store = GachaStore(Path(self.temp.name) / "future", (*PARTNERS, future),
                           GachaRules.from_config({}), random.Random(1))
        with store._transaction():
            for user in ("alice", "bob"):
                store._profile("group:future", user)["owned"] = {"2048": {"normal": 1, "shiny": 0}}
            store._save()
        pending = OwnedDuelService(store).challenge("group:future", "alice", "bob", 2048, now=1000)
        self.assertEqual((pending["challenger"]["partner_key"], pending["challenger"]["index"]),
                         ("2048", 2048))

    def test_superscript_number_does_not_match_a_numeric_language_alias(self):
        porygon = Partner("233", 233, "0233", "多边兽Ⅱ", "一般", 320, "C",
                          {"id": "233", "name_zh": "多边兽Ⅱ", "name_en": "Porygon2", "form_name": ""})
        store = GachaStore(Path(self.temp.name) / "numeric-alias", (*PARTNERS, porygon),
                           GachaRules.from_config({}), random.Random(1))
        with store._transaction():
            for user in ("alice", "bob"):
                store._profile("group:numeric", user)["owned"] = {"233": {"normal": 1, "shiny": 0}}
            store._save()
        service = OwnedDuelService(store)
        for selection in ("²", "#²", "＃²"):
            with self.subTest(selection=selection), self.assertRaisesRegex(GachaError, "正整数图鉴编号"):
                service.challenge("group:numeric", "alice", "bob", selection, now=1000)
        pending = service.challenge("group:numeric", "alice", "bob", "Porygon2", now=1000)
        self.assertEqual(pending["challenger"]["partner_key"], "233")

    def test_unique_partial_name_and_unowned_name_or_shiny(self):
        pending = self.service.challenge("group:a", "alice", "bob", "形态", now=1000)
        self.assertEqual(pending["challenger"]["partner_key"], "25-form")
        self.service.cancel("group:a", "alice", now=1001)
        with self.assertRaisesRegex(GachaError, "尚未拥有"):
            self.service.challenge("group:a", "alice", "bob", "火稚鸡", now=1002)
        with self.assertRaisesRegex(GachaError, "闪光"):
            self.service.challenge("group:a", "alice", "bob", "妙蛙种子", shiny=True, now=1002)

    def test_round_limit_is_validated_and_frozen_through_history(self):
        before = self.store.snapshot()
        for limit in (True, 0, 101, -1, "abc", 1.5):
            with self.subTest(limit=limit), self.assertRaises(GachaError):
                self.challenge(max_turns=limit)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(self.challenge(max_turns=20)["max_turns"], 20)
        match = self.service.accept("group:a", "bob", "妙蛙种子", now=1001)
        self.assertEqual(match["max_turns"], 20)
        record = self.service.complete("group:a", match["match_id"], self.result(match), now=1002)
        self.assertEqual(record["max_turns"], 20)
        self.assertEqual(OwnedDuelService(self.new_store()).history("group:a")[0]["max_turns"], 20)

    def test_result_turns_cannot_exceed_agreed_limit_or_change_stats(self):
        self.challenge(max_turns=20)
        match = self.service.accept("group:a", "bob", "妙蛙种子", now=1001)
        for turns in (21, -1, True, "3", 3.0):
            result = {**self.result(match), "turns": turns}
            with self.subTest(turns=turns), self.assertRaisesRegex(GachaError, "回合数"):
                self.service.complete("group:a", match["match_id"], result, now=1002)
            self.assertEqual(self.service.profile("group:a", "alice")["total"], 0)
        self.service.complete("group:a", match["match_id"], {**self.result(match, "draw"), "turns": 20}, now=1003)
        self.assertEqual(self.service.profile("group:a", "alice")["draws"], 1)

    def test_unowned_index_empty_opponent_self_and_private_are_rejected(self):
        for index in (0, -1, 2, 99, 1026, True, "1.5", "abc", ""):
            with self.assertRaises(GachaError):
                self.service.challenge("group:a", "alice", "bob", index, now=1000)
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "alice", "nobody", 1, now=1000)
        with self.assertRaises(GachaError):
            self.service.challenge("group:a", "alice", "alice", 1, now=1000)
        with self.assertRaises(GachaError):
            self.service.challenge("private:alice", "alice", "bob", 1, now=1000)
        self.assertEqual(self.service.history("group:a"), [])

    def test_selection_is_frozen_when_collection_positions_change(self):
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
        self.service.challenge("group:a", "alice", "bob", 25, now=2000)
        self.service.reject("group:a", "bob", now=2001)
        self.service.challenge("group:a", "alice", "bob", 25, now=2002)
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
        match = self.service.accept("group:a", "bob", 25, now=1001)
        self.service.complete("group:a", match["match_id"], self.result(match, "target"), now=1002)
        self.assertEqual(self.service.profile("group:a", "bob")["wins"], 1)
        self.service.challenge("group:a", "alice", "bob", 25, now=2000)
        match = self.service.accept("group:a", "bob", 25, now=2001)
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

    def test_deleting_a_member_releases_only_their_pending_challenge(self):
        self.challenge()
        elsewhere=self.service.challenge("group:b","alice","bob",25,now=1000)
        before_users=self.store.snapshot()["scopes"]["group:a"]["users"]
        self.assertEqual(self.store.delete_scope("group:a","alice"),1)
        snapshot=self.store.snapshot()
        self.assertEqual(snapshot["scopes"]["group:a"]["duels"]["pending"],{})
        self.assertIn(elsewhere["match_id"],snapshot["scopes"]["group:b"]["duels"]["pending"])
        self.assertEqual(snapshot["scopes"]["group:a"]["users"]["bob"],before_users["bob"])
        # The surviving trainer can immediately take another challenge.
        self.service.challenge("group:a","carol","bob",1,now=1001)

    def test_deleting_a_member_aborts_running_reservation_but_keeps_old_history(self):
        first=self.start()
        self.service.complete("group:a",first["match_id"],self.result(first),now=1002)
        history=self.service.history("group:a")
        self.service.challenge("group:a","alice","bob",25,now=2000)
        running=self.service.accept("group:a","bob",1,now=2001)
        self.store.delete_scope("group:a","alice")
        with self.assertRaises(GachaError):
            self.service.complete("group:a",running["match_id"],self.result(running),now=2002)
        self.assertFalse(self.service.abort("group:a",running["match_id"]))
        self.assertNotIn("alice",self.store.snapshot()["scopes"]["group:a"]["users"])
        self.assertEqual(self.service.history("group:a"),history)
        self.assertEqual(self.service.profile("group:a","bob")["total"],1)

    def test_records_are_bounded_and_invalid_results_are_not_counted(self):
        match = self.start()
        with self.assertRaises(GachaError):
            self.service.complete("group:a", match["match_id"], {"winner": "alice", "turns": 1}, now=1002)
        self.assertEqual(self.service.profile("group:a", "alice")["total"], 0)
        self.service.complete("group:a", match["match_id"], self.result(match), now=1003)
        for number in range(MAX_HISTORY + 2):
            timestamp = 2000 + number * 10
            self.service.challenge("group:a", "alice", "bob", 25, now=timestamp)
            match = self.service.accept("group:a", "bob", 1, now=timestamp + 1)
            self.service.complete("group:a", match["match_id"], self.result(match), now=timestamp + 2)
        data = self.store.snapshot()["scopes"]["group:a"]["duels"]
        self.assertEqual(len(data["records"]), MAX_HISTORY)
        self.assertEqual(self.service.profile("group:a", "alice")["wins"], MAX_HISTORY + 3)
        self.assertEqual(len(self.service.history("group:a", limit=999)), 20)


if __name__ == "__main__":
    unittest.main(verbosity=2)
