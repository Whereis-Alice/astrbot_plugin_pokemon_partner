"""QQ official openid scoping and registered-trainer challenge resolution."""
from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from duel_commands import parse_challenge
from gacha import GachaError
import official_identity as identity


def event(*, private=False, user='USER_OPENID_01', group='GROUP_OPENID_01',
          name='', platform='qq_official', raw=None, session=''):
    return SimpleNamespace(
        get_platform_name=lambda: platform,
        get_sender_id=lambda: user,
        get_group_id=lambda: group,
        get_sender_name=lambda: name,
        get_session_id=lambda: session,
        is_private_chat=lambda: private,
        message_obj=SimpleNamespace(raw_message=raw),
    )


class Store:
    def __init__(self, scope='group:qq_official:GROUP_OPENID_01'):
        self.state = {'scopes': {scope: {'users': {
            'USER_OPENID_01': {'nickname': ''},
            'USER_OPENID_02': {'nickname': '小霞'},
        }}, 'group:qq_official:ANOTHER_GROUP': {'users': {
            'ANOTHER_USER': {'nickname': '小霞'},
        }}}}

    def snapshot(self):
        return deepcopy(self.state)


class OfficialIdentityTests(unittest.TestCase):
    def test_group_and_private_use_stable_official_namespaces(self):
        scope, user, name = identity.official_scope(event())
        self.assertEqual(scope, 'group:qq_official:GROUP_OPENID_01')
        self.assertEqual(user, 'USER_OPENID_01')
        self.assertEqual(name, '训练家 '+identity.trainer_code(scope, user))
        scope, user, name = identity.official_scope(event(private=True, name='小智'))
        self.assertEqual(scope, 'private:qq_official:USER_OPENID_01')
        self.assertEqual(name, '小智')

    def test_old_onebot_is_not_rewritten(self):
        self.assertFalse(identity.is_qq_official(event(platform='aiocqhttp')))
        with self.assertRaises(ValueError):
            identity.official_scope(event(platform='aiocqhttp'))

    def test_raw_group_openid_and_channel_fallback_are_supported(self):
        group = event(group='', raw={'group_openid': 'RAW_GROUP'})
        self.assertEqual(identity.official_scope(group)[0], 'group:qq_official:RAW_GROUP')
        channel = event(group='', raw=SimpleNamespace(channel_id='123456'))
        self.assertEqual(identity.official_scope(channel)[0], 'group:qq_official:123456')

    def test_missing_identity_is_rejected_instead_of_merging_unknown_users(self):
        with self.assertRaises(GachaError):
            identity.official_scope(event(user='', raw={}))
        with self.assertRaises(GachaError):
            identity.official_scope(event(group='', raw={}))

    def test_short_code_is_stable_and_scope_specific(self):
        code = identity.trainer_code('group:qq_official:a', 'user')
        self.assertRegex(code, r'^T-[0-9A-F]{8}$')
        self.assertEqual(code, identity.trainer_code('group:qq_official:a', 'user'))
        self.assertNotEqual(code, identity.trainer_code('group:qq_official:b', 'user'))
        self.assertNotEqual(code, identity.trainer_code('group:qq_official:a', 'other-user'))

    def test_trainer_code_resolves_for_existing_challenge_parser(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        store = Store(scope)
        code = identity.trainer_code(scope, 'USER_OPENID_02')
        raw, mentions = identity.resolve_challenge_mentions(
            f'宝可梦挑战 25 @{code.lower()} 20 闪光', store, scope,
            sender_id='USER_OPENID_01')
        parsed = parse_challenge(raw, mentions, bot_id='qq_official')
        self.assertEqual(parsed.target_id, 'USER_OPENID_02')
        self.assertEqual(parsed.target_name, '小霞')
        self.assertEqual(parsed.selection, '25')
        self.assertEqual(parsed.max_turns, 20)
        self.assertTrue(parsed.shiny)

    def test_encoded_real_mention_does_not_leak_brackets_into_selection(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        raw, mentions = identity.resolve_challenge_mentions(
            '宝可梦挑战 皮卡丘 <@!USER_OPENID_02> 20', Store(scope), scope)
        parsed = parse_challenge(raw, mentions, bot_id='qq_official')
        self.assertEqual(parsed.selection, '皮卡丘')
        self.assertEqual(parsed.target_id, 'USER_OPENID_02')

    def test_pseudo_bot_mention_is_ignored(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        raw, mentions = identity.resolve_challenge_mentions(
            '<@qq_official> 宝可梦挑战 25 @USER_OPENID_02', Store(scope), scope)
        self.assertEqual([x[0] for x in mentions], ['USER_OPENID_02'])
        self.assertEqual(parse_challenge(raw, mentions).selection, '25')

    def test_sdk_at_components_survive_removal_from_plain_text(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        raw, mentions = identity.resolve_challenge_mentions(
            '宝可梦挑战 25 20', Store(scope), scope,
            mentions=[('qq_official', ''), ('USER_OPENID_02', 'untrusted nickname')])
        parsed = parse_challenge(raw, mentions, bot_id='qq_official')
        self.assertEqual((parsed.target_id, parsed.target_name, parsed.max_turns),
                         ('USER_OPENID_02', '小霞', 20))

    def test_sdk_at_and_text_code_deduplicate_only_the_same_target(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        code = identity.trainer_code(scope, 'USER_OPENID_02')
        _, mentions = identity.resolve_challenge_mentions(
            f'宝可梦挑战 25 @{code}', Store(scope), scope,
            mentions=[('USER_OPENID_02', '')])
        self.assertEqual(len(mentions), 1)
        with self.assertRaises(GachaError):
            identity.resolve_challenge_mentions(
                f'宝可梦挑战 25 @{code}', Store(scope), scope,
                mentions=[('USER_OPENID_01', '')])

    def test_sdk_at_cannot_select_an_unregistered_or_cross_group_user(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        for target in ('ANOTHER_USER', 'NEW_USER'):
            with self.subTest(target=target), self.assertRaises(GachaError):
                identity.resolve_challenge_mentions('宝可梦挑战 25', Store(scope), scope,
                                                    mentions=[(target, '')])

    def test_unknown_nickname_and_other_group_id_cannot_claim_a_member(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        for value in ('小霞', 'ANOTHER_USER', '12345678'):
            with self.subTest(value=value), self.assertRaises(GachaError):
                identity.resolve_challenge_mentions(f'宝可梦挑战 25 @{value}', Store(scope), scope)

    def test_self_and_multiple_targets_are_rejected(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        with self.assertRaises(GachaError):
            identity.resolve_challenge_mentions('宝可梦挑战 25 @USER_OPENID_01', Store(scope), scope,
                                                sender_id='USER_OPENID_01')
        with self.assertRaises(GachaError):
            identity.resolve_challenge_mentions('宝可梦挑战 25 @USER_OPENID_01 @USER_OPENID_02',
                                                Store(scope), scope)

    def test_code_collision_never_selects_an_arbitrary_member(self):
        scope = 'group:qq_official:GROUP_OPENID_01'
        with patch.object(identity, 'trainer_code', return_value='T-AAAAAAAA'):
            with self.assertRaisesRegex(GachaError, '冲突'):
                identity.resolve_challenge_mentions('宝可梦挑战 25 @T-AAAAAAAA', Store(scope), scope)


if __name__ == '__main__':
    unittest.main()
