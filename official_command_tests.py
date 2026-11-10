"""Official QQ routing through the real AstrBot SDK, without platform sends."""
from __future__ import annotations

import ast
import asyncio
import inspect
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

_runtime = tempfile.TemporaryDirectory(prefix='partner-official-sdk-')
os.environ.setdefault('ASTRBOT_ROOT', _runtime.name)

import main
import official_response as response
from astrbot.api.event import AstrMessageEvent
from astrbot.api.message_components import At, Plain
from astrbot.core.platform.message_type import MessageType
from astrbot.core.platform.platform_metadata import PlatformMetadata
from astrbot.core.star.filter.command import CommandFilter
from astrbot.core.star.filter.permission import PermissionTypeFilter
from astrbot.core.star.star_handler import star_handlers_registry
from gacha import GachaRules
from gacha_store import today_key
from official_identity import trainer_code
from owned_duels import OwnedDuelService
from partner_service import PartnerService
from pokedex_db import PokedexDB

SCOPE = 'group:qq_official:GROUP_OPENID_01'


class Event(AstrMessageEvent):
    def __init__(self, text, *, user='USER_OPENID_01', group='GROUP_OPENID_01',
                 components=None, private=False, platform='qq_official', raw=None):
        message = SimpleNamespace(
            type=MessageType.FRIEND_MESSAGE if private else MessageType.GROUP_MESSAGE,
            message=[Plain(text)] if components is None else components,
            sender=SimpleNamespace(user_id=user, nickname=''), self_id='qq_official',
            group_id='' if private else group, raw_message=raw or {}, message_id='TEST_MESSAGE')
        super().__init__(text, message, PlatformMetadata(platform, 'test', 'test'), group)


def text_of(results):
    return '\n'.join(part.text for result in results for part in result.chain
                     if isinstance(part, Plain))


class OfficialCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='partner-official-state-')
        self.addCleanup(self.temp.cleanup)
        self.db = PokedexDB()
        self.plugin = object.__new__(main.PokemonPartnerPlugin)
        self.plugin.db = self.db
        self.plugin.config = {'enable_image_card': False}
        self.plugin.context = SimpleNamespace(get_config=lambda _: {'wake_prefix': ['/']})
        self.plugin.partner_service = PartnerService.create(
            self.db, Path(self.temp.name), GachaRules.from_config({}))
        self.store = self.plugin.partner_service.store
        self.duels = OwnedDuelService(self.store)
        self.transport = AsyncMock(return_value=False)
        transport_patch = patch.object(response, 'adapt_results', self.transport)
        transport_patch.start()
        self.addCleanup(transport_patch.stop)
        partners = {p.dex: p for p in self.store.partners if not p.row.get('form_name')}
        with self.store._transaction():
            for user, dex in (('USER_OPENID_01', 25), ('USER_OPENID_02', 255)):
                profile = self.store._profile(SCOPE, user)
                profile['owned'] = {partners[dex].key: {'normal': 1, 'shiny': 0}}
            self.store._save()

    def run_command(self, name, event, *args):
        async def collect():
            return [result async for result in getattr(self.plugin, name)(event, *args)]
        return asyncio.run(collect())

    def test_official_reset_uses_same_group_scope_as_draw_and_keeps_other_groups(self):
        today = today_key()
        scopes = (SCOPE, 'group:GROUP_OPENID_01', 'group:qq_official:ANOTHER_GROUP')
        with self.store._transaction():
            for scope in scopes:
                self.store._profile(scope, 'USER_OPENID_01')['counts'] = {today: 3}
            self.store._profile(SCOPE, 'USER_OPENID_02')['counts'] = {today: 2}
            self.store._save()
        results = self.run_command('cmd_partner_reset_group', Event('重置本群宝可梦'))
        self.assertIn('2 位成员', text_of(results))
        snapshot = self.store.snapshot()['scopes']
        for profile in snapshot[SCOPE]['users'].values():
            self.assertNotIn(today, profile['counts'])
        for scope in scopes[1:]:
            self.assertEqual(snapshot[scope]['users']['USER_OPENID_01']['counts'][today], 3)

    def test_official_reset_accepts_raw_group_fallback_and_rejects_private(self):
        today = today_key()
        self.store.draw(SCOPE, 'USER_OPENID_01')
        results = self.run_command('cmd_partner_reset_group', Event(
            '重置本群宝可梦', group='', raw={'group_openid': 'GROUP_OPENID_01'}))
        self.assertIn('1 位成员', text_of(results))
        self.assertNotIn(today, self.store.snapshot()['scopes'][SCOPE]['users']['USER_OPENID_01']['counts'])
        private = self.run_command('cmd_partner_reset_group', Event('重置本群宝可梦', private=True))
        self.assertIn('仅支持群聊', text_of(private))

    def test_code_challenge_keeps_dex_selection_and_rounds(self):
        code = trainer_code(SCOPE, 'USER_OPENID_02')
        results = self.run_command('cmd_owned_challenge', Event(f'宝可梦挑战 25 @{code} 20'))
        self.assertIn('图鉴编号 #0025', text_of(results))
        self.assertIn('最多 20 回合', text_of(results))
        self.assertEqual(results[0].chain[0].qq, 'USER_OPENID_02')
        self.assertEqual(len(self.store.snapshot()['scopes'][SCOPE]['duels']['pending']), 1)

    def test_real_at_component_is_retained_when_plain_text_excludes_the_mention(self):
        results = self.run_command('cmd_owned_challenge', Event(
            '宝可梦挑战 皮卡丘20', components=[
                At(qq='qq_official'), Plain('宝可梦挑战 皮卡丘'),
                At(qq='USER_OPENID_02'), Plain('20')]))
        self.assertIn('最多 20 回合', text_of(results))
        self.assertEqual(results[0].chain[0].qq, 'USER_OPENID_02')

    def test_history_resolves_a_registered_trainer_code_and_can_select_oneself(self):
        self.duels.challenge(SCOPE, 'USER_OPENID_01', 'USER_OPENID_02', '25')
        match = self.duels.accept(SCOPE, 'USER_OPENID_02', '255')
        self.duels.complete(SCOPE, match['match_id'], {
            'setup': [{'name': match[side]['name']} for side in ('challenger', 'target')],
            'winner': match['challenger']['name'], 'turns': 3})
        code = trainer_code(SCOPE, 'USER_OPENID_02')
        results = self.run_command('cmd_owned_history', Event(f'对战记录 @{code}'))
        self.assertIn('0胜 / 1负', text_of(results))
        own_code = trainer_code(SCOPE, 'USER_OPENID_01')
        own = self.run_command('cmd_owned_history', Event(f'对战记录 @{own_code}'))
        self.assertIn('1胜 / 0负', text_of(own))

    def test_unknown_history_target_is_reported_instead_of_showing_own_record(self):
        results = self.run_command('cmd_owned_history', Event('对战记录 @T-FFFFFFFF'))
        self.assertIn('没有找到本群', text_of(results))
        self.assertNotIn('的收藏战绩', text_of(results))

    def test_nested_bare_draw_decorators_emit_once_and_do_not_draw_again(self):
        event = Event('今日宝可梦')
        self.transport.return_value = True
        with patch.object(main.asyncio, 'to_thread', wraps=asyncio.to_thread):
            results = self.run_command('cmd_partner_draw_bare', event)
        self.assertEqual(results, [])
        self.assertEqual(self.transport.await_count, 1)
        profile = self.store.snapshot()['scopes'][SCOPE]['users']['USER_OPENID_01']
        self.assertEqual(profile['total_draws'], 1)

    def test_sdk_command_registry_preserves_arguments_aliases_and_admin_filter(self):
        command = star_handlers_registry.get_handler_by_full_name('main_cmd_owned_challenge')
        signature = inspect.signature(command.handler)
        self.assertEqual(list(signature.parameters), ['self', 'event', 'keyword'])
        reset = star_handlers_registry.get_handler_by_full_name('main_cmd_partner_reset_group')
        self.assertTrue(any(isinstance(f, PermissionTypeFilter) for f in reset.event_filters))
        identity = star_handlers_registry.get_handler_by_full_name('main_cmd_trainer_identity')
        command_filter = next(f for f in identity.event_filters if isinstance(f, CommandFilter))
        self.assertIn('我的训练家编号', command_filter.alias)

    def test_first_explicit_delivery_rejection_keeps_committed_draw_without_drawing_again(self):
        from qq_official import QQRequestError
        self.transport.side_effect = QQRequestError(403)
        results = self.run_command('cmd_partner_draw', Event('今日宝可梦'))
        self.assertTrue(results)
        self.assertEqual(self.transport.await_count, 1)
        profile = self.store.snapshot()['scopes'][SCOPE]['users']['USER_OPENID_01']
        self.assertEqual(profile['total_draws'], 1)

    def test_dashboard_validates_official_template_parameters(self):
        from webui import PokemonPartnerWebUI
        config = {'qq_official_mode': 'template'}
        with self.assertRaises(ValueError):
            PokemonPartnerWebUI._validate_settings(config, config)
        config.update(qq_official_markdown_template_id='approved-template',
                      qq_official_markdown_title_key='same', qq_official_markdown_body_key='same')
        with self.assertRaises(ValueError):
            PokemonPartnerWebUI._validate_settings(config, config)
        config['qq_official_markdown_body_key'] = 'body'
        PokemonPartnerWebUI._validate_settings(config, config)

    def test_all_registered_public_commands_have_official_reply_wrapping(self):
        source = ast.parse(Path(main.__file__).read_text(encoding='utf-8-sig'))
        missing = []
        for node in ast.walk(source):
            if isinstance(node, ast.AsyncFunctionDef):
                decorators = [ast.unparse(d) for d in node.decorator_list]
                if any('filter.command' in d or 'filter.regex' in d for d in decorators):
                    if 'official_response' not in decorators:
                        missing.append(node.name)
        self.assertEqual(missing, [])


if __name__ == '__main__':
    unittest.main()
