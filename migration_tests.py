"""Portable collection round trips, identity mapping and atomic import checks."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from gacha import GachaRules, Partner
from gacha_store import GachaStore
from migration import MigrationError, apply_import, export_bundle, parse_bundle, preview_import
from owned_duels import OwnedDuelService

PARTNERS = (
    Partner('1', 1, '0001', '妙蛙种子', '草/毒', 318, 'C', {'id': '1','name_zh':'妙蛙种子'}),
    Partner('25', 25, '0025', '皮卡丘', '电', 320, 'R', {'id':'25','name_zh':'皮卡丘'}),
)


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='partner-migration-')
        self.addCleanup(self.folder.cleanup)
        self.rules = GachaRules.from_config({'draw_cooldown_seconds': 0, 'daily_draw_limit': 50,
            'shiny_chance_percent': 100, 'rarity_weights': {'C':100,'R':0,'SR':0,'SSR':0}})
        self.source, self.target = self.store('old'), self.store('new')
        for user in ('10001', '10002'):
            self.source.draw('group:old', user, nickname='训练家'+user, now=1000, today='2026-11-11')
        self.source.add_bonus('group:old','10001',3, today='2026-11-11')
        self.source.set_dust('group:old', '10001', 128)
        self.source.update_member('group:old','10001','pity_ssr',7)
        self.source.update_member('group:old','10001','pity_new',4)
        duel = OwnedDuelService(self.source)
        duel.challenge('group:old','10001','10002',1,now=2000)
        match = duel.accept('group:old','10002',1,now=2001)
        duel.complete('group:old',match['match_id'],{'setup':[{'name':'甲'},{'name':'乙'}],
            'winner':'甲','turns':3,'reason':'一方倒下','final':{}},now=2002)

    def store(self, name):
        return GachaStore(Path(self.folder.name)/name, PARTNERS, self.rules, random.Random(1))

    def test_full_round_trip_preserves_collection_progress_and_battle_history(self):
        bundle = export_bundle(self.source, OwnedDuelService(self.source), config={'daily_card_style':'yugioh'})
        before = self.source.snapshot()
        preview = preview_import(self.target, json.dumps(bundle))
        self.assertEqual(preview['summary']['shiny'], 2)
        self.assertEqual(preview['summary']['battle_records'], 1)
        self.assertFalse(self.target.state_path.exists(), 'Preview must not save data')
        result = apply_import(self.target, bundle)
        self.assertEqual(self.target.snapshot(), before)
        self.assertEqual(result['imported_scopes'], 1)
        self.assertTrue(Path(result['backup_path']).is_file())
        self.assertEqual(OwnedDuelService(self.target).profile('group:old','10001')['wins'], 1)

    def test_keep_existing_is_idempotent_and_never_adds_duplicate_individuals(self):
        bundle = export_bundle(self.source)
        apply_import(self.target, bundle)
        self.target.set_dust('group:old','10001',999)
        before = self.target.snapshot()
        result = apply_import(self.target, bundle)
        self.assertEqual(result['skip_scopes'], ['group:old'])
        self.assertEqual(result['imported_scopes'], 0)
        self.assertIsNone(result['backup_path'])
        self.assertEqual(self.target.snapshot(), before)

    def test_replace_changes_only_selected_sessions_and_creates_restorable_backup(self):
        self.target.draw('group:old','new_user',today='2026-11-11',now=1000)
        self.target.draw('group:untouched','new_user',today='2026-11-11',now=1000)
        before = self.target.snapshot()
        result = apply_import(self.target, export_bundle(self.source), mode='replace')
        self.assertEqual(self.target.snapshot()['scopes']['group:untouched'], before['scopes']['group:untouched'])
        rollback = parse_bundle(Path(result['backup_path']).read_bytes())
        self.assertEqual(rollback['state'], before)
        apply_import(self.target, rollback, mode='replace')
        self.assertEqual(self.target.snapshot(), before)

    def test_explicit_identity_mapping_also_moves_winner_and_battle_statistics(self):
        result = apply_import(self.target, export_bundle(self.source),
            scope_map={'group:old':'group:qq_official:group-openid'},
            user_map={'10001':'openid-a','10002':'openid-b'})
        data = self.target.snapshot()['scopes']['group:qq_official:group-openid']
        self.assertEqual(set(data['users']), {'openid-a','openid-b'})
        self.assertEqual(set(data['duels']['stats']), {'openid-a','openid-b'})
        self.assertEqual(data['duels']['records'][0]['winner_id'], 'openid-a')
        self.assertEqual(data['duels']['records'][0]['target']['user_id'], 'openid-b')
        self.assertFalse(result['warnings'])

    def test_mapping_requires_unambiguous_distinct_users_and_groups(self):
        with self.assertRaises(MigrationError):
            apply_import(self.target, export_bundle(self.source), user_map={'10001':'openid','10002':'openid'})
        self.source.draw('group:another','10003',today='2026-11-11',now=1000)
        with self.assertRaises(MigrationError):
            apply_import(self.target, export_bundle(self.source),
                scope_map={'group:old':'group:target','group:another':'group:target'})
        self.assertFalse(self.target.state_path.exists())

    def test_private_user_mapping_keeps_platform_prefix_and_never_guesses_public_ids(self):
        for scope in ('private:10001','private:qq_official:10001'):
            self.source.draw(scope,'10001',today='2026-11-11',now=1000)
        apply_import(self.target, export_bundle(self.source), user_map={'10001':'openid-a'})
        scopes = self.target.snapshot()['scopes']
        self.assertIn('group:old', scopes, 'Group IDs require their own explicit mapping')
        self.assertIn('private:openid-a', scopes)
        self.assertIn('private:qq_official:openid-a', scopes)

    def test_pending_challenges_are_not_resurrected_after_migration(self):
        duel = OwnedDuelService(self.source)
        duel.challenge('group:old','10001','10002',1,now=3000)
        result = apply_import(self.target, export_bundle(self.source))
        duels = self.target.snapshot()['scopes']['group:old']['duels']
        self.assertEqual(duels['pending'], {})
        self.assertEqual(len(duels['records']), 1)
        self.assertEqual(result['cleared_pending_challenges'], 1)

    def test_legacy_webui_session_and_all_session_exports_are_supported(self):
        state = self.source.snapshot()
        for name, data in (('group:old',state['scopes']['group:old']), ('',state['scopes'])):
            with self.subTest(scope=name):
                result = preview_import(self.target, {'ok':True,'version':2,'scope':name,'data':data})
                self.assertTrue(result['legacy_backup'])
                self.assertEqual(result['summary']['users'], 2)

    def test_unknown_forms_survive_even_when_the_target_pool_excludes_them(self):
        with self.source._transaction():
            self.source.state['scopes']['group:old']['users']['10001']['owned']['25:超级皮卡丘'] = {'normal':2,'shiny':1}
            self.source._save()
        result = apply_import(self.target, export_bundle(self.source))
        self.assertIn('25:超级皮卡丘', result['inactive_partner_keys'])
        self.assertEqual(self.target.snapshot()['scopes']['group:old']['users']['10001']['owned']['25:超级皮卡丘']['shiny'], 1)

    def test_export_omits_credentials_and_machine_specific_paths(self):
        config = {'daily_card_style':'yugioh','qq_official_mode':'template','qq_official_template_id':'123',
            'qq_official_api_key':'secret','qq_official_app_secret':'secret','qq_official_token':'secret',
            'images_dir':'/old/private/path','champions_db_path':'/old/private/db','other_key':'secret'}
        exported = export_bundle(self.source, config=config)['config']
        self.assertEqual(exported, {'daily_card_style':'yugioh','qq_official_mode':'template','qq_official_template_id':'123'})

    def test_nested_official_options_never_export_credentials(self):
        exported = export_bundle(self.source, config={'qq_official_options':{
            'template_id':'123', 'secret':'hidden', 'headers':{'Authorization':'hidden'}}})['config']
        self.assertEqual(exported, {'qq_official_options':{'template_id':'123','headers':{}}})

    def test_malformed_battle_record_numbers_do_not_enter_the_live_store(self):
        source = self.source.snapshot()
        source['scopes']['group:old']['duels']['records'][0]['turns'] = 'three'
        with self.assertRaises(MigrationError):
            apply_import(self.target, source)
        self.assertFalse(self.target.state_path.exists())

    def test_invalid_backup_versions_checksum_numbers_and_dates_leave_target_unchanged(self):
        self.target.draw('group:new','other',today='2026-11-11',now=1000)
        before = self.target.state_path.read_bytes()
        good = export_bundle(self.source)
        invalid = []
        for mutate in (
            lambda b: b.update(format_version=99),
            lambda b: b['state']['scopes']['group:old']['users']['10001'].update(dust=-1),
            lambda b: b.update(checksum='broken'),
        ):
            bad = deepcopy(good); mutate(bad); invalid.append(bad)
        state = deepcopy(good['state'])
        state['scopes']['group:old']['users']['10001']['counts'] = {'2026-11-99':1}
        invalid.append(state)
        state = deepcopy(good['state']); state['scopes']['group:old']['users']['10001']['dust'] = True
        invalid.append(state)
        for bad in invalid:
            with self.assertRaises(MigrationError):
                apply_import(self.target, bad, mode='replace')
            self.assertEqual(self.target.state_path.read_bytes(), before)

    def test_duplicate_keys_nonfinite_numbers_and_oversize_json_are_rejected(self):
        for bad in ('{"state":{},"state":{}}', '{"value":NaN}', '{"value":Infinity}'):
            with self.assertRaises(MigrationError):
                parse_bundle(bad)
        with patch('migration.MAX_BYTES', 16):
            with self.assertRaises(MigrationError):
                parse_bundle('{"data":"this JSON is too large"}')

    def test_failed_atomic_commit_rolls_back_memory_disk_and_random_state(self):
        self.target.draw('group:original','other',today='2026-11-11',now=1000)
        before = self.target.snapshot()
        raw_before, rng_before = self.target.state_path.read_bytes(), self.target.rng.getstate()
        original_replace = __import__('os').replace

        def fail_commit(source, target):
            if Path(target) == self.target.state_path:
                raise OSError('simulated final commit failure')
            return original_replace(source, target)

        with patch('gacha_store.os.replace', fail_commit):
            with self.assertRaises(OSError):
                apply_import(self.target, export_bundle(self.source), mode='replace')
        self.assertEqual(self.target.state, before)
        self.assertEqual(self.target.state_path.read_bytes(), raw_before)
        self.assertEqual(self.target.rng.getstate(), rng_before)
        backups = list((self.target.state_dir/'migration_backups').glob('*.json'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(parse_bundle(backups[0].read_bytes())['state'], before)

    def test_source_corruption_is_not_exported_as_an_empty_valid_backup(self):
        self.source.state_path.write_text('broken',encoding='utf-8')
        self.source.state_path.with_suffix('.json.bak').write_text('broken',encoding='utf-8')
        with self.assertRaises(MigrationError):
            export_bundle(self.source)

    def test_cli_export_and_preview_real_storage_paths(self):
        script = Path(__file__).parent/'scripts/migrate_data.py'
        output = Path(self.folder.name)/'portable.json'
        export = subprocess.run([sys.executable,str(script),'--state-dir',str(self.source.state_dir),
            'export','--output',str(output)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(export.returncode, 0, export.stderr)
        bundle = parse_bundle(output.read_bytes())
        self.assertEqual(bundle['state'], self.source.snapshot())
        preview = subprocess.run([sys.executable,str(script),'--state-dir',str(self.target.state_dir),
            'preview','--input',str(output)],capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(preview.stdout)['summary']['users'], 2)
        self.assertFalse(self.target.state_path.exists())


if __name__ == '__main__':
    unittest.main()
