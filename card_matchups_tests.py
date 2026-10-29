"""Main-series card type groups, including dual types and conditional abilities."""

from __future__ import annotations

import itertools
import unittest
from copy import deepcopy

from card_matchups import TYPE_ORDER, _groups, matchup_groups
from pokedex_db import PokedexDB


class CardMatchupsTests(unittest.TestCase):
    @staticmethod
    def group_map(row, types=None):
        return {key: dict(values) for key, values in matchup_groups(row, types).items()}

    def test_dual_types_keep_quadruple_weakness_and_quarter_resistance(self):
        groups = self.group_map({'types': '火/飞行'})
        self.assertEqual(groups['weak'], {'水': 2.0, '电': 2.0, '岩石': 4.0})
        self.assertEqual(groups['resist']['草'], .25)
        self.assertEqual(groups['resist']['虫'], .25)
        self.assertEqual(groups['immune'], {'地面': 0.0})

    def test_normal_ghost_combines_all_three_type_immunities(self):
        groups = self.group_map({'types': '一般/幽灵'})
        self.assertEqual(groups['immune'], {'一般': 0.0, '格斗': 0.0, '幽灵': 0.0})
        self.assertEqual(groups['weak'], {'恶': 2.0})

    def test_conditional_ability_is_not_printed_as_a_fixed_type_immunity(self):
        groups = self.group_map({'types': '电', 'abilities': '漂浮', 'immune_to': '地面'})
        self.assertEqual(groups['weak']['地面'], 2.0)
        self.assertEqual(groups['immune'], {})

    def test_explicit_types_override_stale_row_fields(self):
        row = {'types': '一般', 'weak_to': '水×20', 'immune_to': '水'}
        self.assertEqual(matchup_groups(row, ['火', '飞行']), matchup_groups({'types': '火/飞行'}))

    def test_unknown_or_missing_types_do_not_invent_matchups(self):
        for types in ('宇宙', '草/宇宙', '', None, 12, ['草', 12], ['火', '水', '草']):
            with self.subTest(types=types):
                self.assertEqual(matchup_groups({'types': types, 'weak_to': '水×2'}),
                                 {'weak': (), 'resist': (), 'immune': ()})

    def test_type_order_and_duplicate_normalization_reuse_the_same_groups(self):
        self.assertEqual(matchup_groups({'types': '飞行／火'}), matchup_groups({'types': '火/飞行'}))
        self.assertEqual(matchup_groups({'types': '草/草'}), matchup_groups({'types': '草'}))
        _groups.cache_clear()
        matchup_groups({'types': '火/飞行'})
        matchup_groups({'types': '飞行/火'})
        self.assertEqual(_groups.cache_info().misses, 1)
        self.assertEqual(_groups.cache_info().hits, 1)

    def test_returned_mapping_and_source_row_are_independent(self):
        row = {'types': '火/飞行', 'abilities': '猛火', 'profile': '测试'}
        before = deepcopy(row)
        expected = matchup_groups(row)
        modified = matchup_groups(row)
        modified['weak'] = ()
        self.assertEqual(matchup_groups(row), expected)
        self.assertEqual(row, before)
        self.assertTrue(all(isinstance(values, tuple) for values in expected.values()))

    def test_all_single_and_dual_types_match_the_actual_type_matrix(self):
        chart = PokedexDB().type_chart()
        combos = itertools.chain(((kind,) for kind in TYPE_ORDER), itertools.combinations(TYPE_ORDER, 2))
        for kinds in combos:
            with self.subTest(types=kinds):
                actual = self.group_map({}, kinds)
                expected = {'weak': {}, 'resist': {}, 'immune': {}}
                for attacker in TYPE_ORDER:
                    multiplier = 1.0
                    for defender in kinds:
                        multiplier *= chart[defender, attacker]
                    if multiplier == 0:
                        expected['immune'][attacker] = multiplier
                    elif multiplier > 1:
                        expected['weak'][attacker] = multiplier
                    elif multiplier < 1:
                        expected['resist'][attacker] = multiplier
                self.assertEqual(actual, expected)
                keys = [set(values) for values in actual.values()]
                self.assertFalse(keys[0] & keys[1] or keys[0] & keys[2] or keys[1] & keys[2])


if __name__ == '__main__':
    unittest.main(verbosity=2)
