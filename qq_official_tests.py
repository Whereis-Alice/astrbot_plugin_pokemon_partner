"""Real AstrBot components and isolated QQ protocol sinks; no external sends."""
from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
import hashlib
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import zipfile

# SDK imports can create runtime files. Respect the user's D-drive policy on
# Windows and keep every test independent of any actual AstrBot data directory.
_temp_parent = Path('D:/CodexData/Temp/pokemon-qqofficial-tests') if os.name == 'nt' else None
if _temp_parent:
    _temp_parent.mkdir(parents=True, exist_ok=True)
_runtime = tempfile.TemporaryDirectory(prefix='sdk-', dir=_temp_parent)
os.environ.setdefault('ASTRBOT_ROOT', _runtime.name)

import astrbot.api.message_components as Comp
from astrbot.api.event import MessageChain
from PIL import Image
import qq_official as qq


def card(color='red', size=(1024, 1600), format='PNG'):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, format=format)
    return stream.getvalue()


class ProtocolSink:
    def __init__(self, post_responses=()):
        self.calls = []
        self.post_responses = list(post_responses)
        self.prepared = 0
        self.completed = 0

    async def request(self, route, **kwargs):
        self.calls.append((route.method, route.path, deepcopy(kwargs)))
        if route.path.endswith('/prepare'):
            self.prepared += 1
            return {'upload_id': f'upload-{self.prepared}',
                    'presigned_url': 'https://upload.example.test/image'}
        if route.path.endswith('/complete'):
            self.completed += 1
            return {'image_id': f'own-{self.completed}',
                    'url': f'qqbot-media://image/v1/token-{self.completed}',
                    'ttl': 7776000}
        if route.method == 'DELETE':
            return None
        if self.post_responses:
            result = self.post_responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return {'id': 'sent-message'}

    @property
    def messages(self):
        return [(method, path, kw['json']) for method, path, kw in self.calls
                if path.endswith('/messages')]

    @property
    def deletions(self):
        return [path for method, path, kw in self.calls if method == 'DELETE']


class Event:
    def __init__(self, *, scene='group', platform='qq_official', text='今日宝可梦',
                 post_responses=(), send_responses=()):
        self.platform, self.text, self.private = platform, text, scene in {'c2c', 'dms'}
        raw = {
            'group': {'group_openid': 'group-openid'},
            'c2c': {'author': {'user_openid': 'user-openid'}},
            'guild': {'channel_id': 'channel-id', 'guild_id': 'guild-id'},
            'dms': {'channel_id': 'channel-id', 'guild_id': 'guild-id'},
            'missing': {},
        }[scene]
        self.message_obj = SimpleNamespace(raw_message=raw, message_id='incoming-message')
        self.sink = ProtocolSink(post_responses)
        self.bot = SimpleNamespace(api=SimpleNamespace(_http=self.sink))
        self.sent = []
        self.files = []
        self.send_responses = list(send_responses)

    def get_platform_name(self): return self.platform
    def get_sender_id(self): return 'sender-openid'
    def get_message_str(self): return self.text
    def is_private_chat(self): return self.private

    def chain_result(self, chain):
        result = MessageChain()
        result.chain.extend(chain)
        return result

    async def send(self, chain):
        self.sent.append(chain)
        for component in chain.chain:
            if isinstance(component, Comp.File):
                self.files.append((component.name, Path(component.file_).read_bytes()))
        if self.send_responses:
            response = self.send_responses.pop(0)
            if isinstance(response, Exception):
                raise response


class Response:
    def __init__(self, status=200, result=None, *, json_error=None, etag=''):
        self.status, self.result, self.json_error = status, result, json_error
        self.headers = {'ETag': etag}

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False

    async def json(self, **kwargs):
        if self.json_error: raise self.json_error
        return self.result

    def raise_for_status(self):
        if self.status >= 400: raise qq.QQRequestError(self.status, 400)


class Session:
    def __init__(self, responses=()):
        self.responses, self.calls = list(responses), []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses.pop(0)
        if isinstance(result, Exception): raise result
        return result

    def put(self, url, **kwargs):
        return self.request(url=url, **kwargs)

    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


def put_digest(url, data, content_type): return hashlib.md5(data).hexdigest()


class QQOfficialTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.put = patch.object(qq, '_put_image', AsyncMock(side_effect=put_digest))
        self.put_mock = self.put.start()
        self.addCleanup(self.put.stop)

    @staticmethod
    def results(event, images=(), text='完整结果'):
        return [event.chain_result([Comp.Plain(text), *[Comp.Image.fromBytes(data) for data in images]])]

    async def test_other_platform_and_unknown_scene_are_not_consumed(self):
        for event in (Event(platform='aiocqhttp'), Event(scene='missing')):
            self.assertFalse(await qq.adapt_results(event, self.results(event)))
            self.assertEqual(event.sink.calls, [])
            self.assertEqual(event.sent, [])

    async def test_all_four_official_scenes_use_their_exact_routes(self):
        paths = {'group': '/v2/groups/group-openid/messages',
                 'c2c': '/v2/users/user-openid/messages',
                 'guild': '/channels/channel-id/messages',
                 'dms': '/dms/guild-id/messages'}
        for scene, path in paths.items():
            with self.subTest(scene=scene):
                event = Event(scene=scene)
                self.assertTrue(await qq.adapt_results(event, self.results(event)))
                method, actual, payload = event.sink.messages[0]
                self.assertEqual((method, actual), ('POST', path))
                self.assertEqual(payload['msg_id'], 'incoming-message')
                self.assertEqual('msg_type' in payload, scene in {'group', 'c2c'})
                self.assertEqual('msg_seq' in payload, scene in {'group', 'c2c'})
                self.assertTrue(event._has_send_oper)

    async def test_three_full_resolution_cards_share_one_markdown_and_cleanup(self):
        event = Event()
        pages = [card('red'), card('blue', (1200, 2100)), card('gold', (1100, 2600))]
        self.assertTrue(await qq.adapt_results(event, self.results(event, pages), keyboard_kind='battle'))
        self.assertEqual(len(event.sink.messages), 1)
        markdown = event.sink.messages[0][2]['markdown']['content']
        self.assertEqual(markdown.count('qqbot-media://image/v1/'), 3)
        self.assertIn('#640px #1000px', markdown)
        self.assertIn('#640px #1120px', markdown)
        self.assertIn('#640px #1513px', markdown)
        self.assertEqual([call.args[1] for call in self.put_mock.await_args_list], pages)
        prepares = [kw['json'] for method, path, kw in event.sink.calls if path.endswith('/prepare')]
        for payload, data in zip(prepares, pages):
            self.assertEqual(payload['file_size'], len(data))
            self.assertEqual(payload['content_type'], 'image/png')
            self.assertEqual(payload['md5'], hashlib.md5(data).hexdigest())
            self.assertEqual(payload['sha1'], hashlib.sha1(data).hexdigest())
            self.assertTrue(payload['file_name'].endswith('.png'))
        self.assertEqual(event.sink.deletions, [f'/v2/media-images/own-{n}/delete' for n in range(1, 4)])
        self.assertEqual(event.sent, [])

    async def test_dynamic_keyboard_rejection_reuses_same_markdown_and_card(self):
        event = Event(post_responses=[qq.QQRequestError(403, 400), {'id': 'success'}])
        self.assertTrue(await qq.adapt_results(event, self.results(event, [card()]), keyboard_kind='draw'))
        first, retry = [entry[2] for entry in event.sink.messages]
        self.assertIn('keyboard', first)
        self.assertNotIn('keyboard', retry)
        self.assertEqual(first['markdown'], retry['markdown'])
        self.assertEqual(retry['msg_seq'], first['msg_seq']+1)
        self.assertEqual(event.sink.prepared, 1)
        self.assertEqual(event.sink.completed, 1)
        self.assertEqual(event.sent, [])

    async def test_draw_title_is_native_markdown_and_only_card_is_uploaded(self):
        event = Event()
        original = card()
        text = '狐狸的今日宝可梦是 单卵细胞球｜C·普通（NEW）\n今日剩余 2/3 次｜星尘0'
        self.assertTrue(await qq.adapt_results(event, self.results(event, [original], text), keyboard_kind='draw'))
        payload = event.sink.messages[0][2]
        markdown = payload['markdown']['content']
        self.assertEqual(markdown.count('qqbot-media://'), 1)
        self.assertEqual(markdown.count('**✦ 单卵细胞球｜C**（NEW）'), 1)
        self.assertLess(markdown.index('**✦ 单卵细胞球'), markdown.index('token-1'))
        self.assertIn('狐狸的今日宝可梦是', markdown)
        self.assertIn('今日剩余 **2/3**', markdown)
        prepared = [kw['json'] for method, path, kw in event.sink.calls if path.endswith('/prepare')]
        self.assertEqual(prepared[0]['md5'], hashlib.md5(original).hexdigest())
        self.assertEqual([call.args[1] for call in self.put_mock.await_args_list], [original])
        self.assertEqual(event.sink.prepared, 1)
        self.assertEqual(len(event.sink.deletions), 1)
        rows = payload['keyboard']['content']['rows']
        self.assertEqual([len(row['buttons']) for row in rows], [3, 3])

    async def test_draw_without_card_keeps_native_title_without_image_upload(self):
        event = Event()
        text = '小智的今日宝可梦是 炎帝｜SSR·传说（NEW）'
        self.assertTrue(await qq.adapt_results(event, self.results(event, text=text), keyboard_kind='draw'))
        markdown = event.sink.messages[0][2]['markdown']['content']
        self.assertIn('**✦ 炎帝｜SSR**（NEW）', markdown)
        self.assertEqual(markdown.count('qqbot-media://'), 0)
        self.put_mock.assert_not_awaited()
        self.assertEqual(event.sink.prepared, 0)

    async def test_ten_draw_three_native_pages_preserve_pixels_and_complete_result(self):
        event = Event()
        pages = [card('red', (2190, 3670)), card('blue', (2190, 3670)),
                 card('gold', (2190, 1855))]
        text = '小智的今日宝可梦是：\n'+'\n'.join(
            f'{index}. 宝可梦{index}｜C·普通（NEW）' for index in range(1, 11))
        text += '\n今日剩余 0/10 次｜星尘0｜SSR 保底进度 10/30'
        self.assertTrue(await qq.adapt_results(
            event, self.results(event, pages, text), keyboard_kind='draw'))
        self.assertEqual(len(event.sink.messages), 1)
        payload = event.sink.messages[0][2]
        markdown = payload['markdown']['content']
        self.assertEqual(markdown.count('qqbot-media://'), 3)
        self.assertEqual(markdown.count('#2190px #3670px'), 2)
        self.assertIn('#2190px #1855px', markdown)
        self.assertNotIn('#640px', markdown)
        for index in range(1, 11):
            self.assertEqual(markdown.count(f'{index}. **✦ 宝可梦{index}｜C**（NEW）'), 1)
        self.assertIn('今日剩余 **0/10**', markdown)
        self.assertIn('SSR 保底进度 10/30', markdown)
        self.assertEqual([call.args[1] for call in self.put_mock.await_args_list], pages)
        prepared = [kw['json'] for method, path, kw in event.sink.calls if path.endswith('/prepare')]
        self.assertEqual([p['md5'] for p in prepared], [hashlib.md5(p).hexdigest() for p in pages])
        self.assertEqual([len(r['buttons']) for r in payload['keyboard']['content']['rows']], [3, 3])
        self.assertEqual(event.sink.deletions, [f'/v2/media-images/own-{n}/delete' for n in range(1, 4)])
        self.assertEqual(event.sent, [])
        self.assertEqual(event.files, [])


    async def test_markdown_rejection_falls_back_to_all_original_cards(self):
        event = Event(post_responses=[qq.QQRequestError(403), qq.QQRequestError(400)])
        pages = [card('red'), card('blue'), card('gold')]
        self.assertTrue(await qq.adapt_results(event, self.results(event, pages, '原始完整文字')))
        self.assertEqual(len(event.sent), 3)
        self.assertEqual(event.sent[0].chain[0].text, '原始完整文字')
        decoded = [base64.b64decode(next(c for c in chain.chain if isinstance(c, Comp.Image)).file[9:])
                   for chain in event.sent]
        self.assertEqual(decoded, pages)
        self.assertTrue(all(chain.use_markdown_ is False for chain in event.sent))

    async def test_timeout_http500_and_missing_id_never_repeat_committed_result(self):
        for failure in (asyncio.TimeoutError(), ConnectionResetError(), qq.QQRequestError(500),
                        None, {}, {'success': True}):
            with self.subTest(failure=type(failure).__name__):
                event = Event(post_responses=[failure])
                self.assertTrue(await qq.adapt_results(event, self.results(event, [card()])))
                self.assertEqual(len(event.sink.messages), 1)
                self.assertTrue(event._pokemon_qq_delivery_uncertain)
                self.assertEqual(event.sent, [])
                self.assertEqual(event.sink.deletions, ['/v2/media-images/own-1/delete'])

    async def test_preexisting_send_flag_does_not_swallow_upload_failure(self):
        event = Event()
        event._has_send_oper = True
        with patch.object(qq, 'upload_markdown_image', AsyncMock(side_effect=ValueError('not available'))):
            self.assertTrue(await qq.adapt_results(event, self.results(event, [card()])))
        self.assertEqual(len(event.sent), 1)
        self.assertFalse(hasattr(event, '_pokemon_qq_delivery_uncertain'))
        self.assertEqual(event.sink.messages, [])

    async def test_invalid_completed_reference_is_deleted_before_fallback(self):
        event = Event()
        original = event.sink.request
        async def request(route, **kwargs):
            if route.path.endswith('/complete'):
                return {'image_id': 'new/id', 'url': 'https://unexpected.test/card'}
            return await original(route, **kwargs)
        event.sink.request = request
        self.assertTrue(await qq.adapt_results(event, self.results(event, [card()])))
        self.assertEqual(event.sink.deletions, ['/v2/media-images/new%2Fid/delete'])
        self.assertEqual(len(event.sent), 1)

    async def test_cleanup_failure_does_not_repeat_successful_message(self):
        event = Event()
        with patch.object(qq, '_api_request', wraps=qq._api_request) as request:
            # Use the actual helper as the delegated target without recursion.
            real = request._mock_wraps
            async def delegate(api, method, path, payload=None):
                if method == 'DELETE': raise OSError('cleanup failure')
                return await real(api, method, path, payload)
            request.side_effect = delegate
            self.assertTrue(await qq.adapt_results(event, self.results(event, [card()])))
        self.assertEqual(len(event.sink.messages), 1)
        self.assertEqual(event.sent, [])

    async def test_off_mode_pairs_text_chunks_and_images_without_unnecessary_zip(self):
        event = Event()
        pages = [card('red'), card('blue'), card('gold')]
        text = '字'*4500
        self.assertTrue(await qq.adapt_results(event, self.results(event, pages, text),
                                              {'qq_official_mode': 'off'}))
        self.assertEqual(len(event.sent), 3)
        self.assertEqual(event.files, [])
        self.assertEqual(''.join(c.text for chain in event.sent for c in chain.chain if isinstance(c, Comp.Plain)), text)
        self.assertEqual(event.sink.calls, [])

    async def test_reply_quota_overflow_archive_retains_all_images_and_full_text(self):
        event = Event()
        pages = [card(size=(16, 24), color=color) for color in ('red', 'blue', 'gold', 'green', 'gray', 'pink')]
        text = '全部对战过程\n每一回合的招式、伤害与效果。'
        self.assertTrue(await qq.adapt_results(event, self.results(event, pages, text),
                                              {'qq_official_mode': 'off'}))
        self.assertEqual(len(event.sent), 1)
        self.assertEqual(event.files[0][0], 'pokemon-result.zip')
        with zipfile.ZipFile(io.BytesIO(event.files[0][1])) as bundle:
            self.assertEqual(bundle.read('result.txt').decode('utf-8'), text)
            self.assertEqual([bundle.read(f'card-{n}.png') for n in range(1, 7)], pages)

    async def test_very_long_plain_text_is_attached_in_full(self):
        event = Event()
        text = '回合效果\n'*2500
        self.assertTrue(await qq.adapt_results(event, self.results(event, text=text)))
        self.assertEqual(event.files, [('pokemon-result.txt', text.strip().encode('utf-8'))])
        self.assertEqual(event.sink.messages, [])

    async def test_compatible_send_first_explicit_rejection_is_not_retried_locally(self):
        event = Event(send_responses=[qq.QQRequestError(403)])
        with self.assertRaises(qq.QQRequestError):
            await qq.adapt_results(event, self.results(event), {'qq_official_mode': 'off'})
        self.assertEqual(len(event.sent), 1)
        self.assertFalse(hasattr(event, '_has_send_oper'))

    async def test_compatible_partial_send_or_timeout_consumes_without_repeating(self):
        for responses in ([None, qq.QQRequestError(403)], [asyncio.TimeoutError()]):
            event = Event(send_responses=responses)
            self.assertTrue(await qq.adapt_results(event, self.results(event, [card(), card('blue')]),
                                                  {'qq_official_mode': 'off'}))
            self.assertTrue(event._pokemon_qq_delivery_uncertain)
            self.assertEqual(len(event.sent), len(responses))

    async def test_unknown_components_leave_original_file_chain_to_sdk(self):
        event = Event()
        chain = event.chain_result([Comp.Plain('导出'), Comp.File(name='save.zip', file='save.zip')])
        self.assertFalse(await qq.adapt_results(event, [chain]))
        self.assertEqual(event.sink.calls, [])
        self.assertEqual(event.sent, [])

    async def test_merged_forward_nodes_keep_all_images_and_opponent_keyboard(self):
        event = Event()
        pages = [card('red'), card('blue')]
        nodes = Comp.Nodes(nodes=[Comp.Node(content=[Comp.At(qq='opponent-openid'), Comp.Plain('挑战内容')]),
                                 *[Comp.Node(content=[Comp.Image.fromBytes(data)]) for data in pages]])
        self.assertTrue(await qq.adapt_results(event, [event.chain_result([nodes])], keyboard_kind='challenge'))
        payload = event.sink.messages[0][2]
        self.assertEqual(payload['markdown']['content'].count('qqbot-media://'), 2)
        accept, reject = payload['keyboard']['content']['rows'][0]['buttons'][:2]
        self.assertEqual(accept['action']['permission']['specify_user_ids'], ['opponent-openid'])
        self.assertEqual(reject['action']['permission']['specify_user_ids'], ['opponent-openid'])

    async def test_actual_prefix_overrides_config_and_keyboard_can_be_disabled(self):
        event = Event()
        cfg = {'qq_official_command_prefix': 'old'}
        self.assertTrue(await qq.adapt_results(event, self.results(event), cfg, prefix='/'))
        payload = event.sink.messages[0][2]
        actions = [b['action'] for row in payload['keyboard']['content']['rows'] for b in row['buttons']]
        self.assertTrue(all(action['data'].startswith('/') for action in actions))
        self.assertTrue(all(action['unsupport_tips'].startswith('请手动发送：/') for action in actions))
        event = Event()
        self.assertTrue(await qq.adapt_results(event, self.results(event), {'qq_official_buttons': False}))
        self.assertNotIn('keyboard', event.sink.messages[0][2])

    async def test_template_payload_and_approved_keyboard_id(self):
        event = Event()
        cfg = {'qq_official_mode': 'template', 'qq_official_markdown_template_id': 'template-id',
               'qq_official_markdown_title_key': 'heading', 'qq_official_markdown_body_key': 'details',
               'qq_official_keyboard_id': 'keyboard-id'}
        self.assertTrue(await qq.adapt_results(event, self.results(event), cfg, 'profile'))
        payload = event.sink.messages[0][2]
        self.assertEqual(payload['keyboard'], {'id': 'keyboard-id'})
        self.assertEqual(payload['markdown']['custom_template_id'], 'template-id')
        params = {p['key']: p['values'][0] for p in payload['markdown']['params']}
        self.assertEqual(params, {'heading': '训练家资料', 'details': '完整结果'})

    async def test_missing_template_config_falls_back_without_request(self):
        event = Event()
        self.assertTrue(await qq.adapt_results(event, self.results(event), {'qq_official_mode': 'template'}))
        self.assertEqual(event.sink.messages, [])
        self.assertEqual(len(event.sent), 1)

    async def test_image_digest_mismatch_falls_back_and_does_not_complete(self):
        event = Event()
        with patch.object(qq, '_put_image', AsyncMock(return_value='0'*32)):
            self.assertTrue(await qq.adapt_results(event, self.results(event, [card()])))
        self.assertEqual(event.sink.completed, 0)
        self.assertEqual(len(event.sent), 1)

    async def test_authenticated_http_preserves_status_and_never_retries(self):
        for response in (Response(400, {'err_code': 40034001, 'message': 'PRIVATE RESPONSE'}),
                         Response(200, {'err_code': 40034001, 'message': 'PRIVATE RESPONSE'}),
                         ConnectionResetError()):
            session = Session([response])
            http = SimpleNamespace(check_session=AsyncMock(), _session=session,
                                   _headers={'Authorization': 'PRIVATE TOKEN'}, timeout=10, is_sandbox=True)
            api = SimpleNamespace(_http=http)
            with self.assertRaises((qq.QQRequestError, ConnectionResetError)) as raised:
                await qq._api_request(api, 'POST', '/v2/groups/group/messages', {'markdown': {'content': 'card'}})
            self.assertEqual(len(session.calls), 1)
            self.assertEqual(session.calls[0]['url'], 'https://sandbox.api.sgroup.qq.com/v2/groups/group/messages')
            self.assertFalse(session.calls[0]['allow_redirects'])
            self.assertNotIn('PRIVATE', str(raised.exception))
            if isinstance(response, Response):
                self.assertEqual(raised.exception.status, response.status)
                self.assertEqual(raised.exception.code, 40034001)
                self.assertTrue(qq._definite_rejection(raised.exception))

    async def test_official_api_domain_and_async_review_do_not_repeat(self):
        for status, code in ((201, 304023), (202, 304024)):
            event = Event()
            session = Session([Response(status, {'err_code': code})])
            event.bot.api._http = SimpleNamespace(check_session=AsyncMock(), _session=session,
                                                  _headers={}, timeout=10, is_sandbox=False)
            self.assertTrue(await qq.adapt_results(event, self.results(event)))
            self.assertEqual(session.calls[0]['url'], 'https://api.bot.qq.com/v2/groups/group-openid/messages')
            self.assertEqual(len(session.calls), 1)
            self.assertTrue(event._pokemon_qq_delivery_uncertain)
            self.assertEqual(event.sent, [])

    async def test_jpeg_format_and_ten_megabyte_limit_follow_material_protocol(self):
        event = Event()
        await qq.upload_markdown_image(event, card(format='JPEG'))
        prepared = next(kw['json'] for method, path, kw in event.sink.calls if path.endswith('/prepare'))
        self.assertTrue(prepared['file_name'].endswith('.jpeg'))
        self.assertEqual(prepared['content_type'], 'image/jpeg')
        with self.assertRaises(ValueError):
            await qq.upload_markdown_image(event, b'x'*(10*1024*1024+1))

    async def test_authenticated_successful_nonjson_response_is_uncertain(self):
        event = Event()
        session = Session([Response(200, json_error=ValueError('not JSON'))])
        event.bot.api._http = SimpleNamespace(check_session=AsyncMock(), _session=session,
                                              _headers={}, timeout=10, is_sandbox=False)
        self.assertTrue(await qq.adapt_results(event, self.results(event)))
        self.assertTrue(event._pokemon_qq_delivery_uncertain)
        self.assertEqual(len(session.calls), 1)
        self.assertEqual(event.sent, [])

    async def test_put_preserves_bytes_mime_and_md5_etag(self):
        self.put.stop()
        data = card()
        digest = hashlib.md5(data).hexdigest()
        session = Session([Response(etag='"'+digest.upper()+'"')])
        with patch.object(qq.aiohttp, 'ClientSession', return_value=session):
            self.assertEqual(await qq._put_image('https://upload.test/object', data, 'image/png'), digest)
        self.assertEqual(session.calls[0]['data'], data)
        self.assertEqual(session.calls[0]['headers'], {'Content-Type': 'image/png'})
        self.assertFalse(session.calls[0]['allow_redirects'])
        with self.assertRaises(ValueError):
            await qq._put_image('http://upload.test/object', data, 'image/png')


class PresentationTests(unittest.TestCase):
    def test_keyboard_limits_ownership_and_argument_actions(self):
        for kind in qq.TITLES:
            with self.subTest(kind=kind):
                keyboard = qq.build_keyboard(Event(), kind, prefix='/')
                rows = keyboard['content']['rows']
                self.assertLessEqual(len(rows), 5)
                for row in rows:
                    self.assertLessEqual(len(row['buttons']), 3)
                    for button in row['buttons']:
                        action = button['action']
                        self.assertEqual(action['permission'], {'type': 0, 'specify_user_ids': ['sender-openid']})
                        self.assertEqual(action['type'], 2)
                        self.assertTrue(action['unsupport_tips'])
                        self.assertEqual(action['enter'], not action['data'].endswith(' '))

    def test_challenge_accept_reject_target_and_cancel_initiator(self):
        rows = qq.build_keyboard(Event(), 'challenge', target_user_id='opponent')['content']['rows']
        accept, reject, cancel = rows[0]['buttons']
        self.assertEqual(accept['action']['permission']['specify_user_ids'], ['opponent'])
        self.assertEqual(reject['action']['permission']['specify_user_ids'], ['opponent'])
        self.assertEqual(cancel['action']['permission']['specify_user_ids'], ['sender-openid'])
        self.assertFalse(accept['action']['enter'])
        self.assertTrue(reject['action']['enter'])
        self.assertEqual(accept['action']['data'], '接收挑战 ')

    def test_collection_buttons_navigate_from_current_page(self):
        rows = qq.build_keyboard(Event(text='我的宝可梦 4'), 'collection')['content']['rows']
        self.assertEqual([b['action']['data'] for b in rows[0]['buttons'][:2]], ['我的宝可梦 3', '我的宝可梦 5'])

    def test_draw_keeps_existing_copy_new_status_and_card_first(self):
        text = '小智的今日宝可梦是 炎帝｜SSR·传说（NEW）\n今日剩余 2/3 次｜星尘0｜SSR 保底进度 0/30\n重复宝可梦自动折算星尘。'
        markdown = qq.build_markdown(text, 'draw', images=[('qqbot-media://image/v1/test', 1024, 1600)])
        self.assertIn('小智的今日宝可梦是', markdown)
        self.assertTrue(markdown.startswith('> 小智的今日宝可梦是\n'))
        self.assertIn('**✦ 炎帝｜SSR**（NEW）', markdown.splitlines())
        self.assertNotIn('SSR·传说', markdown)
        self.assertNotIn('# 今日宝可梦\n', markdown)
        self.assertIn('今日剩余 **2/3** 次', markdown)
        self.assertIn('星尘 **0**', markdown)
        self.assertIn('SSR 保底进度 0/30', markdown)
        self.assertLess(markdown.index('qqbot-media://'), markdown.index('今日剩余'))
        self.assertTrue(markdown.endswith('\n'))
        text_only = qq.build_markdown(text, 'draw')
        self.assertIn('**✦ 炎帝｜SSR**（NEW）', text_only)
        self.assertNotIn('qqbot-media://', text_only)

    def test_multi_draw_keeps_each_number_and_all_annotations_compact(self):
        text = '小智的今日宝可梦是：\n1. 炎帝｜SSR·传说（NEW）\n2. 皮卡丘｜C·普通（✦闪光、+6星尘）'
        markdown = qq.build_markdown(text, 'draw')
        self.assertIn('1. **✦ 炎帝｜SSR**（NEW）', markdown)
        self.assertIn('2. **✨ 皮卡丘｜C**（✦闪光、+6星尘）', markdown)
        self.assertNotIn('·普通', markdown)
        self.assertNotIn('·传说', markdown)
        self.assertNotIn('（NEW）**', markdown)
        self.assertNotIn('（✦闪光、+6星尘）**', markdown)

    def test_plain_content_is_escaped_without_format_injection(self):
        markdown = qq.build_markdown('训练家 <script> [点我](https://invalid) **标题**', 'profile')
        self.assertIn(r'\<script\>', markdown)
        self.assertIn(r'\[点我\]', markdown)
        self.assertIn(r'\*\*标题\*\*', markdown)


if __name__ == '__main__':
    unittest.main()
