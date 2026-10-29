"""Bounded image bodies with real consumer paths and no network requests."""
import asyncio
import io
import os
import tempfile
import unittest
from types import SimpleNamespace

os.environ.setdefault('ASTRBOT_ROOT', tempfile.mkdtemp(prefix='image-download-sdk-'))

from PIL import Image
from artwork import ArtworkCache
from image_download import MAX_IMAGE_BYTES, read_image_body
from pokeapi_client import PokeAPIClient


class Response:
    def __init__(self, data=b'', *, repeats=1, declared=None):
        self.data, self.repeats, self.reads = data, repeats, 0
        self.status = 200
        self.headers = {'Content-Type': 'image/png'}
        if declared is not None:
            self.headers['Content-Length'] = declared
        self.content = SimpleNamespace(iter_chunked=self.chunks)

    async def chunks(self, size):
        for _ in range(self.repeats):
            for offset in range(0, len(self.data), size):
                self.reads += 1
                yield self.data[offset:offset + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        raise AssertionError('Image readers must not buffer an unbounded body')


class ImageDownloadTests(unittest.TestCase):
    def test_oversized_declared_body_is_rejected_without_reading(self):
        response = Response(b'not read', declared=str(MAX_IMAGE_BYTES + 1))
        self.assertIsNone(asyncio.run(read_image_body(response)))
        self.assertEqual(response.reads, 0)

    def test_chunked_oversize_is_stopped_before_consuming_the_entire_body(self):
        response = Response(b'x' * 65536, repeats=1000)
        self.assertIsNone(asyncio.run(read_image_body(response)))
        self.assertLess(response.reads, response.repeats)
        self.assertLessEqual(response.reads * 65536, MAX_IMAGE_BYTES + 65536)

    def test_exact_limit_and_invalid_content_length_preserve_complete_data(self):
        response = Response(b'x' * MAX_IMAGE_BYTES, declared='not-a-length')
        self.assertEqual(asyncio.run(read_image_body(response)), response.data)

    def test_empty_body_remains_a_missing_image(self):
        self.assertIsNone(asyncio.run(read_image_body(Response())))

    def test_pokeapi_image_path_uses_the_stream_bound(self):
        response = Response(b'x' * 65536, repeats=1000)
        client = PokeAPIClient()
        client._session = SimpleNamespace(closed=False, get=lambda *args, **kwargs: response)
        self.assertIsNone(asyncio.run(client.fetch_bytes('https://example.test/image.png')))
        self.assertLess(response.reads, response.repeats)

    def test_artwork_oversize_falls_through_to_valid_fallback_source(self):
        raw = io.BytesIO()
        Image.new('RGBA', (16, 16), (50, 100, 200, 255)).save(raw, 'PNG')
        oversized = Response(b'x' * 65536, repeats=1000)
        valid = Response(raw.getvalue())
        responses = iter((oversized, valid))
        session = SimpleNamespace(get=lambda url: next(responses))
        with tempfile.TemporaryDirectory(prefix='image-download-cache-') as directory:
            cache = ArtworkCache(directory)
            self.assertEqual(asyncio.run(cache._download(session, 25)), raw.getvalue())
        self.assertLess(oversized.reads, oversized.repeats)


if __name__ == '__main__':
    unittest.main(verbosity=2)
