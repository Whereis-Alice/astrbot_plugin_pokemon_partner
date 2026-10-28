"""Challenge parsing regressions without an AstrBot runtime or external messages."""

import unittest

from duel_commands import parse_accept, parse_challenge, round_limit
from gacha import GachaError


class DuelCommandParserTests(unittest.TestCase):
    def test_canonical_ordinal_keeps_trailing_round_separate(self):
        request = parse_challenge("宝可梦挑战 2 @小霞 20", [("bob", "小霞")])
        self.assertEqual((request.selection, request.target_id, request.target_name, request.max_turns),
                         ("2", "bob", "小霞", 20))

    def test_real_mention_before_or_after_name(self):
        for text in ("宝可梦挑战 皮卡丘 @小霞 20回合", "宝可梦挑战 @小霞 皮卡丘 回合=20"):
            with self.subTest(text=text):
                request = parse_challenge(text, [("bob", "小霞")])
                self.assertEqual((request.selection, request.target_id, request.max_turns), ("皮卡丘", "bob", 20))

    def test_explicit_round_spellings(self):
        for option in ("20回合", "回合20", "回合=20", "回合:20", "回合：20", "回合 20", "回合= 20"):
            with self.subTest(option=option):
                self.assertEqual(parse_challenge(f"宝可梦挑战 2 @123 {option}").max_turns, 20)

    def test_default_limit_and_owned_shiny_flag(self):
        request = parse_challenge("宝可梦挑战 皮卡丘 闪光 详细 合并转发 @123")
        self.assertEqual((request.selection, request.shiny, request.max_turns), ("皮卡丘", True, 50))

    def test_quoted_english_names_and_apostrophes(self):
        for name in ('"Mr. Mime"', '“Mr. Mime”', "Mr. Mime", "Farfetch'd"):
            with self.subTest(name=name):
                request = parse_challenge(f"宝可梦挑战 {name} @123 20")
                self.assertEqual(request.selection, name.strip('"“”'))

    def test_attached_at_and_bot_at_are_supported(self):
        request = parse_challenge("@999 宝可梦挑战 皮卡丘@小霞 20", [("999", "bot"), ("bob", "小霞")], bot_id="999")
        self.assertEqual((request.selection, request.target_id, request.max_turns), ("皮卡丘", "bob", 20))

    def test_duplicate_actual_id_with_different_display_names_is_one_target(self):
        request = parse_challenge("宝可梦挑战 2 20", [("bob", ""), ("bob", "小霞")])
        self.assertEqual((request.target_id, request.target_name), ("bob", "小霞"))

    def test_numeric_at_and_encoded_at(self):
        for target in ("@123456", "[CQ:at,qq=123456]", "[At:123456]"):
            with self.subTest(target=target):
                self.assertEqual(parse_challenge(f"宝可梦挑战 2 {target}").target_id, "123456")

    def test_legacy_commands_preserve_numeric_id_first(self):
        for command in ("收藏挑战", "挑战宝可梦"):
            with self.subTest(command=command):
                request = parse_challenge(f"{command} 123456 2 20")
                self.assertEqual((request.target_id, request.selection, request.max_turns), ("123456", "2", 20))

    def test_canonical_requires_target_without_guessing_ordinal_as_user_id(self):
        for text in ("宝可梦挑战 2 20", "宝可梦挑战 皮卡丘 20", "宝可梦挑战 2 @小霞", "宝可梦挑战 2 @all"):
            with self.subTest(text=text), self.assertRaisesRegex(GachaError, "@"):
                parse_challenge(text)

    def test_multiple_targets_are_rejected(self):
        cases = [("宝可梦挑战 2 @123 @456", ()),
                 ("宝可梦挑战 2", (("123", "小智"), ("456", "小霞"))),
                 ("宝可梦挑战 2 @456", (("123", "小智"),))]
        for text, mentions in cases:
            with self.subTest(text=text, mentions=mentions), self.assertRaisesRegex(GachaError, "一位"):
                parse_challenge(text, mentions)

    def test_multiple_round_limits_are_rejected(self):
        for options in ("20 30回合", "20回合 回合=30", "回合 20 30", "20 30"):
            with self.subTest(options=options), self.assertRaisesRegex(GachaError, "一个回合"):
                parse_challenge(f"宝可梦挑战 2 @123 {options}")

    def test_bad_round_values_report_round_error(self):
        for option in ("回合=-1", "回合=abc", "abc回合", "回合=1.5", "回合", "0回合", "101回合", "-1", "1.5", ".5", "1e2"):
            with self.subTest(option=option), self.assertRaisesRegex(GachaError, "回合"):
                parse_challenge(f"宝可梦挑战 2 @123 {option}")

    def test_round_limit_range_and_type(self):
        for value in (1, "20", 100):
            self.assertEqual(round_limit(value), int(value))
        for value in (True, False, None, 0, 101, -1, "abc", "1.5", 20.0, "²"):
            with self.subTest(value=value), self.assertRaises(GachaError):
                round_limit(value)

    def test_accept_aliases_names_indices_and_output_flags(self):
        for command in ("接收挑战", "接受挑战", "接受对战"):
            with self.subTest(command=command):
                self.assertEqual(parse_accept(f"{command} 火稚鸡").selection, "火稚鸡")
                request = parse_accept(f"{command} 1 闪光 完整 卡片")
                self.assertEqual((request.selection, request.shiny), ("1", True))
        self.assertEqual(parse_accept('接收挑战 "Mr. Mime"').selection, "Mr. Mime")

    def test_accept_cannot_change_round_limit_or_omit_selection(self):
        for text in ("接收挑战 1 20", "接收挑战 火稚鸡 20", "接收挑战 1 20回合", "接收挑战 1 回合=abc", "接收挑战 闪光 卡片", "接收挑战 1 -1", "接收挑战 1 1.5", "接收挑战 1 abc回合"):
            with self.subTest(text=text), self.assertRaises(GachaError):
                parse_accept(text)

    def test_image_forward_options_keep_selection_shiny_and_rounds_separate(self):
        for option in ("图片转发", "卡片转发"):
            for selection in ("2", "皮卡丘", '"Mr. Mime"'):
                with self.subTest(option=option, selection=selection):
                    accepted = parse_accept(f"接收挑战 {selection} 闪光 {option}")
                    challenged = parse_challenge(f"宝可梦挑战 {selection} @123 20 {option} 闪光")
                    self.assertEqual(accepted.selection, selection.strip('"'))
                    self.assertTrue(accepted.shiny)
                    self.assertEqual(challenged.selection, accepted.selection)
                    self.assertEqual(challenged.max_turns, 20)
                    self.assertEqual(challenged.target_id, "123")
                    self.assertTrue(challenged.shiny)
            with self.subTest(option=option), self.assertRaises(GachaError):
                parse_accept(f"接收挑战 {option}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
