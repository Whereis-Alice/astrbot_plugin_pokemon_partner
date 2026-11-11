"""Native QQ Official Markdown, hosted card images and command keyboards.

QQ's 2026 API opens custom Markdown to all group/C2C bots. Image references use
the documented prepare/PUT/complete material API, rather than a public image
host or the old SDK's opaque file_info. Inline keyboards remain permissioned;
a definite keyboard rejection retries the same Markdown without its keyboard.

References:
https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/type/markdown.html
https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/material-management.html
https://bot.q.qq.com/wiki/develop/api-v2/server-inter/message/trans/msg-btn.html
"""
from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import io
import logging
from pathlib import Path
import re
import tempfile
from urllib.parse import quote, urlsplit
import uuid
import zipfile

import aiohttp
from PIL import Image as PILImage

log = logging.getLogger(__name__)
MARKDOWN_LIMIT = 3900
MAX_IMAGE_BYTES = 10*1024*1024
OFFICIAL_NAMES = frozenset({'qq_official', 'qqofficial'})
TITLES = {
    'menu': '宝可梦研究站', 'draw': '今日宝可梦', 'collection': '我的宝可梦',
    'profile': '训练家资料', 'battle': '宝可梦对战', 'challenge': '宝可梦挑战',
    'exchange': '星尘兑换', 'history': '对战记录', 'ranking': '训练家排行',
    'query': '宝可梦图鉴', 'odds': '抽取概率',
}


@dataclass(frozen=True)
class ImageSource:
    data: bytes | None = None
    url: str = ''


@dataclass(frozen=True)
class UploadedImage:
    image_id: str
    url: str


class QQRequestError(RuntimeError):
    """Retain HTTP outcome without logging platform response bodies or IDs."""

    def __init__(self, status: int, code=None):
        self.status = status
        self.code = code if isinstance(code, int) or (
            isinstance(code, str) and re.fullmatch(r'\d{1,12}', code)) else None
        super().__init__(f'QQ request HTTP {status}, code {self.code}')


def _error_summary(exc):
    return str(exc) if isinstance(exc, QQRequestError) else type(exc).__name__


def _get(value, key, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def is_qq_official(event) -> bool:
    getter = getattr(event, 'get_platform_name', None)
    name = getter() if callable(getter) else _get(_get(event, 'platform_meta'), 'name', '')
    return str(name).lower() in OFFICIAL_NAMES


def _scene(event):
    raw = _get(_get(event, 'message_obj'), 'raw_message')
    group = _get(raw, 'group_openid')
    author = _get(raw, 'author')
    openid = _get(author, 'user_openid')
    channel, guild = _get(raw, 'channel_id'), _get(raw, 'guild_id')
    if group:
        return 'group', str(group)
    if openid:
        return 'c2c', str(openid)
    private = getattr(event, 'is_private_chat', lambda: False)()
    if guild and private:
        return 'dms', str(guild)
    if channel:
        return 'guild', str(channel)
    # Compatibility with event wrappers that expose only the normalized model.
    if private:
        sender = getattr(event, 'get_sender_id', lambda: '')()
        if sender:
            return 'c2c', str(sender)
    return None


def _escape(value: object) -> str:
    text = str(value).replace('\r\n', '\n').replace('\r', '\n')
    return re.sub(r'([\\`*_\[\]<>#~])', r'\\\1', text)


def _command(prefix: str, value: str) -> str:
    return str(prefix or '')+value


def _button(label: str, command: str, owner: str, *, enter=True, primary=False, prefix=''):
    return {
        'id': 'pk_'+hashlib.sha1((label+command+owner).encode()).hexdigest()[:14],
        'render_data': {'label': label, 'visited_label': '已提交' if enter else '已填入',
                        'style': 1 if primary else 0},
        'action': {'type': 2, 'permission': {'type': 0, 'specify_user_ids': [owner]},
                   'data': _command(prefix, command), 'reply': False, 'enter': enter,
                   'unsupport_tips': '请手动发送：'+_command(prefix, command).strip()},
    }


def build_keyboard(event, kind='menu', *, prefix='', target_user_id='') -> dict | None:
    """Use actual command buttons; argument-taking actions fill the input box."""
    owner = str(getattr(event, 'get_sender_id', lambda: '')() or '')
    if not owner:
        return None
    b = lambda label, command, **kw: _button(label, command, owner, prefix=prefix, **kw)
    if kind == 'draw':
        rows = [[b('再抽一次', '今日宝可梦', primary=True), b('我的宝可梦', '我的宝可梦')],
                [b('收集资料', '收集资料'), b('星尘兑换', '兑换宝可梦 ', enter=False)],
                [b('本群训练家', '本群训练家'), b('我的编号', '训练家编号')]]
    elif kind == 'collection':
        text = str(getattr(event, 'get_message_str', lambda: '')() or '')
        match = re.search(r'(?:我的宝可梦|我的图鉴|图鉴收藏)\s+(\d+)', text)
        page = max(1, min(100000, int(match.group(1)))) if match else 1
        rows = [[b('上一页', f'我的宝可梦 {max(1, page-1)}'),
                 b('下一页', f'我的宝可梦 {page+1}')],
                [b('发起挑战', '宝可梦挑战 ', enter=False, primary=True),
                 b('星尘兑换', '兑换宝可梦 ', enter=False)],
                [b('收集资料', '收集资料'), b('全部功能', '图鉴帮助')],
                [b('本群训练家', '本群训练家'), b('我的编号', '训练家编号')]]
    elif kind == 'challenge' and target_user_id:
        target = str(target_user_id)
        rows = [[_button('选择宝可梦接战', '接收挑战 ', target, enter=False, primary=True, prefix=prefix),
                 _button('拒绝挑战', '拒绝挑战', target, prefix=prefix)],
                [b('取消挑战', '取消挑战'), b('我的宝可梦', '我的宝可梦')],
                [b('本群训练家', '本群训练家'), b('我的编号', '训练家编号')]]
    elif kind in {'battle', 'history', 'challenge'}:
        rows = [[b('发起挑战', '宝可梦挑战 ', enter=False, primary=True),
                 b('我的宝可梦', '我的宝可梦')],
                [b('对战记录', '对战记录'), b('对战排行', '对战排行')],
                [b('对战模拟', '对战 ', enter=False), b('全部功能', '图鉴帮助')],
                [b('本群训练家', '本群训练家'), b('我的编号', '训练家编号')]]
    elif kind == 'exchange':
        rows = [[b('选择兑换对象', '兑换宝可梦 ', enter=False, primary=True),
                 b('我的宝可梦', '我的宝可梦')],
                [b('收集资料', '收集资料'), b('全部功能', '图鉴帮助')]]
    else:
        rows = [[b('今日宝可梦', '今日宝可梦', primary=True), b('我的宝可梦', '我的宝可梦')],
                [b('图鉴查询', '宝可梦 ', enter=False), b('收集资料', '收集资料')],
                [b('星尘兑换', '兑换宝可梦 ', enter=False), b('对战记录', '对战记录')],
                [b('发起挑战', '宝可梦挑战 ', enter=False), b('本群训练家', '本群训练家')],
                [b('我的编号', '训练家编号'), b('全部功能', '图鉴帮助')]]
    buttons = [button for row in rows for button in row]
    return {'content': {'rows': [{'buttons': buttons[i:i+3]}
                                 for i in range(0, len(buttons), 3)]}}


def _draw_heading(name: str, rarity: str, description: str, prefix: str = '') -> str:
    """Keep rarity compact and draw annotations separate from the bold name."""
    _, bracket, tags = description.partition('（')
    suffix = bracket+tags if bracket else ''
    symbol = '✨ ' if '闪光' in tags else '✦ '
    return prefix+'**'+symbol+_escape(name+'｜'+rarity)+'**'+_escape(suffix)


def _format_draw(text: str) -> tuple[str, list[str]]:
    lines = text.splitlines()
    result = []
    for line in lines:
        single = re.match(r'^(.+?)的今日宝可梦是\s+(.+?)｜(SSR|SR|R|C)·(.+)$', line)
        if single:
            owner, name, rarity, badge = single.groups()
            result += ['> '+_escape(owner)+'的今日宝可梦是',
                       _draw_heading(name, rarity, badge)]
        elif line.startswith('今日剩余 '):
            status = re.match(r'^今日剩余 (\d+/\d+) 次｜星尘(\d+)(.*)$', line)
            if status:
                count, dust, pity = status.groups()
                result += ['> 今日剩余 **'+count+'** 次　｜　星尘 **'+dust+'**']
                if pity:
                    result += ['> '+_escape(pity.lstrip('｜'))]
            else:
                result.append('> '+_escape(line))
        elif re.match(r'^\d+\. ', line):
            numbered = re.match(r'^(\d+\. )(.+?)｜(SSR|SR|R|C)·(.+)$', line)
            if numbered:
                prefix, name, rarity, badge = numbered.groups()
                result.append(_draw_heading(name, rarity, badge, prefix))
            else:
                result.append(_escape(line))
        else:
            result.append(_escape(line))
    return '今日宝可梦', result


def build_markdown(text: str, kind='menu', *, images: Sequence[tuple[str, int, int]] = ()) -> str:
    """Readable headings/status beside retained, full-resolution card artwork."""
    text = str(text or '').strip()
    if kind == 'draw':
        title, lines = _format_draw(text)
    else:
        title = TITLES.get(kind, TITLES['menu'])
        lines = text.splitlines()
        if lines:
            heading = re.match(r'^【([^】]+)】(.*)$', lines[0])
            if heading:
                title = heading.group(1)
                lines[0] = heading.group(2).strip()
        lines = [_escape(line) for line in lines]
    body = [] if kind == 'draw' else ['# '+_escape(title)]
    if images:
        # Main card art leads the reading flow. The image bytes are uploaded at
        # native resolution; the size hint only controls mobile presentation.
        if kind == 'draw' and lines:
            split = next((i for i, line in enumerate(lines) if line.startswith('> 今日剩余')), len(lines))
            body += lines[:split]
            tail = lines[split:]
        else:
            tail = lines
        for index, (url, width, height) in enumerate(images, 1):
            if kind == 'draw':
                # Draw pages retain native card pixels. Keep their real size
                # in QQ's image hint instead of describing a 640px preview.
                display_width, display_height = width, height
            else:
                display_width = min(640, width)
                display_height = max(1, round(height*display_width/max(1, width)))
            body += [f'![{_escape(title)} {index} #{display_width}px #{display_height}px]({url})']
        if tail:
            body += ['***', *tail]
    else:
        body += lines
    return '\n\n'.join(body).strip()+'\n'


def _image_source(component) -> ImageSource:
    value = _get(component, 'file', '') or _get(component, 'url', '')
    if isinstance(value, bytes):
        return ImageSource(data=value)
    value = str(value or '')
    if value.startswith('base64://'):
        return ImageSource(data=base64.b64decode(value[9:], validate=True))
    if value.startswith('file:///'):
        return ImageSource(data=Path(value[8:]).read_bytes())
    if value.startswith(('https://', 'http://')):
        parsed = urlsplit(value)
        if not parsed.hostname or parsed.username or parsed.password or any(c in value for c in '\r\n()'):
            raise ValueError('Invalid card image URL')
        return ImageSource(url=value)
    if value:
        return ImageSource(data=Path(value).read_bytes())
    raise ValueError('Empty card image source')


def _extract_results(results):
    text, images, mentions = [], [], []
    unsupported = []

    def component(value):
        name = type(value).__name__
        if isinstance(value, str):
            text.append(value)
        elif name == 'Plain':
            text.append(str(_get(value, 'text', '')))
        elif name == 'Image':
            images.append(_image_source(value))
        elif name == 'At':
            uid = _get(value, 'qq') or _get(value, 'user_id')
            if uid and str(uid) not in {'all', 'qq_official'}:
                mentions.append(str(uid))
        elif name == 'Nodes':
            for node in _get(value, 'nodes', ()):
                component(node)
        elif name == 'Node':
            for child in _get(value, 'content', ()):
                component(child)
        elif name in {'Reply', 'AtAll'}:
            pass
        else:
            unsupported.append(value)

    for result in results:
        if result is None:
            continue
        if isinstance(result, str):
            component(result)
            continue
        if isinstance(result, tuple) and len(result) == 2 and result[0] in {'text', 'chain'}:
            chain = [result[1]] if result[0] == 'text' else result[1]
        else:
            chain = _get(result, 'chain', result if isinstance(result, list) else None)
        if chain is None:
            unsupported.append(result)
        else:
            for part in chain:
                component(part)
    return '\n'.join(part.strip() for part in text if part.strip()), images, mentions, unsupported


async def _api_request(api, method: str, path: str, payload=None):
    """Use the SDK's authentication/session, with one HTTP attempt per call.

    Botpy retries connection resets and discards HTTP 400 status in its public
    request helper. A committed draw must not be duplicated by those retries.
    Keep the status here and never include the response body in our logs.
    """
    from botpy.http import Route
    http = api._http
    route = Route(method, path, is_sandbox=getattr(http, 'is_sandbox', False))
    if not callable(getattr(http, 'check_session', None)):
        # Protocol sinks and older compatible wrappers expose request only.
        return await http.request(route, **({'json': payload} if payload is not None else {}))
    await http.check_session()
    endpoint = route.url
    if not getattr(http, 'is_sandbox', False) and urlsplit(endpoint).hostname in {
        'api.sgroup.qq.com', 'api.bot.qq.com',
    }:
        endpoint = 'https://api.bot.qq.com'+path
    async with http._session.request(
        method=method, url=endpoint, headers=http._headers,
        timeout=aiohttp.ClientTimeout(total=getattr(http, 'timeout', 25)),
        allow_redirects=False, **({'json': payload} if payload is not None else {}),
    ) as response:
        try:
            result = await response.json(content_type=None)
        except (ValueError, aiohttp.ContentTypeError):
            result = None
        code = _get(result, 'err_code', _get(result, 'code'))
        if 200 <= response.status < 300 and code in (None, 0, '0', 304023, 304024, '304023', '304024'):
            return result
        raise QQRequestError(response.status, code)


async def _put_image(url: str, data: bytes, content_type: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('QQ material upload did not return a secure presigned URL')
    timeout = aiohttp.ClientTimeout(total=25)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.put(url, data=data, headers={'Content-Type': content_type},
                               allow_redirects=False) as response:
            response.raise_for_status()
            etag = response.headers.get('ETag', '').strip('"')
            if not re.fullmatch(r'[0-9a-fA-F]{32}', etag):
                raise ValueError('QQ material upload did not return a valid MD5 ETag')
            return etag.lower()


async def upload_markdown_image(event, data: bytes) -> UploadedImage:
    """Use the current official three-stage material protocol exactly."""
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError('Card image is empty or exceeds the hosted-image size limit')
    with PILImage.open(io.BytesIO(data)) as image:
        format_name = str(image.format or '').upper()
        image.verify()
    content_type, suffix = {'JPEG': ('image/jpeg', 'jpeg'), 'PNG': ('image/png', 'png'),
                            'GIF': ('image/gif', 'gif'), 'WEBP': ('image/webp', 'webp')}.get(
                                format_name, ('', ''))
    if not content_type:
        raise ValueError('Unsupported QQ hosted image format')
    api = event.bot.api
    digest = hashlib.md5(data).hexdigest()
    prepared = await _api_request(api, 'POST', '/v2/media-images/prepare', {
        'file_name': 'pokemon-card-'+uuid.uuid4().hex[:12]+'.'+suffix,
        'file_size': len(data), 'content_type': content_type,
        'md5': digest, 'sha1': hashlib.sha1(data).hexdigest(),
    })
    if not isinstance(prepared, Mapping) or not prepared.get('upload_id') or not prepared.get('presigned_url'):
        raise ValueError('Invalid QQ material preparation response')
    etag = await _put_image(str(prepared['presigned_url']), data, content_type)
    if etag != digest:
        raise ValueError('QQ material upload ETag does not match the card bytes')
    completed = await _api_request(api, 'POST', '/v2/media-images/complete', {
        'upload_id': prepared['upload_id'], 'md5': etag,
    })
    if not isinstance(completed, Mapping) or not completed.get('image_id'):
        raise ValueError('Invalid QQ material completion response')
    url = str(completed.get('url') or '')
    if not url.startswith('qqbot-media://image/v1/') or any(c in url for c in '\r\n()'):
        await _cleanup_images(event, [UploadedImage(str(completed['image_id']), '')])
        raise ValueError('QQ material did not return a valid hosted-image reference')
    return UploadedImage(str(completed['image_id']), url)


async def _cleanup_images(event, images: Sequence[UploadedImage]) -> None:
    # QQ copies the material into a sent message; deleting our temporary image
    # does not alter that message. Never enumerate/delete another plugin's IDs.
    for image in images:
        try:
            await asyncio.wait_for(_api_request(event.bot.api, 'DELETE',
                '/v2/media-images/'+quote(image.image_id, safe='')+'/delete'), timeout=5)
        except Exception as exc:
            log.warning('QQ card material cleanup failed (%s); original message retained', _error_summary(exc))


def _definite_rejection(exc: Exception) -> bool:
    if isinstance(exc, (OSError, asyncio.TimeoutError)):
        return False
    if isinstance(getattr(exc, 'status', None), int):
        if 400 <= exc.status < 500:
            return True
        return 200 <= exc.status < 300 and getattr(exc, 'code', None) not in (
            None, 0, '0', 304023, 304024, '304023', '304024')
    if type(exc).__name__ in {'ForbiddenError', 'AuthenticationFailedError', 'NotFoundError',
                             'MethodNotAllowedError'}:
        return True
    value = str(exc)
    return bool(re.search(r'(?:错误代码|status|http)[\s:：]+4\d\d', value, re.I)) or any(
        term in value.lower() for term in ('不允许发送原生 markdown', '不允许发送原生markdown',
                                          'keyboard permission', '按钮权限', 'markdown not allowed'))


def _mark_sent(event, *, uncertain=False):
    event._has_send_oper = True
    if uncertain:
        event._pokemon_qq_delivery_uncertain = True


async def _post(event, scene, payload):
    api = event.bot.api
    kind, target = scene
    if kind == 'group':
        path = '/v2/groups/'+quote(target, safe='')+'/messages'
    elif kind == 'c2c':
        path = '/v2/users/'+quote(target, safe='')+'/messages'
    elif kind == 'guild':
        path = '/channels/'+quote(target, safe='')+'/messages'
    else:
        path = '/dms/'+quote(target, safe='')+'/messages'
    response = await _api_request(api, 'POST', path, payload)
    if not response or not _get(response, 'id'):
        _mark_sent(event, uncertain=True)
        log.warning('QQ Official send returned no message ID; delivery uncertain, not repeating committed result')
    else:
        _mark_sent(event)
    return response


def _markdown_payload(markdown: str, config: Mapping, title: str):
    template = str(config.get('qq_official_markdown_template_id') or '').strip()
    mode = str(config.get('qq_official_mode', 'auto')).lower()
    if mode == 'template' or (mode == 'auto' and template):
        if not template:
            raise ValueError('Markdown template mode requires a template ID')
        title_key = str(config.get('qq_official_markdown_title_key') or 'title').strip()
        body_key = str(config.get('qq_official_markdown_body_key') or 'body').strip()
        if not title_key or not body_key or title_key == body_key:
            raise ValueError('Markdown template parameter names must be distinct')
        body = markdown.split('\n', 1)[1].strip() if markdown.startswith('# ') else markdown
        return {'custom_template_id': template, 'params': [
            {'key': title_key, 'values': [title]}, {'key': body_key, 'values': [body]}]}
    return {'content': markdown}


def _parts(text: str, size=1700):
    if not text:
        return []
    parts = []
    while len(text) > size:
        split = text.rfind('\n', 0, size+1)
        if split < size//2:
            split = size
        parts.append(text[:split])
        text = text[split:].lstrip('\n')
    if text:
        parts.append(text)
    return parts


async def _legacy_batch(event, text: str, images: Sequence[ImageSource]) -> bool:
    """Preserve all images even when enhanced presentation is switched off."""
    import astrbot.api.message_components as Comp
    pieces = _parts(text)
    completed = 0

    async def send(chain):
        nonlocal completed
        try:
            await event.send(chain)
        except Exception as exc:
            if not _definite_rejection(exc) or completed:
                _mark_sent(event, uncertain=True)
                log.warning('QQ compatible delivery interrupted (%s); not repeating partial result',
                            _error_summary(exc))
                return False
            # An explicit rejection before any successful send remains the
            # wrapper's responsibility. Do not attempt the fallback twice.
            raise
        completed += 1
        _mark_sent(event)
        return True

    if max(len(pieces), len(images), 1) > 5:
        # The platform's passive-reply quota is finite. Send the complete
        # content as a file rather than silently dropping text or pictures.
        from astrbot.core.utils.astrbot_path import get_astrbot_temp_path
        directory = Path(get_astrbot_temp_path())
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pokemon-qq-result-', dir=directory) as temp:
            if images:
                path = Path(temp)/'pokemon-result.zip'
                with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
                    bundle.writestr('result.txt', text)
                    for index, image in enumerate(images, 1):
                        if image.data:
                            with PILImage.open(io.BytesIO(image.data)) as source:
                                suffix = {'JPEG': 'jpg', 'PNG': 'png', 'GIF': 'gif',
                                          'WEBP': 'webp'}.get(source.format, 'image')
                            bundle.writestr(f'card-{index}.{suffix}', image.data)
                        else:
                            bundle.writestr(f'card-{index}-url.txt', image.url)
            else:
                path = Path(temp)/'pokemon-result.txt'
                path.write_text(text, encoding='utf-8', newline='\n')
            chain = event.chain_result([Comp.Plain('内容较长，完整结果已放在附件中。'),
                                        Comp.File(name=path.name, file=str(path))])
            if hasattr(chain, 'use_markdown'):
                chain.use_markdown(False)
            await send(chain)
        return True
    count = max(len(images), len(pieces), 1)
    for index in range(count):
        components = []
        if index < len(pieces):
            components.append(Comp.Plain(pieces[index]))
        if index < len(images):
            image = images[index]
            components.append(Comp.Image.fromBytes(image.data) if image.data else Comp.Image.fromURL(image.url))
        if not components:
            continue
        chain = event.chain_result(components)
        if hasattr(chain, 'use_markdown'):
            chain.use_markdown(False)
        if not await send(chain):
            break
    return True


async def adapt_results(event, results: Sequence, config: Mapping | None = None,
                        keyboard_kind='menu', *, prefix: str | None = None) -> bool:
    """Send a complete command output in QQ's native layout; return consumed.

False means no send occurred and the normal adapter may receive the results.
After an ambiguous send response, True prevents duplicate delivery. Other
platforms and unsupported components are left to AstrBot's ordinary sender.
"""
    if not is_qq_official(event):
        return False
    config = config or {}
    scene = _scene(event)
    if scene is None or not _get(_get(event, 'bot'), 'api'):
        return False
    try:
        text, images, mentions, unsupported = _extract_results(results)
    except Exception as exc:
        log.warning('QQ output preparation failed (%s); keeping original output', type(exc).__name__)
        return False
    if unsupported or not (text or images):
        return False
    mode = str(config.get('qq_official_mode', 'auto')).lower()
    if mode == 'off':
        return await _legacy_batch(event, text, images)
    owned = []
    image_refs = []
    post_started = False
    legacy_started = False

    async def fallback():
        nonlocal legacy_started
        legacy_started = True
        return await _legacy_batch(event, text, images)

    try:
        for image in images:
            if image.data:
                uploaded = await upload_markdown_image(event, image.data)
                owned.append(uploaded)
                with PILImage.open(io.BytesIO(image.data)) as source:
                    width, height = source.size
                image_refs.append((uploaded.url, width, height))
            else:
                # The documented API also accepts a public external image URL.
                image_refs.append((image.url, 640, 640))
        markdown = build_markdown(text, keyboard_kind, images=image_refs)
        if len(markdown) > MARKDOWN_LIMIT:
            return await fallback()
        payload = {'markdown': _markdown_payload(markdown, config, TITLES.get(keyboard_kind, TITLES['menu']))}
        message_id = str(_get(_get(event, 'message_obj'), 'message_id', '') or '')
        if message_id:
            payload['msg_id'] = message_id
        if scene[0] in {'group', 'c2c'}:
            payload['msg_type'] = 2
            payload['msg_seq'] = int(uuid.uuid4().int % 999999)+1
        command_prefix = str(config.get('qq_official_command_prefix', '') if prefix is None else prefix)
        if config.get('qq_official_buttons', True):
            keyboard_id = str(config.get('qq_official_keyboard_id') or '').strip()
            keyboard = {'id': keyboard_id} if keyboard_id else build_keyboard(
                event, keyboard_kind, prefix=command_prefix,
                target_user_id=mentions[0] if mentions else '')
            if keyboard:
                payload['keyboard'] = keyboard
        post_started = True
        try:
            await _post(event, scene, payload)
        except Exception as exc:
            if not _definite_rejection(exc):
                _mark_sent(event, uncertain=True)
                log.warning('QQ Official delivery uncertain (%s); not repeating committed result', _error_summary(exc))
                return True
            # A rejected keyboard has not sent a message. Keep the exact same
            # hosted card references and Markdown; avoid another draw/upload.
            if payload.get('keyboard'):
                retry = dict(payload)
                retry.pop('keyboard')
                if 'msg_seq' in retry:
                    retry['msg_seq'] += 1
                try:
                    await _post(event, scene, retry)
                    log.info('QQ keyboard unavailable (%s); native Markdown card retained', _error_summary(exc))
                    return True
                except Exception as retry_exc:
                    if not _definite_rejection(retry_exc):
                        _mark_sent(event, uncertain=True)
                        log.warning('QQ Official retry delivery uncertain (%s)', _error_summary(retry_exc))
                        return True
            post_started = False
            log.info('QQ native Markdown was rejected; using original text and all card images')
            return await fallback()
        return True
    except Exception as exc:
        if legacy_started:
            raise
        if post_started:
            _mark_sent(event, uncertain=True)
            log.warning('QQ output interrupted after send (%s); not duplicating result', _error_summary(exc))
            return True
        log.warning('QQ hosted-card preparation unavailable (%s); using compatible output', type(exc).__name__)
        return await fallback()
    finally:
        await _cleanup_images(event, owned)


async def adapt_result(event, result, config: Mapping | None = None, keyboard_kind='menu', **kwargs) -> bool:
    return await adapt_results(event, [result], config, keyboard_kind, **kwargs)


async def send_official(event, *, text: str, image_bytes: bytes | None = None,
                        keyboard_kind='menu', config: Mapping | None = None,
                        prefix: str | None = None, **_unused) -> bool:
    import astrbot.api.message_components as Comp
    components = [Comp.Plain(text)] if text else []
    if image_bytes:
        components.append(Comp.Image.fromBytes(image_bytes))
    return await adapt_result(event, event.chain_result(components), config, keyboard_kind, prefix=prefix)


__all__ = ['is_qq_official', 'adapt_result', 'adapt_results', 'send_official',
           'build_markdown', 'build_keyboard', 'upload_markdown_image']
