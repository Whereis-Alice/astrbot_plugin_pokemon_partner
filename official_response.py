"""Adapt this plugin's public replies without decorating other plugins' output."""
from __future__ import annotations

from functools import wraps
import logging

try:
    from .qq_official import is_qq_official, adapt_results, _definite_rejection
except ImportError:
    from qq_official import is_qq_official, adapt_results, _definite_rejection

log = logging.getLogger(__name__)


def _keyboard_kind(name):
    if 'history' in name:
        return 'history'
    if 'ranking' in name:
        return 'ranking'
    if 'profile' in name:
        return 'profile'
    if 'odds' in name:
        return 'odds'
    if 'challenge' in name or 'accept' in name or 'reject' in name or 'cancel' in name:
        return 'challenge'
    if 'draw' in name:
        return 'draw'
    if 'collection' in name:
        return 'collection'
    if 'exchange' in name:
        return 'exchange'
    if 'battle' in name or 'duel' in name:
        return 'battle'
    return 'menu'


def official_response(handler):
    """Preserve AstrBot's handler signature, permission filters and generator."""
    @wraps(handler)
    async def wrapped(self, event, *args, **kwargs):
        if not is_qq_official(event):
            async for result in handler(self, event, *args, **kwargs):
                yield result
            return
        results = [result async for result in handler(self, event, *args, **kwargs)]
        if not results:
            return
        config = dict(getattr(self, 'config', {}) or {})
        if not config.get('qq_official_command_prefix'):
            try:
                cfg = self.context.get_config(event.unified_msg_origin)
                prefixes = cfg.get('wake_prefix', ['/'])
                config['qq_official_command_prefix'] = str(prefixes[0]) if prefixes else ''
            except (AttributeError, TypeError, KeyError):
                config['qq_official_command_prefix'] = '/'
        if handler.__name__.startswith(('llm_', 'tool_')):
            for result in results:
                if isinstance(result, str):
                    yield result
            results = [result for result in results if not isinstance(result, str)]
            if not results:
                return
        try:
            sent = await adapt_results(event, results, config, keyboard_kind=_keyboard_kind(handler.__name__))
        except Exception as exc:
            # The adapter raises only when its first ordinary send was
            # explicitly rejected. It consumes all uncertain/partial sends.
            # Keep the original committed result for AstrBot's normal sender.
            if not _definite_rejection(exc):
                raise
            log.warning('QQ first reply rejected (%s); retaining original result', type(exc).__name__)
            sent = False
        if not sent:
            for result in results:
                yield result
    return wrapped
