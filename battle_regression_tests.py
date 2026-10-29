"""对战真实规则回归：python battle_regression_tests.py。"""

import random
import unittest
from unittest.mock import patch

from battle_engine import BattleMove, BattlePokemon, BattleSimulator, make_move, move_effect_text
from battle_report import build_battle_pokemon, build_battle_report, format_battle_report, format_battle_report_data, parse_battle_options, parse_battle_max_turns, split_battle_text
from pokedex_db import PokedexDB


def pokemon(name, move, hp=500, speed=50, types=None):
    return BattlePokemon(name, types or ["一般"], {"HP": hp, "攻击": 100, "防御": 100,
                         "特攻": 100, "特防": 100, "速度": speed}, [move])


def move(name, power=40, category="物理", **kwargs):
    return BattleMove(name, "一般", category, power, 100, 20, **kwargs)


class BattleRulesTests(unittest.TestCase):
    def setUp(self):
        self.db = PokedexDB()
        self.sim = BattleSimulator(self.db.type_chart(), random.Random(7), max_turns=3)

    def test_priority_beats_speed(self):
        slow = pokemon("慢", move("先制", power=1000, priority=1), speed=1)
        fast = pokemon("快", move("普通", power=1000), speed=200)
        report = self.sim.simulate(slow, fast)
        self.assertEqual(report["winner"], "慢")
        self.assertIn("慢先手", report["turn_log"][0][0])

    def test_stage_increases_damage_and_caps(self):
        attacker = pokemon("甲", move("撞击"))
        defender = pokemon("乙", move("撞击"))
        self.sim._deal_damage(attacker, defender, attacker.moves[0])
        base_damage = 500 - defender.current_hp
        attacker.stages["攻击"] = 2
        defender.current_hp = 500
        self.sim.rng = random.Random(7)
        self.sim._deal_damage(attacker, defender, attacker.moves[0])
        self.assertGreater(500 - defender.current_hp, base_damage * 1.7)
        sword = make_move(self.db.move_index()["剑舞"])
        for _ in range(10):
            self.sim._apply_changes(sword, attacker, defender)
        self.assertEqual(attacker.stages["攻击"], 6)

    def test_close_combat_drops_both_defenses_after_ko(self):
        combat = make_move(self.db.move_index()["近身战"])
        attacker, defender = pokemon("甲", combat), pokemon("乙", move("撞击"), hp=1)
        self.sim._take_turn(attacker, defender, combat)
        self.assertTrue(defender.fainted)
        self.assertEqual(attacker.stages, {"防御": -1, "特防": -1})

    def test_secondary_status_has_actual_effect(self):
        bolt = make_move(self.db.move_index()["十万伏特"])
        bolt.effect_chance = 1
        attacker, defender = pokemon("甲", bolt), pokemon("乙", move("撞击"))
        self.sim._take_turn(attacker, defender, bolt)
        self.assertEqual(defender.status, "麻痹")
        self.assertEqual(defender.effective_speed, 25)

    def test_poison_immunity_and_toxic_escalation(self):
        toxic = make_move(self.db.move_index()["剧毒"])
        defender = pokemon("钢", move("空", None, "变化"), types=["钢"])
        self.sim._take_turn(pokemon("毒手", toxic), defender, toxic)
        self.assertIsNone(defender.status)
        p1 = pokemon("甲", move("空", None, "变化"))
        p2 = pokemon("乙", move("空", None, "变化"))
        p2.status = "剧毒"
        self.sim.simulate(p1, p2)
        self.assertEqual(p2.current_hp, 500 - (500 // 16 + 1000 // 16 + 1500 // 16))

    def test_protect_blocks_damage_and_recovers_next_turn(self):
        p1 = pokemon("盾", make_move(self.db.move_index()["守住"]))
        p2 = pokemon("攻", move("撞击"))
        self.sim._take_turn(p1, p2, p1.moves[0])
        self.sim._take_turn(p2, p1, p2.moves[0])
        self.assertEqual(p1.current_hp, 500)
        self.assertIn("挡下", "\n".join(self.sim.log))

    def test_heal_and_drain_clamp_hp(self):
        heal = make_move(self.db.move_index()["自我再生"])
        p1, p2 = pokemon("甲", heal), pokemon("乙", move("撞击"))
        p1.current_hp = 490
        self.sim._take_turn(p1, p2, heal)
        self.assertEqual(p1.current_hp, 500)
        drain = make_move(self.db.move_index()["终极吸取"])
        p1.current_hp = 200
        self.sim._take_turn(p1, p2, drain)
        self.assertGreater(p1.current_hp, 200)

    def test_recoil_applies_even_on_knockout(self):
        p1, p2 = pokemon("甲", move("反伤", recoil=1 / 3)), pokemon("乙", move("撞击"), hp=3)
        self.sim._take_turn(p1, p2, p1.moves[0])
        self.assertTrue(p2.fainted)
        self.assertEqual(p1.current_hp, 499)

    def test_struggle_can_cause_double_knockout(self):
        p1 = pokemon("甲", move("耗尽"), hp=8)
        p1.moves[0].pp = 0
        p1.current_hp = 1
        p2 = pokemon("乙", move("撞击"), hp=1)
        p1.stats["速度"] = 200
        report = self.sim.simulate(p1, p2)
        self.assertEqual(report["winner"], "平局")
        self.assertTrue(all(s["fainted"] for s in report["final"].values()))

    def test_last_opponent_knockout_ends_before_residual_status_damage(self):
        for status in ("中毒", "灼伤", "剧毒"):
            with self.subTest(status=status):
                attacker = pokemon("甲", move("撞击"), hp=16, speed=200)
                attacker.current_hp, attacker.status = 1, status
                defender = pokemon("乙", move("撞击"), hp=1, speed=1)
                report = self.sim.simulate(attacker, defender)
                self.assertEqual(report["winner"], "甲")
                self.assertEqual(report["final"]["甲"]["hp"], 1)
                self.assertTrue(report["final"]["乙"]["fainted"])
                self.assertEqual(report["turns"], 1)
                self.assertFalse(any(f"受到{status}伤害" in line for line in report["log"]))
                self.assertIn("乙 倒下了！", report["turn_log"][0])

    def test_immediate_recoil_double_knockout_still_draws(self):
        attacker = pokemon("甲", move("舍身撞击", recoil=1 / 3), hp=16, speed=200)
        attacker.current_hp, attacker.status = 1, "中毒"
        defender = pokemon("乙", move("撞击"), hp=1, speed=1)
        report = self.sim.simulate(attacker, defender)
        self.assertEqual(report["winner"], "平局")
        self.assertTrue(all(item["fainted"] for item in report["final"].values()))
        self.assertTrue(any("反作用力 1 点伤害" in line for line in report["turn_log"][0]))
        self.assertFalse(any("受到中毒伤害" in line for line in report["log"]))

    def test_charge_uses_one_pp_and_recharge_skips_action(self):
        charging = make_move(self.db.move_index()["日光束"])
        p1, p2 = pokemon("甲", charging), pokemon("乙", move("撞击"))
        initial_pp = charging.pp
        self.sim._take_turn(p1, p2, charging)
        self.assertEqual(p2.current_hp, 500)
        self.sim._take_turn(p1, p2, charging)
        self.assertLess(p2.current_hp, 500)
        self.assertEqual(charging.pp, initial_pp - 1)
        recharge = make_move(self.db.move_index()["破坏光线"])
        self.sim._take_turn(p1, p2, recharge)
        hp = p2.current_hp
        self.sim._take_turn(p1, p2, recharge)
        self.assertEqual(p2.current_hp, hp)
        self.assertIn("休息", self.sim.log[-1])

    def test_sleep_freeze_and_flinch_skip_actions(self):
        p1, p2 = pokemon("甲", move("撞击")), pokemon("乙", move("撞击"))
        p1.status, p1.status_turns = "睡眠", 2
        self.sim._take_turn(p1, p2)
        self.assertEqual(p2.current_hp, 500)
        p1.status = "冰冻"
        self.sim.rng = random.Random(0)
        self.sim._take_turn(p1, p2)
        self.assertEqual(p2.current_hp, 500)
        p1.status, p1.flinched = None, True
        self.sim._take_turn(p1, p2)
        self.assertEqual(p2.current_hp, 500)

    def test_immunity_and_same_name_final_are_unambiguous(self):
        p1 = pokemon("相同", move("地面", power=100))
        p1.moves[0].type = "地面"
        p2 = pokemon("相同", move("空", None, "变化"))
        p2.ability = "飘浮"
        report = self.sim.simulate(p1, p2)
        self.assertEqual(len(report["final"]), 2)
        self.assertEqual(p2.current_hp, 500)

    def test_turn_limit_does_not_invent_winner(self):
        p1, p2 = pokemon("甲", move("空", None, "变化")), pokemon("乙", move("空", None, "变化"))
        p2.current_hp = 250
        report = self.sim.simulate(p1, p2)
        self.assertEqual(report["winner"], "平局")
        self.assertIn("回合上限", report["reason"])

    def test_move_registry_does_not_share_mutable_effects(self):
        self.assertIsNone(make_move(self.db.move_index()["勇鸟猛攻"]).status)
        self.assertEqual(make_move(self.db.move_index()["伏特攻击"]).status, "麻痹")
        self.assertEqual(make_move(self.db.move_index()["暗影球"]).changes, (("target", "特防", -1),))

    def test_status_ability_and_powder_immunity(self):
        wave = make_move(self.db.move_index()["电磁波"])
        p1, p2 = pokemon("甲", wave), pokemon("乙", move("撞击"))
        p2.ability = "蓄电"
        self.sim._take_turn(p1, p2, wave)
        self.assertIsNone(p2.status)
        powder = make_move(self.db.move_index()["催眠粉"])
        p2.ability, p2.types = "", ["草"]
        self.sim._take_turn(p1, p2, powder)
        self.assertIsNone(p2.status)

    def test_recharge_is_not_required_on_immunity(self):
        beam = make_move(self.db.move_index()["破坏光线"])
        p1, p2 = pokemon("甲", beam), pokemon("乙", move("撞击"), types=["幽灵"])
        self.sim._take_turn(p1, p2, beam)
        self.assertFalse(p1.resting)

    def test_explosion_miss_still_faints_user(self):
        explosion = make_move(self.db.move_index()["大爆炸"])
        explosion.accuracy = 0
        p1, p2 = pokemon("甲", explosion), pokemon("乙", move("撞击"))
        self.sim._take_turn(p1, p2, explosion)
        self.assertTrue(p1.fainted)
        self.assertEqual(p2.current_hp, 500)

    def test_ai_avoids_immunity_and_useless_full_hp_healing(self):
        immune = move("一般", power=100)
        coverage = move("覆盖", power=40)
        coverage.type = "恶"
        heal = make_move(self.db.move_index()["自我再生"])
        p1 = pokemon("甲", immune)
        p1.moves.extend([coverage, heal])
        p2 = pokemon("乙", move("撞击"), types=["幽灵"])
        self.assertIs(self.sim._select_move(p1, p2), coverage)
        p1.current_hp = 20
        self.assertIs(self.sim._select_move(p1, p2), heal)

    def test_ai_prefers_usable_status_and_stops_reapplying_it(self):
        toxic = make_move(self.db.move_index()["剧毒"])
        hit = move("撞击", power=10)
        p1 = pokemon("甲", toxic)
        p1.moves.append(hit)
        p2 = pokemon("乙", move("撞击"))
        self.assertIs(self.sim._select_move(p1, p2), toxic)
        p2.status = "中毒"
        self.assertIs(self.sim._select_move(p1, p2), hit)

    def test_ai_immune_damage_cannot_apply_secondary_effects(self):
        bolt = make_move(self.db.move_index()["十万伏特"])
        bolt.effect_chance = 1
        p1, p2 = pokemon("甲", bolt), pokemon("乙", move("撞击"), types=["水"])
        p2.ability = "蓄电"
        self.assertEqual(self.sim._move_score(p1, p2, bolt)[0], 0)
        shadow = make_move(self.db.move_index()["暗影球"])
        p2.types, p2.ability = ["一般"], ""
        self.assertEqual(self.sim._move_score(p1, p2, shadow)[0], 0)

    def test_ai_drain_estimate_uses_remaining_target_hp(self):
        drain = move("吸取", power=1000, drain=0.5)
        p1, p2 = pokemon("甲", drain), pokemon("乙", move("撞击"), hp=1)
        p1.current_hp = 1
        score, _reason = self.sim._move_score(p1, p2, drain)
        self.assertLess(score, 5, "目标仅1HP时不能把几百点过量伤害当作回复收益")

    def test_ai_respects_stage_cap_and_actual_attack_category(self):
        sword = make_move(self.db.move_index()["剑舞"])
        hit = move("撞击", power=40)
        p1 = pokemon("甲", sword)
        p1.moves.append(hit)
        p2 = pokemon("乙", move("撞击"))
        p1.stages["攻击"] = 6
        self.assertIs(self.sim._select_move(p1, p2), hit)
        p1.stages["攻击"] = -6
        self.assertGreater(self.sim._move_score(p1, p2, sword)[0], 0)

    def test_toxic_poison_user_bypasses_accuracy_roll(self):
        toxic = make_move(self.db.move_index()["剧毒"])
        toxic.accuracy = 0
        p1, p2 = pokemon("甲", toxic, types=["毒"]), pokemon("乙", move("撞击"))
        self.sim._take_turn(p1, p2, toxic)
        self.assertEqual(p2.status, "剧毒")
        self.assertIn("毒属性使用者必定命中", move_effect_text(toxic))

    def test_heat_moves_thaw_user_and_can_burn_thawed_target(self):
        scald = make_move(self.db.move_index()["热水"])
        scald.effect_chance = 1
        p1, p2 = pokemon("甲", scald), pokemon("乙", move("撞击"))
        p1.status = p2.status = "冰冻"
        self.sim._take_turn(p1, p2, scald)
        self.assertIsNone(p1.status)
        self.assertEqual(p2.status, "灼伤")
        self.assertLess(p2.current_hp, 500)
        self.assertIn("使用前可解除自身冰冻", move_effect_text(scald))

    def test_ai_same_seed_keeps_selection_and_battle_reproducible(self):
        def run():
            p1 = pokemon("甲", move("撞击", power=40))
            p1.moves.append(move("拍击", power=40))
            p2 = pokemon("乙", move("撞击"))
            return BattleSimulator(self.db.type_chart(), random.Random(8), max_turns=3).simulate(p1, p2)
        self.assertEqual(run(), run())
        self.assertIn("选择【", "\n".join(run()["log"]))

    def test_legal_ordinary_moves_and_report_replay(self):
        row1, row2 = self.db.pokemon("皮卡丘"), self.db.pokemon("喷火龙")
        partner = build_battle_pokemon(self.db, row1, 5)
        chosen = [m.name for m in partner.moves]
        self.assertTrue(self.db.validate_moveset("皮卡丘", chosen, 5)["is_valid"])
        self.assertNotIn("真气拳", chosen)
        self.assertTrue(all(not self.db.move_index()[n].get("is_z") for n in chosen))
        first = format_battle_report(self.db, row1, row2, 50, seed=7, detailed=True)
        second = format_battle_report(self.db, row1, row2, 50, seed=7, detailed=True)
        self.assertEqual(first, second)
        self.assertIn("模拟效果", first)
        self.assertIn("第1回合", first)
        self.assertIn("属性×", first)
        self.assertIn("PP", first)
        self.assertEqual(parse_battle_options("对战 A B 50 详细 种子=123"), (True, 123))
        self.assertTrue(all(len(p) <= 1800 for p in split_battle_text(first)))

    def test_shared_snapshot_does_not_resimulate_when_formatting(self):
        row1, row2 = self.db.pokemon("皮卡丘"), self.db.pokemon("喷火龙")
        battle = build_battle_report(self.db, row1, row2, 50, seed=7, detailed=True)
        self.assertEqual(battle["result"]["setup"][0]["stats"]["HP"], 110)
        expected = format_battle_report(self.db, row1, row2, 50, seed=7, detailed=True)
        with patch.object(BattleSimulator, "simulate", side_effect=AssertionError("must reuse snapshot")):
            self.assertEqual(format_battle_report_data(battle), expected)

    def test_forms_keep_their_names_in_setup_process_and_results(self):
        form, ordinary = self.db.pokemon("超级喷火龙Ｘ"), self.db.pokemon("喷火龙")
        battle = build_battle_report(self.db, form, ordinary, 50, seed=7, detailed=True, max_turns=1)
        self.assertEqual([setup["name"] for setup in battle["result"]["setup"]], ["超级喷火龙Ｘ", "喷火龙"])
        self.assertEqual(set(battle["result"]["final"]), {"超级喷火龙Ｘ", "喷火龙"})
        text = format_battle_report_data(battle)
        self.assertTrue(text.startswith("【超级喷火龙Ｘ vs 喷火龙】"))
        self.assertIn("超级喷火龙Ｘ", "\n".join(battle["result"]["turn_log"][0]))
        self.assertNotIn("喷火龙（甲）", text)
        mirror = build_battle_report(self.db, form, form, 50, seed=7, max_turns=1)
        self.assertEqual(set(mirror["result"]["final"]), {"超级喷火龙Ｘ（甲）", "超级喷火龙Ｘ（乙）"})

    def test_mirror_status_logs_keep_the_side_label(self):
        wave = move("状态招", None, "变化", status="麻痹")
        first, second = pokemon("镜像", wave, types=["电"]), pokemon("镜像", move("状态招", None, "变化", status="麻痹"), types=["电"])
        report = BattleSimulator(self.db.type_chart(), random.Random(7), max_turns=1).simulate(first, second)
        immune = [line for line in report["log"] if "免受麻痹" in line]
        self.assertEqual(len(immune), 2)
        self.assertTrue(all("（甲）" in line or "（乙）" in line for line in immune))

    def test_text_split_bounds_long_lines_and_keeps_all_content(self):
        for limit in (1, 40, 1800):
            with self.subTest(limit=limit):
                text = "战报开场\r\n\n" + "招式效果完整保留" * 260 + "\n对战结束\n"
                parts = split_battle_text(text, limit)
                self.assertTrue(all(0 < len(part) <= limit for part in parts))
                self.assertEqual("".join(parts), text)
        with self.assertRaises(ValueError):
            split_battle_text("战报", 0)

    def test_configured_turn_limit_is_enforced_and_disclosed(self):
        row = self.db.pokemon("梦幻")
        report = build_battle_report(self.db, row, row, 50, seed=7, max_turns=1)
        self.assertEqual(report["result"]["turns"], 1)
        self.assertEqual(report["result"]["winner"], "平局")
        self.assertIn("回合上限：1回合", format_battle_report_data(report))
        self.assertEqual(parse_battle_max_turns("对战 梦幻 梦幻 50 回合=20"), 20)
        self.assertEqual(parse_battle_max_turns("对战 梦幻 梦幻 50 20回合"), 20)
        self.assertEqual(parse_battle_max_turns("对战 梦幻 梦幻 80 回合 20"), 20)
        self.assertEqual(parse_battle_max_turns("对战 梦幻 梦幻"), 100)
        for invalid in (0, 101, True, "20"):
            with self.assertRaises(ValueError):
                build_battle_report(self.db, row, row, 50, max_turns=invalid)

    def test_turn_limit_rejects_malformed_or_repeated_parameters(self):
        for suffix in ("回合=abc", "回合=-1", "1.5回合", "回合", "回合=abc 20回合",
                       "回合=20 回合=30", "20回合 30回合"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                parse_battle_max_turns("对战 梦幻 梦幻 50 " + suffix)


if __name__ == "__main__":
    unittest.main(verbosity=2)
