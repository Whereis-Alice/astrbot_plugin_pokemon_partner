"""Portable, validated collection backups with explicit identity remapping.

All persistent collections and battle history share GachaStore's state file.
Imports reuse its process lock and atomic commit; no archive paths are extracted
and existing sessions are kept unless replacement is explicitly selected.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import date, datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import uuid

try:
    from .gacha import GachaError
    from .gacha_store import STATE_VERSION
except ImportError:
    from gacha import GachaError
    from gacha_store import STATE_VERSION

FORMAT = 'astrbot_plugin_pokemon_partner.migration'
FORMAT_VERSION = 1
MAX_BYTES = 32 * 1024 * 1024
MAX_INTEGER = 2**63-1
SAFE_CONFIG_KEYS = frozenset({
    'gacha_enabled', 'draws_per_command', 'daily_draw_limit', 'draw_cooldown_seconds',
    'rarity_weights', 'rare_min_bst', 'super_rare_min_bst', 'ultra_rare_min_bst',
    'ssr_pity_after', 'new_pity_after', 'allow_duplicate_draw', 'include_alternate_forms',
    'shiny_chance_percent', 'duplicate_dust', 'spark_costs', 'shiny_dust_multiplier',
    'shiny_spark_multiplier', 'enable_image_card', 'daily_card_style',
    'yugioh_profile_black_bold', 'battle_output_mode', 'wishmaker_style',
    'fallback_web_image', 'pokeapi_enabled', 'pokeapi_timeout',
    'champions_default_format', 'champions_top_n',
})


class MigrationError(GachaError):
    """The backup is invalid or importing it would lose/merge identities."""


def _json_bytes(value) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False,
                         sort_keys=True, separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as exc:
        raise MigrationError('备份包含无法识别的 JSON 数据。') from exc
    if len(raw) > MAX_BYTES:
        raise MigrationError(f'备份文件不能超过 {MAX_BYTES // 1024 // 1024} MB。')
    return raw


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise MigrationError(f'备份含有重复字段：{key}')
        result[key] = value
    return result


def parse_bundle(value) -> dict:
    """Read a JSON upload, rejecting oversized, duplicate and nonfinite data."""
    if isinstance(value, (bytes, bytearray, str)):
        raw = value.encode('utf-8') if isinstance(value, str) else bytes(value)
        if len(raw) > MAX_BYTES:
            raise MigrationError('备份文件超过 32 MB。')
        try:
            value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                parse_constant=lambda constant: (_ for _ in ()).throw(
                    MigrationError(f'备份含有非法数字：{constant}')))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise MigrationError('请选择有效的 JSON 备份文件。') from exc
    if not isinstance(value, Mapping):
        raise MigrationError('备份最外层必须是 JSON 对象。')
    _json_bytes(value)
    _bounded(value)
    return deepcopy(dict(value))


def _bounded(value, depth=0):
    if depth > 24:
        raise MigrationError('备份嵌套层级过深。')
    if isinstance(value, dict):
        if len(value) > 100000:
            raise MigrationError('备份字段数量过多。')
        for key, item in value.items():
            _text(key, '字段名称', 1024, empty=True)
            _bounded(item, depth+1)
    elif isinstance(value, list):
        if len(value) > 100000:
            raise MigrationError('备份列表过长。')
        for item in value:
            _bounded(item, depth+1)
    elif isinstance(value, str):
        if len(value) > 65536 or '\x00' in value:
            raise MigrationError('备份文字过长或含有非法字符。')
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise MigrationError('备份含有非法数字。')
    elif value is not None and not isinstance(value, (int, bool)):
        raise MigrationError('备份含有未知数据类型。')


def _text(value, label, maximum=512, *, empty=False):
    if not isinstance(value, str) or len(value) > maximum or any(ord(c) < 32 for c in value):
        raise MigrationError(f'{label}无效。')
    if not empty and not value.strip():
        raise MigrationError(f'{label}不能为空。')
    return value


def _integer(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INTEGER:
        raise MigrationError(f'{label}必须是非负整数。')
    return value


def _object(value, label):
    if not isinstance(value, dict):
        raise MigrationError(f'{label}必须是 JSON 对象。')
    return value


def _day(value, label, *, empty=False):
    if empty and value == '':
        return
    try:
        if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
            raise ValueError
    except ValueError as exc:
        raise MigrationError(f'{label}必须是 YYYY-MM-DD 日期。') from exc


def _participant(value, label):
    value = _object(value, label)
    _text(value.get('user_id'), label+'用户ID')
    if 'partner_key' in value:
        _text(value['partner_key'], label+'收藏编号')
    if 'nickname' in value:
        _text(value['nickname'], label+'昵称', 512, empty=True)
    if 'shiny' in value and not isinstance(value['shiny'], bool):
        raise MigrationError(label+'闪光标记必须为布尔值。')
    return value


def _timestamp(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_INTEGER:
        raise MigrationError(label+'无效。')


def _validated_state(state):
    state = deepcopy(_object(state, '收藏存档'))
    if type(state.get('version')) is not int or state['version'] != STATE_VERSION:
        raise MigrationError('收藏存档版本不支持，请使用本插件导出的备份。')
    scopes = _object(state.get('scopes'), '会话列表')
    if len(scopes) > 10000:
        raise MigrationError('备份会话数量过多。')
    for scope, data in scopes.items():
        _text(scope, '会话ID')
        data = _object(data, '会话资料')
        users = _object(data.setdefault('users', {}), '成员列表')
        for user_id, profile in users.items():
            _text(user_id, '用户ID')
            profile = _object(profile, '成员资料')
            for name in ('dust', 'pity_ssr', 'pity_new', 'total_draws'):
                _integer(profile.setdefault(name, 0), name)
            if 'nickname' in profile:
                _text(profile['nickname'], '昵称', 512, empty=True)
            _timestamp(profile.get('last_draw_ts', 0), '上次抽取时间')
            for name in ('counts', 'bonus'):
                for day, count in _object(profile.setdefault(name, {}), name).items():
                    _day(day, name+'日期')
                    _integer(count, name+'次数')
            for key, day in _object(profile.setdefault('milestones', {}), '里程碑').items():
                if key != 'all' and (not key.isdecimal() or int(key) < 1):
                    raise MigrationError('里程碑编号无效。')
                _day(day, '里程碑领取日期')
            for key, owned in _object(profile.setdefault('owned', {}), '收藏列表').items():
                _text(key, '收藏编号')
                owned = _object(owned, '收藏个体')
                for name in ('normal', 'shiny'):
                    _integer(owned.get(name, 0), name+'个体数')
                for name in ('first_normal', 'first_shiny'):
                    if name in owned:
                        _day(owned[name], '首次获得日期', empty=True)
        if 'duels' not in data:
            continue
        duels = _object(data['duels'], '对战资料')
        _object(duels.setdefault('pending', {}), '待接收挑战')
        for user_id, stat in _object(duels.setdefault('stats', {}), '对战战绩').items():
            _text(user_id, '战绩用户ID')
            stat = _object(stat, '对战统计')
            for name in ('wins', 'losses', 'draws'):
                _integer(stat.setdefault(name, 0), '对战'+name)
            _integer(stat.setdefault('total', sum(stat[k] for k in ('wins','losses','draws'))), '对战总数')
            if stat['total'] != sum(stat[k] for k in ('wins', 'losses', 'draws')):
                raise MigrationError('对战总数与胜负平局数量不一致。')
            if 'last_at' in stat:
                _timestamp(stat['last_at'], '最近对战时间')
        records = duels.setdefault('records', [])
        if not isinstance(records, list) or len(records) > 10000:
            raise MigrationError('对战记录列表无效或过长。')
        match_ids = set()
        for record in records:
            record = _object(record, '对战记录')
            match_id = _text(record.get('match_id'), '对局ID')
            if match_id in match_ids:
                raise MigrationError('同一会话的对局ID不能重复。')
            match_ids.add(match_id)
            participants = [_participant(record.get(side), side) for side in ('challenger', 'target')]
            if participants[0]['user_id'] == participants[1]['user_id']:
                raise MigrationError('对战记录中的双方不能是同一用户。')
            winner = _text(record.get('winner_id', ''), '胜者ID', empty=True)
            if winner and winner not in {p['user_id'] for p in participants}:
                raise MigrationError('胜者ID不在对战双方中。')
            for name in ('seed', 'level', 'max_turns', 'turns'):
                if name in record:
                    _integer(record[name], '对战'+name)
            if 'turns' in record and 'max_turns' in record and record['turns'] > record['max_turns']:
                raise MigrationError('对战记录回合数超过回合上限。')
            if 'completed_at' in record:
                _timestamp(record['completed_at'], '对战结束时间')
    return state


def _safe_config(config):
    def safe(key):
        return key in SAFE_CONFIG_KEYS or (
            key.startswith('qq_official_') and
            not re.search(r'token|secret|password|credential|cookie|api_key', key, re.I))
    def clean(value):
        if isinstance(value, Mapping):
            return {key: clean(item) for key, item in value.items()
                if isinstance(key, str) and
                not re.search(r'token|secret|password|credential|cookie|api_key|authorization', key, re.I)}
        if isinstance(value, list):
            return [clean(item) for item in value]
        return deepcopy(value)
    return {key: clean(value) for key, value in (config or {}).items()
            if isinstance(key, str) and safe(key)}


def _checksum(state, config):
    return hashlib.sha256(_json_bytes({'state': state, 'config': config})).hexdigest()


def export_bundle(store, duel_service=None, config=None, scope='') -> dict:
    """Export a consistent complete snapshot, excluding credentials and caches."""
    if duel_service is not None and duel_service.store is not store:
        raise MigrationError('对战与收藏必须来自同一个存档。')
    state = store.snapshot()
    if getattr(store, '_load_failed', False):
        raise MigrationError('原存档损坏，不能用空快照覆盖备份。请先保留原文件。')
    if scope:
        if scope not in state['scopes']:
            raise MigrationError('要导出的会话不存在。')
        state['scopes'] = {scope: state['scopes'][scope]}
    settings = _safe_config(config)
    result = {'format': FORMAT, 'format_version': FORMAT_VERSION,
        'exported_at': datetime.now(timezone.utc).isoformat(),
        'scope': scope, 'state': state, 'config': settings,
        'checksum': _checksum(state, settings)}
    _json_bytes(result)
    return result


def _unpack(bundle):
    bundle = parse_bundle(bundle)
    legacy = False
    if bundle.get('format') == FORMAT:
        if type(bundle.get('format_version')) is not int or bundle['format_version'] != FORMAT_VERSION:
            raise MigrationError('迁移包版本不支持。')
        raw_state, config = bundle.get('state'), _object(bundle.get('config', {}), '配置')
        if bundle.get('checksum') != _checksum(raw_state, config):
            raise MigrationError('备份校验失败，文件可能不完整或已被修改。')
    elif 'version' in bundle and 'scopes' in bundle:
        raw_state, config, legacy = bundle, {}, True
    elif 'version' in bundle and 'data' in bundle:
        # Original WebUI /sessions/export response (state version, scope, data).
        version = bundle['version']
        if type(version) is not int or version not in {1, STATE_VERSION}:
            raise MigrationError('旧版会话备份版本不支持。')
        scope = bundle.get('scope', '')
        if scope:
            _text(scope, '会话ID')
            scopes = {scope: bundle['data']}
        else:
            scopes = bundle['data']
        raw_state, config, legacy = {'version': STATE_VERSION, 'scopes': scopes}, {}, True
    else:
        raise MigrationError('这不是宝可梦插件的迁移包或会话备份。')
    return _validated_state(raw_state), _safe_config(config), legacy


def _map_values(value, label):
    value = {} if value is None else dict(_object(dict(value) if isinstance(value, Mapping) else value, label))
    for source, target in value.items():
        _text(source, label+'原ID')
        _text(target, label+'新ID')
    return value


def _remap(state, scope_map, user_map):
    scope_map, user_map = _map_values(scope_map, '会话映射'), _map_values(user_map, '用户映射')
    output = {'version': STATE_VERSION, 'scopes': {}}
    cleared = 0
    for source_scope, data in state['scopes'].items():
        target_scope = scope_map.get(source_scope, source_scope)
        if source_scope not in scope_map and source_scope.startswith('private:'):
            prefix = 'private:qq_official:' if source_scope.startswith('private:qq_official:') else 'private:'
            user = source_scope[len(prefix):]
            target_scope = prefix+user_map.get(user, user)
        if target_scope in output['scopes']:
            raise MigrationError('多个原会话映射到同一个新会话；请分别导入，避免混合群资料。')
        data = deepcopy(data)
        users = {}
        for user_id, profile in data['users'].items():
            target_user = user_map.get(user_id, user_id)
            if target_user in users:
                raise MigrationError('多个原成员映射到同一个新用户；无法判断是否为同一人。')
            users[target_user] = profile
        data['users'] = users
        duels = data.get('duels')
        if duels:
            cleared += len(duels.get('pending', {}))
            duels['pending'] = {}
            stats = {}
            for user_id, stat in duels.get('stats', {}).items():
                target_user = user_map.get(user_id, user_id)
                if target_user in stats:
                    raise MigrationError('对战战绩用户映射冲突。')
                stats[target_user] = stat
            duels['stats'] = stats
            for record in duels.get('records', []):
                for side in ('challenger', 'target'):
                    user = record[side]['user_id']
                    record[side]['user_id'] = user_map.get(user, user)
                user = record.get('winner_id', '')
                record['winner_id'] = user_map.get(user, user)
        output['scopes'][target_scope] = data
    return output, cleared


def _summary(state):
    scopes = state.get('scopes', {})
    profiles = [p for data in scopes.values() for p in data.get('users', {}).values()]
    return {'scopes': len(scopes), 'users': len(profiles),
        'collections': sum(len(p.get('owned', {})) for p in profiles),
        'normal': sum(v.get('normal', 0) for p in profiles for v in p.get('owned', {}).values()),
        'shiny': sum(v.get('shiny', 0) for p in profiles for v in p.get('owned', {}).values()),
        'dust': sum(p.get('dust', 0) for p in profiles),
        'battle_records': sum(len(d.get('duels', {}).get('records', [])) for d in scopes.values())}


def _plan(store, state, bundle, mode, scope_map, user_map):
    if mode not in {'keep_existing', 'replace'}:
        raise MigrationError('导入方式必须为 keep_existing 或 replace。')
    source, config, legacy = _unpack(bundle)
    imported, cleared = _remap(source, scope_map, user_map)
    target = deepcopy(state)
    current = _object(target.get('scopes'), '目标会话列表')
    conflicts, selected, skipped = [], [], []
    for scope, data in imported['scopes'].items():
        if scope in current:
            conflicts.append(scope)
            if mode == 'keep_existing':
                skipped.append(scope)
                continue
        selected.append(scope)
        current[scope] = data
    inactive = sorted({key for data in imported['scopes'].values() for p in data['users'].values()
        for key in p['owned'] if key not in store.by_key})
    report = {'ok': True, 'mode': mode, 'summary': _summary(imported),
        'import_scopes': selected, 'skip_scopes': skipped, 'conflicts': conflicts,
        'cleared_pending_challenges': cleared, 'inactive_partner_keys': inactive,
        'config': config, 'legacy_backup': legacy,
        'warnings': ['旧版备份没有校验和，已按数据结构检查。'] if legacy else []}
    if cleared:
        report['warnings'].append('迁移后不会恢复等待中或进行中的短时挑战；已完成战绩完整保留。')
    if inactive:
        report['warnings'].append('部分收藏不在当前卡池中；原数据已保留，开启对应形态或更新图鉴后可见。')
    return target, report


def preview_import(store, bundle, *, mode='keep_existing', scope_map=None, user_map=None) -> dict:
    """Validate and preview session conflicts without changing the target store."""
    return _plan(store, store.snapshot(), bundle, mode, scope_map, user_map)[1]


def apply_import(store, bundle, *, mode='keep_existing', scope_map=None, user_map=None) -> dict:
    """Recheck under the store lock, back up, and commit one atomic snapshot."""
    with store._transaction():
        if getattr(store, '_load_failed', False):
            raise MigrationError('目标存档损坏，请先处理原文件；导入不会覆盖损坏现场。')
        target, report = _plan(store, store.state, bundle, mode, scope_map, user_map)
        report['backup_path'] = None
        report['imported_scopes'] = len(report['import_scopes'])
        if not report['import_scopes']:
            return report
        backup_dir = store.state_dir/'migration_backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        backup = backup_dir/f'before-import-{stamp}-{uuid.uuid4().hex[:8]}.json'
        raw = _json_bytes({'format': FORMAT, 'format_version': FORMAT_VERSION,
            'exported_at': datetime.now(timezone.utc).isoformat(), 'state': deepcopy(store.state),
            'config': {}, 'checksum': _checksum(store.state, {})})
        # Exclusive names and fsync make the rollback copy durable before commit.
        with backup.open('xb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        store.state = target
        store._save()
        report['backup_path'] = str(backup)
        return report


__all__ = ['MigrationError', 'FORMAT', 'FORMAT_VERSION', 'MAX_BYTES',
           'SAFE_CONFIG_KEYS', 'parse_bundle', 'export_bundle', 'preview_import', 'apply_import']
