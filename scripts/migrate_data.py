"""Export, preview or import Pokémon collection data while AstrBot is stopped.

Example:
  python scripts/migrate_data.py --state-dir /AstrBot/data/plugin_data/astrbot_plugin_pokemon_partner/gacha export --output backup.json
  python scripts/migrate_data.py --state-dir /new/plugin_data/astrbot_plugin_pokemon_partner/gacha preview --input backup.json
  python scripts/migrate_data.py --state-dir /new/plugin_data/astrbot_plugin_pokemon_partner/gacha import --input backup.json

Configuration and identity maps are optional JSON files. Existing sessions stay
unchanged by default; --mode replace explicitly replaces imported sessions.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gacha import GachaRules, build_partners
from gacha_store import GachaStore
from migration import MAX_BYTES, MigrationError, apply_import, export_bundle, parse_bundle, preview_import
from pokedex_db import PokedexDB


def read_json(path):
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise MigrationError('JSON 文件超过 32 MB。')
    return parse_bundle(path.read_bytes())


def main(argv=None):
    parser = argparse.ArgumentParser(description='宝可梦收藏备份与服务器迁移')
    parser.add_argument('--state-dir', required=True, help='包含 state.json 的实际 gacha 数据目录')
    parser.add_argument('--config-file', help='可选：原插件配置 JSON，仅导出非敏感配置')
    commands = parser.add_subparsers(dest='action', required=True)
    export = commands.add_parser('export', help='导出完整可移植 JSON 迁移包')
    export.add_argument('--output', required=True)
    export.add_argument('--scope', default='', help='留空导出全部会话')
    for action in ('preview', 'import'):
        command = commands.add_parser(action, help='检查导入内容' if action == 'preview' else '导入已检查的迁移包')
        command.add_argument('--input', required=True)
        command.add_argument('--mode', choices=('keep_existing', 'replace'), default='keep_existing')
        command.add_argument('--scope-map', help='可选：原会话ID到新会话ID的 JSON 对象文件')
        command.add_argument('--user-map', help='可选：原用户ID到新用户ID的 JSON 对象文件')
    args = parser.parse_args(argv)
    try:
        state_dir = Path(args.state_dir).resolve()
        if args.action == 'export' and not any((state_dir/name).is_file() for name in ('state.json','state.json.bak')):
            raise MigrationError('指定目录没有 state.json 或可用备份，请核对实际存档位置。')
        config = read_json(args.config_file) if args.config_file else {}
        rules = GachaRules.from_config({**config, 'include_alternate_forms': True})
        store = GachaStore(state_dir, build_partners(PokedexDB(), rules), rules)
        if args.action == 'export':
            bundle = export_bundle(store, config=config, scope=args.scope)
            output = Path(args.output).resolve()
            # Never accidentally replace the live state or an existing backup.
            if output in {store.state_path.resolve(), store.state_path.with_suffix('.json.bak').resolve()}:
                raise MigrationError('导出路径不能是正在使用的存档文件。')
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open('x', encoding='utf-8') as handle:
                json.dump(bundle, handle, ensure_ascii=False, indent=2, allow_nan=False)
            result = {'ok': True, 'output': str(output), 'scopes': len(bundle['state']['scopes'])}
        else:
            bundle = read_json(args.input)
            kwargs = dict(mode=args.mode,
                scope_map=read_json(args.scope_map) if args.scope_map else None,
                user_map=read_json(args.user_map) if args.user_map else None)
            action = preview_import if args.action == 'preview' else apply_import
            result = action(store, bundle, **kwargs)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, MigrationError) as exc:
        print(f'迁移失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
