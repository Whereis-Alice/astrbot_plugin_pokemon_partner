"""Exact form artwork identity, diagnostic logging and recovery regressions."""
import asyncio
import io
import os
from pathlib import Path
import tempfile
from unittest import TestCase, main
from unittest.mock import AsyncMock, patch

os.environ.setdefault("ASTRBOT_ROOT", tempfile.mkdtemp(prefix="form-artwork-sdk-"))
from PIL import Image
import artwork
from artwork import ArtworkCache, SPRITE_URL, SPRITE_URL_HOME, SPRITE_URL_FALLBACK
from artwork_forms import FORM_SPRITES, sprite_for
from pokedex_db import PokedexDB
from shiny_artwork_tests import Session, Response, png

FORM = {"id": "53", "id_raw": "0053", "name_zh": "猫老大", "name_en": "Persian", "form_name": "阿罗拉猫老大"}
NORMAL, SHINY, BASE = (45, 160, 205, 255), (220, 150, 55, 255), (240, 35, 35, 255)

class FormArtworkTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="form-artwork-")
        self.addCleanup(self.temp.cleanup)
        self.cache = ArtworkCache(Path(self.temp.name) / "cache", allow_download=True)

    def write(self, path, color):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png(color))
        return path

    def pixel(self, row):
        return self.cache.load(row).getpixel((0, 0))

    def test_all_dataset_forms_are_exactly_mapped_or_explicitly_unsupported(self):
        rows = [r for r in PokedexDB().table("pokemon") if r.get("form_name")]
        mapped = [r for r in rows if sprite_for(r) is not None]
        self.assertEqual(len(rows), 295)
        self.assertEqual(len(mapped), 290)
        self.assertEqual(sprite_for(FORM), 10108)
        self.assertEqual(sprite_for({**FORM, "form_name": "不存在的地区形态"}), None)
        self.assertEqual(sprite_for({"id": "26", "form_name": "阿罗拉的样子"}), 10100)
        self.assertEqual(sprite_for({"id": "128", "form_name": "帕底亚的样子"}), None)
        for raw in (True, 0, -1, "../53", "1.0", "５３"):
            self.assertIsNone(sprite_for({**FORM, "id": raw}))

    def test_downloads_keep_default_alola_and_shiny_in_separate_caches(self):
        normal = dict(FORM, form_name="")
        shiny = dict(FORM, _artwork_shiny=True)
        calls = []
        async def download(session, sprite, *, shiny=False):
            calls.append((sprite, shiny))
            return png(SHINY if shiny else NORMAL if sprite == 10108 else BASE)
        with patch("aiohttp.ClientSession", return_value=Session()), patch.object(self.cache, "_download", side_effect=download):
            self.assertEqual(asyncio.run(self.cache.prefetch([normal, FORM, shiny, FORM])), 3)
        self.assertEqual(calls, [(53, False), (10108, False), (10108, True)])
        self.assertEqual(self.cache.path_for(FORM), self.cache.cache_dir / "forms/10108.png")
        self.assertEqual(self.cache.path_for(shiny), self.cache.cache_dir / "shiny/forms/10108.png")
        self.assertEqual(self.pixel(normal), BASE)
        self.assertEqual(self.pixel(FORM), NORMAL)
        self.assertEqual(self.pixel(shiny), SHINY)
        self.assertEqual(self.cache.cached_count(), 3)

    def test_forms_never_borrow_default_images_and_missing_reason_is_logged_once(self):
        self.cache.allow_download = False
        self.write(self.cache.cache_dir / "0053.png", BASE)
        with patch.object(artwork.logger, "warning") as warning:
            self.assertIsNone(self.cache.load(FORM))
            self.assertIsNone(self.cache.load(FORM))
        warning.assert_called_once()
        message = warning.call_args.args[0]
        for value in ("0053", "猫老大", "阿罗拉猫老大", "10108", "fallback_web_image=false", "cache_dir="):
            self.assertIn(value, message)

    def test_unknown_form_keeps_missing_and_does_not_claim_base_art(self):
        unknown = dict(FORM, form_name="不存在的形态")
        self.write(self.cache.cache_dir / "0053.png", BASE)
        with patch.object(artwork.logger, "warning") as warning:
            self.assertIsNone(self.cache.load(unknown))
        self.assertIn("不会使用基础形态立绘", warning.call_args.args[0])
        self.assertEqual(self.cache.missing([unknown]), [])

    def test_named_cosmetic_form_uses_the_exact_sprite_suffix(self):
        row = {"id": "412", "name_zh": "结草儿", "form_name": "砂土蓑衣"}
        self.assertEqual(sprite_for(row), "412-sandy")
        url = SPRITE_URL.format(dex="412-sandy")
        session = Session({url: png(NORMAL)})
        with patch("aiohttp.ClientSession", return_value=session):
            self.assertEqual(asyncio.run(self.cache.prefetch([row])), 1)
        self.assertEqual(session.urls, [url])
        self.assertEqual(self.cache.path_for(row), self.cache.cache_dir / "forms/412-sandy.png")

    def test_female_form_uses_home_female_subdirectory_and_a_flat_independent_cache(self):
        row = {"id": "521", "name_zh": "高傲雉鸡", "form_name": "雌性的样子"}
        self.assertEqual(sprite_for(row), "female/521")
        url = SPRITE_URL_HOME.format(dex="female/521")
        session = Session({url: png(NORMAL)})
        with patch("aiohttp.ClientSession", return_value=session):
            self.assertEqual(asyncio.run(self.cache.prefetch([row])), 1)
        self.assertEqual(session.urls, [SPRITE_URL.format(dex="female/521"), url])
        self.assertEqual(self.cache.path_for(row), self.cache.cache_dir / "forms/female-521.png")
        self.assertEqual(self.cache.cached_count(), 1)
        self.assertEqual(self.cache.cleanup_cache(keep=[521]), 0)
        self.assertEqual(self.cache.cleanup_cache(keep=[]), 1)

    def test_form_shiny_fallback_reads_only_its_own_existing_normal_art(self):
        self.write(self.cache.cache_dir / "0053.png", BASE)
        self.write(self.cache.cache_dir / "forms/10108.png", NORMAL)
        shiny = dict(FORM, _artwork_shiny=True)
        self.assertIsNone(self.cache.path_for(shiny))
        self.assertEqual(self.pixel(shiny), NORMAL)
        self.assertEqual(self.cache.missing([shiny]), [(10108, self.cache.cache_dir / "shiny/forms/10108.png")])

    def test_source_errors_log_identity_url_status_and_are_not_retried_each_card(self):
        self.cache.missing([FORM])
        session = Session()
        with patch.object(artwork.logger, "warning") as warning:
            self.assertIsNone(asyncio.run(self.cache._download(session, 10108)))
            self.assertIsNone(asyncio.run(self.cache._download(session, 10108)))
        self.assertEqual(len(session.urls), 3)
        warning.assert_called_once()
        message = warning.call_args.args[0]
        for value in ("猫老大", "阿罗拉猫老大", "0053", "10108", "HTTP 404", "official-artwork", "home", "300 秒"):
            self.assertIn(value, message)

    def test_bad_content_and_empty_responses_explain_the_failure(self):
        self.cache.missing([FORM])
        nonimage = Response(png(NORMAL))
        nonimage.headers["Content-Type"] = "text/html"
        responses = [Response(status=503), nonimage, Response(status=200)]
        session = Session()
        with patch.object(session, "get", side_effect=responses), patch.object(artwork.logger, "warning") as warning:
            self.assertIsNone(asyncio.run(self.cache._download(session, 10108)))
        message = warning.call_args.args[0]
        for value in ("HTTP 503", "非图片 Content-Type=text/html", "图片为空或超过 8 MiB"):
            self.assertIn(value, message)

    def test_retry_after_cooldown_can_recover_and_clear_previous_failure(self):
        now = [1000.0]
        with patch.object(artwork.time, "monotonic", side_effect=lambda: now[0]):
            self.assertIsNone(asyncio.run(self.cache._download(Session(), 10108)))
            now[0] += 301
            session = Session({SPRITE_URL_HOME.format(dex=10108): png(NORMAL)})
            self.assertEqual(asyncio.run(self.cache._download(session, 10108)), png(NORMAL))
        self.assertNotIn(("10108", False), self.cache._download_failures)

    def test_corrupt_cache_is_logged_once_and_repaired(self):
        target = self.cache._cache_path(FORM)
        target.parent.mkdir(parents=True)
        target.write_bytes(b"not an image")
        with patch.object(artwork.logger, "warning") as warning:
            self.assertIsNone(self.cache.load(FORM))
            self.assertIsNone(self.cache.load(FORM))
        corruption = [c.args[0] for c in warning.call_args_list if "缓存损坏" in c.args[0]]
        self.assertEqual(len(corruption), 1)
        self.assertIn("猫老大", corruption[0])
        with patch("aiohttp.ClientSession", return_value=Session()), patch.object(self.cache, "_download", new=AsyncMock(return_value=png(NORMAL))):
            self.assertEqual(asyncio.run(self.cache.prefetch([FORM])), 1)
        self.assertEqual(self.pixel(FORM), NORMAL)

    def test_cache_write_failure_is_diagnosed_with_the_form(self):
        with patch("aiohttp.ClientSession", return_value=Session()), patch.object(self.cache, "_download", new=AsyncMock(return_value=png(NORMAL))), patch.object(self.cache, "_save_download", side_effect=PermissionError("denied")), patch.object(artwork.logger, "warning") as warning:
            self.assertEqual(asyncio.run(self.cache.prefetch([FORM])), 0)
        self.assertIn("缓存写入失败", warning.call_args.args[0])
        self.assertIn("阿罗拉猫老大", warning.call_args.args[0])
        self.assertIn("PermissionError", warning.call_args.args[0])

    def test_bulk_download_counts_same_species_forms_and_unsupported_correctly(self):
        other = {"id": "26", "name_zh": "雷丘", "form_name": "阿罗拉雷丘"}
        mega = {**other, "form_name": "超级雷丘Ｘ"}
        unknown = {**other, "form_name": "未知形态"}
        with patch("aiohttp.ClientSession", return_value=Session()), patch.object(self.cache, "_download", new=AsyncMock(return_value=png(NORMAL))):
            result = asyncio.run(self.cache.download_all([other, mega, other, unknown]))
        self.assertEqual(result, {"total": 3, "saved": 2, "skipped": 0, "failed": 1})
        self.assertEqual(self.cache.cached_count(), 2)
        self.assertEqual(self.cache.cleanup_cache(keep=[26]), 0)
        self.assertEqual(self.cache.cleanup_cache(keep=[]), 2)

    def test_large_prefetch_and_bulk_restore_the_download_identity_after_lru_eviction(self):
        rows = [{"id": str(i), "name_zh": f"名称{i}"} for i in range(1, artwork.MAX_FAILURE_REASONS + 2)]
        first_warning = []
        def warn(message):
            if "素材ID=1)" in message or "素材ID=1）" in message:
                first_warning.append(message)
        with patch("aiohttp.ClientSession", return_value=Session()), patch.object(artwork.logger, "warning", side_effect=warn):
            self.assertEqual(asyncio.run(self.cache.prefetch(rows, limit=1)), 0)
        self.assertTrue(first_warning)
        self.assertIn("名称1", first_warning[0])
        self.cache.clear_cache()
        first_warning.clear()
        with patch("aiohttp.ClientSession", return_value=Session()), patch.object(artwork.logger, "warning", side_effect=warn):
            result = asyncio.run(self.cache.download_all(rows))
        self.assertEqual(result["failed"], len(rows))
        self.assertTrue(first_warning)
        self.assertIn("名称1", first_warning[0])

if __name__ == "__main__":
    main(verbosity=2)
