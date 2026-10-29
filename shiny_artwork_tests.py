"""Real shiny artwork selection and isolated cache/prefetch regressions."""

import ast
import asyncio
import io
import logging
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="shiny-artwork-astrbot-"))

from PIL import Image

from artwork import ArtworkCache, SHINY_SPRITE_URL, SHINY_SPRITE_URL_HOME
from pokedex_db import PokedexDB


NORMAL = (230, 80, 140, 255)
SHINY = (60, 165, 245, 255)
LOCAL = (45, 210, 120, 255)
ROW = {"id": "151", "id_raw": "0151", "name_zh": "梦幻", "form_name": ""}


def png(color):
    output = io.BytesIO()
    Image.new("RGBA", (32, 32), color).save(output, "PNG")
    return output.getvalue()


class Response:
    def __init__(self, data=None, status=None):
        self.data = data
        self.status = status if status is not None else (200 if data else 404)
        self.headers = {"Content-Type": "image/png"}
        self.content = SimpleNamespace(iter_chunked=self.chunks)

    async def chunks(self, size):
        for start in range(0, len(self.data or b""), size):
            yield self.data[start:start + size]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def read(self):
        return self.data


class Session:
    def __init__(self, images=None):
        self.images, self.urls = images or {}, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def get(self, url):
        self.urls.append(url)
        return Response(self.images.get(url))


class ShinyArtworkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="shiny-artwork-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = ArtworkCache(self.root / "cache", allow_download=False)
        self.shiny_row = dict(ROW, _artwork_shiny=True)

    def write(self, path, color):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png(color))
        return path

    def pixel(self, row):
        image = self.cache.load(row)
        return image.getpixel((0, 0)) if image else None

    def test_normal_and_shiny_cache_pixels_and_lru_keys_are_independent(self):
        self.write(self.cache.cache_dir / "0151.png", NORMAL)
        self.write(self.cache.cache_dir / "shiny" / "0151.png", SHINY)
        self.assertEqual(self.pixel(ROW), NORMAL)
        self.assertEqual(self.pixel(self.shiny_row), SHINY)
        self.assertEqual(self.cache.cache_size(), 2)
        self.assertEqual(self.cache.cached_count(), 2)
        self.assertNotIn("_artwork_shiny", ROW)

    def test_shiny_missing_can_read_existing_normal_without_becoming_shiny_cache(self):
        normal = self.write(self.cache.cache_dir / "0151.png", NORMAL)
        self.assertIsNone(self.cache.path_for(self.shiny_row))
        self.assertEqual(self.pixel(self.shiny_row), NORMAL)
        self.assertEqual(self.cache.path_for(ROW), normal)
        self.assertEqual(
            self.cache.missing([self.shiny_row]),
            [(151, self.cache.cache_dir / "shiny" / "0151.png")],
        )

    def test_local_shiny_directory_has_priority_over_independent_cache(self):
        images = self.root / "images"
        self.cache.images_dir = images
        local = self.write(images / "shiny" / "0151.png", LOCAL)
        self.write(self.cache.cache_dir / "shiny" / "0151.png", SHINY)
        self.assertEqual(self.cache.path_for(self.shiny_row), local)
        self.assertEqual(self.pixel(self.shiny_row), LOCAL)
        self.assertEqual(self.cache.missing([self.shiny_row]), [])

    def test_dataset_shiny_lookup_uses_shiny_suffix_and_normal_lookup_remains_normal(
        self,
    ):
        self.cache.images_dir = self.root / "dataset"
        self.cache.db = PokedexDB()
        self.write(self.cache.images_dir / "home" / "0151-梦幻.png", NORMAL)
        self.write(self.cache.images_dir / "home" / "0151-梦幻-shiny.png", SHINY)
        self.assertEqual(self.pixel(self.shiny_row), SHINY)
        self.assertEqual(self.pixel(ROW), NORMAL)

    def test_missing_deduplicates_each_variant_and_rejects_invalid_ids(self):
        todo = self.cache.missing([ROW, self.shiny_row, ROW, self.shiny_row])
        self.assertEqual(
            todo,
            [
                (151, self.cache.cache_dir / "0151.png"),
                (151, self.cache.cache_dir / "shiny" / "0151.png"),
            ],
        )
        for value in (0, -1, True, "../151", "1.5", 1.5, "²"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.cache.missing([{**ROW, "id": value, "_artwork_shiny": True}]),
                    [],
                )

    def test_forms_require_local_exact_art_and_do_not_use_national_number_cache(self):
        form = {
            "id": "26",
            "id_raw": "0026",
            "name_zh": "雷丘",
            "form_name": "阿罗拉的样子",
        }
        shiny = dict(form, _artwork_shiny=True)
        self.write(self.cache.cache_dir / "0026.png", NORMAL)
        self.write(self.cache.cache_dir / "shiny" / "0026.png", SHINY)
        self.cache.images_dir = self.root / "images"
        self.cache.db = PokedexDB()
        self.write(self.cache.images_dir / "shiny" / "0026.png", LOCAL)
        self.assertIsNone(self.cache.load(form))
        self.assertIsNone(self.cache.load(shiny))
        self.assertEqual(self.cache.missing([form, shiny]), [])
        self.write(
            self.cache.images_dir / "home" / "0026-雷丘-阿罗拉的样子-shiny.png", SHINY
        )
        self.assertEqual(self.pixel(shiny), SHINY)

    def test_disabled_download_preserves_cached_shiny_and_never_requests_network(self):
        self.write(self.cache.cache_dir / "shiny" / "0151.png", SHINY)
        with patch.object(self.cache, "_download", new=AsyncMock()) as download:
            self.assertEqual(asyncio.run(self.cache.prefetch([self.shiny_row])), 0)
            download.assert_not_awaited()
        self.assertEqual(self.pixel(self.shiny_row), SHINY)

    def test_prefetch_downloads_and_commits_variants_separately(self):
        self.cache.allow_download = True
        calls = []

        async def download(session, dex, *, shiny=False):
            calls.append((dex, shiny))
            return png(SHINY if shiny else NORMAL)

        with (
            patch("aiohttp.ClientSession", return_value=Session()),
            patch.object(self.cache, "_download", side_effect=download),
        ):
            saved = asyncio.run(
                self.cache.prefetch([self.shiny_row, ROW, self.shiny_row, ROW])
            )
        self.assertEqual(saved, 2)
        self.assertEqual(calls, [(151, True), (151, False)])
        self.assertEqual(self.pixel(self.shiny_row), SHINY)
        self.assertEqual(self.pixel(ROW), NORMAL)
        self.assertEqual(list(self.cache.cache_dir.rglob("*.tmp")), [])

    def test_corrupt_shiny_cache_falls_back_and_is_repaired_independently(self):
        self.write(self.cache.cache_dir / "0151.png", NORMAL)
        target = self.cache.cache_dir / "shiny" / "0151.png"
        target.parent.mkdir()
        target.write_bytes(b"corrupt")
        self.assertEqual(self.pixel(self.shiny_row), NORMAL)
        self.cache.allow_download = True
        with (
            patch("aiohttp.ClientSession", return_value=Session()),
            patch.object(
                self.cache, "_download", new=AsyncMock(return_value=png(SHINY))
            ),
        ):
            self.assertEqual(asyncio.run(self.cache.prefetch([self.shiny_row])), 1)
        self.assertEqual(self.pixel(self.shiny_row), SHINY)
        self.assertEqual(self.pixel(ROW), NORMAL)

    def test_download_uses_shiny_official_then_shiny_home_with_exact_id(self):
        official = SHINY_SPRITE_URL.format(dex=151)
        home = SHINY_SPRITE_URL_HOME.format(dex=151)
        session = Session({home: png(SHINY)})
        data = asyncio.run(self.cache._download(session, 151, shiny=True))
        self.assertEqual(data, png(SHINY))
        self.assertEqual(session.urls, [official, home])
        self.assertTrue(all("/shiny/151.png" in url for url in session.urls))

    def test_missing_shiny_sources_never_download_ordinary_into_shiny_cache(self):
        session = Session()
        self.assertIsNone(asyncio.run(self.cache._download(session, 151, shiny=True)))
        self.assertEqual(len(session.urls), 3)
        self.assertTrue(all("/shiny/151.png" in url for url in session.urls))

    def test_download_cancellation_propagates(self):
        session = SimpleNamespace(
            get=lambda url: (_ for _ in ()).throw(asyncio.CancelledError())
        )
        with self.assertRaises(asyncio.CancelledError):
            asyncio.run(self.cache._download(session, 151, shiny=True))

    def test_bulk_download_and_cleanup_keep_variant_identity(self):
        self.cache.allow_download = True

        async def download(session, dex, *, shiny=False):
            return png(SHINY if shiny else NORMAL)

        with (
            patch("aiohttp.ClientSession", return_value=Session()),
            patch.object(self.cache, "_download", side_effect=download),
        ):
            result = asyncio.run(self.cache.download_all([ROW, self.shiny_row, ROW]))
        self.assertEqual(result, {"total": 2, "saved": 2, "skipped": 0, "failed": 0})
        self.assertEqual(self.cache.cleanup_cache(keep=[151]), 0)
        self.assertEqual(self.cache.cleanup_cache(keep=[]), 2)
        self.assertEqual(self.cache.cached_count(), 0)

    def test_draw_prefetch_copies_shiny_row_and_includes_normal_fallback(self):
        tree = ast.parse(
            (Path(__file__).parent / "main.py").read_text(encoding="utf-8-sig")
        )
        plugin = next(
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "PokemonPartnerPlugin"
        )
        method = next(
            node
            for node in plugin.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_send_draw_card"
        )
        namespace = {
            "asyncio": asyncio,
            "logger": logging.getLogger(__name__),
            "render_draw_card": lambda *args, **kwargs: b"rendered",
            "PLUGIN_ID": "test",
            "Comp": SimpleNamespace(
                Image=SimpleNamespace(fromBytes=lambda value: value)
            ),
        }
        exec(
            compile(
                ast.Module(body=[method], type_ignores=[]),
                "draw-prefetch-under-test",
                "exec",
            ),
            namespace,
        )
        regular = {"id": "25", "name_zh": "皮卡丘"}
        items = [
            SimpleNamespace(row=ROW, shiny=True),
            SimpleNamespace(row=regular, shiny=False),
        ]
        service = SimpleNamespace(
            recovery_notice=lambda: "",
            artwork=lambda row: None,
            store=SimpleNamespace(rules=SimpleNamespace(ssr_pity_after=10)),
        )
        ensure = AsyncMock()
        instance = SimpleNamespace(
            config={},
            _partner_or_error=lambda: service,
            _card_enabled=lambda: True,
            _ensure_artwork=ensure,
        )
        event = SimpleNamespace(chain_result=lambda chain: chain)
        outcome = SimpleNamespace(
            items=items,
            remaining=1,
            daily_limit=2,
            dust=0,
            pity_ssr=0,
            capped=False,
            milestones=[],
        )

        async def collect():
            return [
                value
                async for value in namespace["_send_draw_card"](
                    instance, event, outcome, "训练家"
                )
            ]

        self.assertEqual(asyncio.run(collect()), [[b"rendered"]])
        requests = ensure.await_args.args[0]
        self.assertEqual(requests, [self.shiny_row, ROW, regular])
        self.assertIsNot(requests[0], ROW)
        self.assertNotIn("_artwork_shiny", ROW)


if __name__ == "__main__":
    unittest.main(verbosity=2)
