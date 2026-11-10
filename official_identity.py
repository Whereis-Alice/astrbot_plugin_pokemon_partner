"""Stable QQ official identities and collection-aware challenge addressing.

Official user/group openids are opaque identifiers, not QQ numbers. A visible
trainer code resolves only within the current collection scope; it never binds
an old numeric QQ identity or searches another group.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import re
from typing import Any

try:
    from .card_text import single_line_text
    from .gacha import GachaError
except ImportError:
    from card_text import single_line_text
    from gacha import GachaError


OFFICIAL_PLATFORMS = frozenset({'qq_official', 'qqofficial'})
_PSEUDO_BOT_IDS = frozenset({'qq_official', 'qqofficial', 'all', 'unknown_selfid'})
_CODE = re.compile(r'T-([0-9A-F]{8})', re.I)
_ENCODED_MENTION = re.compile(r'<@!?([A-Za-z0-9_-]{1,128})>')
_TEXT_MENTION = re.compile(r'@([^\s<>，,；;]+)')


def _get(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _event_text(event: Any, method: str) -> str:
    getter = getattr(event, method, None)
    return str(getter() or '') if callable(getter) else ''


def is_qq_official(event: Any) -> bool:
    """Accept the real SDK platform name and the earlier documented alias."""
    return _event_text(event, 'get_platform_name') in OFFICIAL_PLATFORMS


def official_scope(event: Any) -> tuple[str, str, str]:
    """Build a server-independent official scope while preserving real openids."""
    if not is_qq_official(event):
        raise ValueError('Not a QQ official event')
    message = getattr(event, 'message_obj', None)
    raw = _get(message, 'raw_message')
    private = bool(event.is_private_chat())
    author = _get(raw, 'author')
    user_id = _event_text(event, 'get_sender_id')
    if not user_id:
        user_id = str(_get(author, 'user_openid' if private else 'member_openid')
                      or _get(author, 'id') or '')
    if not user_id or user_id in _PSEUDO_BOT_IDS:
        raise GachaError('没有识别到 QQ 官方机器人用户，请重新发送指令。')
    if private:
        scope = f'private:qq_official:{user_id}'
    else:
        group_id = _event_text(event, 'get_group_id')
        if not group_id:
            group_id = str(_get(raw, 'group_openid') or _get(raw, 'channel_id')
                           or _event_text(event, 'get_session_id') or '')
        if not group_id:
            raise GachaError('没有识别到当前群聊或频道，请在目标会话重新发送指令。')
        scope = f'group:qq_official:{group_id}'
    return scope, user_id, trainer_label(scope, user_id, _event_text(event, 'get_sender_name'))


def trainer_code(scope: str, user_id: str) -> str:
    """Return a stable public code; never use it as the storage identity."""
    if not scope or not user_id:
        raise ValueError('Trainer code requires a scope and user identity')
    identity = f'pokemon-partner-trainer-v1\0{scope}\0{user_id}'.encode('utf-8')
    return 'T-'+hashlib.sha256(identity).hexdigest()[:8].upper()


def trainer_label(scope: str, user_id: str, nickname: str = '') -> str:
    """Official QQ group/C2C events omit nicknames; show a useful trainer label."""
    return single_line_text(nickname)[:48] or f'训练家 {trainer_code(scope, user_id)}'


def _users(store: Any, scope: str) -> Mapping[str, Any]:
    snapshot = store.snapshot()
    scopes = snapshot.get('scopes') if isinstance(snapshot, Mapping) else None
    current = scopes.get(scope) if isinstance(scopes, Mapping) else None
    users = current.get('users') if isinstance(current, Mapping) else None
    return users if isinstance(users, Mapping) else {}


def resolve_challenge_mentions(
    raw: str, store: Any, scope: str, *, sender_id: str = '', bot_id: str = 'qq_official',
    mentions: Sequence[tuple[str, str]] = (),
) -> tuple[str, list[tuple[str, str]]]:
    """Canonicalise official @mentions/codes for the existing challenge parser.

    The target must already be registered in this exact scope. A code collision
    never selects an arbitrary profile: direct platform @mention is required.
    Nicknames are not used for identity resolution.
    """
    users = _users(store, scope)
    known = {str(user_id): profile for user_id, profile in users.items()
             if isinstance(profile, Mapping)}
    blocked = _PSEUDO_BOT_IDS | {str(bot_id)}
    text = _ENCODED_MENTION.sub(lambda match: '@'+match.group(1), str(raw or ''))
    targets: list[tuple[str, str]] = []

    def add_target(target: str) -> None:
        if target == str(sender_id):
            raise GachaError('不能挑战自己，请选择本群另一位训练家。')
        if target not in {target_id for target_id, _ in targets}:
            profile = known[target]
            targets.append((target, trainer_label(scope, target, str(profile.get('nickname') or ''))))

    # Normalised SDK components may have already removed the @ segment from
    # Plain text. Retain their real identity while enforcing this same scope.
    for mentioned_id, _ in mentions:
        target = str(mentioned_id)
        if target in blocked:
            continue
        if target not in known:
            raise GachaError('没有找到本群的这位训练家。请让对方先发送“训练家编号”登记。')
        add_target(target)

    def replace_mention(match: re.Match[str]) -> str:
        token = match.group(1)
        if token in blocked:
            return ''
        code_match = _CODE.fullmatch(token)
        if code_match:
            code = 'T-'+code_match.group(1).upper()
            matches = [user_id for user_id in known if trainer_code(scope, user_id) == code]
            if len(matches) > 1:
                raise GachaError('训练家编号发生冲突，请直接 @ 对方；系统不会代你选择其他成员。')
            target = matches[0] if matches else ''
        else:
            target = token if token in known else ''
        if not target:
            raise GachaError('没有找到本群的这位训练家。请让对方发送“训练家编号”，再使用 @T-编号发起挑战；不能只写昵称。')
        add_target(target)
        return '@'+target

    text = _TEXT_MENTION.sub(replace_mention, text)
    if len(targets) > 1:
        raise GachaError('一次只能向一位群友发起挑战。')
    return text, targets


__all__ = ['OFFICIAL_PLATFORMS', 'is_qq_official', 'official_scope',
           'trainer_code', 'trainer_label', 'resolve_challenge_mentions']
