"""宝可梦立绘获取与本地缓存。

优先级：

1. 配置的 ``images_dir``（pokemon-dataset-zh 的 ``data/images``）；
2. 插件数据目录 ``artwork/`` 下已缓存的官方图；
3. 可选的 PokéAPI 官方图下载（``fallback_web_image`` 控制）。

闪光请求只在行副本中携带 ``_artwork_shiny=True``，优先本地闪光图、独立
``shiny/`` 缓存及官方闪光图源；缺图可读取已存在的普通图。

渲染路径要求同步返回 ``PIL.Image``，所以下载由 :meth:`ArtworkCache.prefetch`
在抽卡前批量完成（异步），渲染时只读缓存。
"""

from __future__ import annotations

import asyncio
import io
import os
import threading
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image

try:
    from .artwork_forms import FORM_SPRITES, sprite_for, unmapped_form_reason
    from .image_download import read_image_body
except ImportError:
    from artwork_forms import FORM_SPRITES, sprite_for, unmapped_form_reason
    from image_download import read_image_body

try:
    from astrbot.api import logger
except ImportError:  # 脱离 AstrBot 环境单测
    import logging

    logger = logging.getLogger(__name__)

SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/other/official-artwork/{dex}.png"
)
SPRITE_URL_HOME = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/other/home/{dex}.png"
)
SPRITE_URL_FALLBACK = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{dex}.png"
)
MAX_CACHED_IMAGES = 160
DOWNLOAD_CONCURRENCY = 8
FAILURE_RETRY_SECONDS = 300
MAX_FAILURE_REASONS = 512
SHINY_SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/other/official-artwork/shiny/{dex}.png"
)
SHINY_SPRITE_URL_HOME = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/other/home/shiny/{dex}.png"
)
SHINY_SPRITE_URL_FALLBACK = "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/shiny/{dex}.png"


class ArtworkCache:
    """按图鉴编号缓存官方立绘，避免每次渲染都读盘/联网。"""

    def __init__(
        self,
        cache_dir: str | Path,
        *,
        images_dir: str | Path | None = None,
        db: Any = None,
        allow_download: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.images_dir = Path(images_dir) if images_dir else None
        self.db = db
        self.allow_download = allow_download
        self.timeout = timeout
        self._images: OrderedDict[str, Image.Image] = OrderedDict()
        self._failed: set[str] = set()
        self._lock = threading.RLock()
        self._verified: dict[str, tuple[int, int, bool]] = {}
        self._warnings: OrderedDict[str, float] = OrderedDict()
        self._download_failures: OrderedDict[tuple[str, bool], tuple[float, str]] = OrderedDict()
        self._contexts: OrderedDict[tuple[str, bool], Mapping[str, Any]] = OrderedDict()
        self._path_contexts: OrderedDict[str, Mapping[str, Any]] = OrderedDict()
        self.max_images = MAX_CACHED_IMAGES

    def _warn_once(self, key: str, message: str) -> None:
        """Keep missing assets diagnosable without logging on every render."""
        now = time.monotonic()
        with self._lock:
            previous = self._warnings.get(key)
            if previous is not None and now - previous < FAILURE_RETRY_SECONDS:
                return
            self._warnings[key] = now
            self._warnings.move_to_end(key)
            while len(self._warnings) > MAX_FAILURE_REASONS:
                self._warnings.popitem(last=False)
        logger.warning(message)

    @staticmethod
    def _describe(row: Mapping[str, Any] | None, sprite=None, shiny=False) -> str:
        row = row or {}
        number = row.get("id_raw") or row.get("id") or sprite or "未知"
        name = row.get("name_zh") or row.get("name_en") or "未知宝可梦"
        form = row.get("form_name") or "默认形态"
        variant = "闪光" if row.get("_artwork_shiny") is True or shiny else "普通"
        return f"#{number} {name} / {form} / {variant}（素材ID={sprite or '未匹配'}）"

    def _is_shiny_target(self, target: Path) -> bool:
        return target.relative_to(self.cache_dir).parts[0] == "shiny"

    def _remember_context(self, path: Path, row: Mapping[str, Any], sprite=None) -> None:
        with self._lock:
            self._path_contexts[str(path)] = dict(row)
            self._path_contexts.move_to_end(str(path))
            while len(self._path_contexts) > MAX_FAILURE_REASONS:
                self._path_contexts.popitem(last=False)
            if sprite is not None:
                key = (str(sprite), self._is_shiny(row))
                self._contexts[key] = dict(row)
                self._contexts.move_to_end(key)
                while len(self._contexts) > MAX_FAILURE_REASONS:
                    self._contexts.popitem(last=False)

    def _missing_reason(self, row: Mapping[str, Any]) -> str:
        sprite = sprite_for(row)
        if sprite is None:
            return unmapped_form_reason(row)
        if not self.allow_download:
            return "本地与缓存未找到可用立绘，fallback_web_image=false 已关闭网络补图"
        key = (str(sprite), self._is_shiny(row))
        with self._lock:
            failure = self._download_failures.get(key)
        return failure[1] if failure else "本地与缓存未找到可用立绘，尚未成功预取"

    # ---------- 同步读取（渲染线程） ----------
    @staticmethod
    def _is_shiny(row: Mapping[str, Any]) -> bool:
        """仅内部请求副本携带此标记，图鉴和业务存档保持原样。"""
        return row.get("_artwork_shiny") is True

    def _local_path(self, row: Mapping[str, Any]) -> Path | None:
        shiny = self._is_shiny(row)
        if shiny and self.images_dir is not None and not row.get("form_name"):
            name = self._cache_name(row.get("id"))
            candidate = self.images_dir / "shiny" / name if name else None
            if candidate is not None and self._valid_cache_file(candidate):
                return candidate
        if self.db is not None and self.images_dir is not None:
            try:
                if shiny:
                    found = self.db.find_image(dict(row), self.images_dir, shiny=True)
                else:
                    found = self.db.find_image(dict(row), self.images_dir)
            except Exception:
                found = None
            if found:
                path = Path(found)
                self._remember_context(path, row)
                return path
        return None

    @staticmethod
    def _cache_name(dex: Any) -> str:
        try:
            if isinstance(dex, bool) or not str(dex).strip().isdecimal():
                return ""
            value = int(dex)
            return f"{value:04d}.png" if value > 0 else ""
        except (TypeError, ValueError):
            return ""

    def _cache_path(self, row: Mapping[str, Any]) -> Path | None:
        sprite = sprite_for(row)
        if sprite is None:
            return None
        folder = self.cache_dir / "shiny" if self._is_shiny(row) else self.cache_dir
        path = (
            folder / "forms" / (str(sprite).replace("/", "-") + ".png")
            if row.get("form_name")
            else folder / self._cache_name(sprite)
        )
        self._remember_context(path, row, sprite)
        return path

    def path_for(self, row: Mapping[str, Any]) -> Path | None:
        """返回请求版本的真实图路径；此层不把普通图当作闪光缓存。"""
        local = self._local_path(row)
        if local is not None and self._valid_cache_file(local):
            return local
        cached = self._cache_path(row)
        return cached if cached is not None and self._valid_cache_file(cached) else None

    def _valid_cache_file(self, path: Path) -> bool:
        """仅在文件变化时验证 PNG，损坏缓存视为缺图并允许重新下载。"""
        try:
            stat = path.stat()
            signature = (stat.st_mtime_ns, stat.st_size)
            key = str(path)
            with self._lock:
                previous = self._verified.get(key)
                if previous and previous[:2] == signature:
                    return previous[2]
            try:
                with Image.open(path) as image:
                    image.verify()
                valid = True
            except Exception as exc:
                valid = False
                with self._lock:
                    row = self._path_contexts.get(key)
                self._warn_once(
                    "corrupt:" + key,
                    f"[宝可梦立绘] 缓存损坏 {self._describe(row)}：{path}；{type(exc).__name__}: {exc}；将尝试重新补图",
                )
            with self._lock:
                if previous and previous[:2] != signature:
                    self._images.pop(key, None)
                    self._failed.discard(key)
                self._verified[key] = (*signature, valid)
            return valid
        except OSError:
            return False

    def load(self, row: Mapping[str, Any]) -> Image.Image | None:
        """同步加载；缺少真实闪光图时可读取已有普通图，不联网或伪造染色。"""
        path = self.path_for(row)
        if path is None and self._is_shiny(row):
            normal_row = dict(row)
            normal_row.pop("_artwork_shiny", None)
            path = self.path_for(normal_row)
        key = str(path or "")
        if not path:
            identity = f"{row.get('id')}:{row.get('form_name', '')}:{self._is_shiny(row)}"
            self._warn_once(
                "missing:" + identity,
                f"[宝可梦立绘] 缺图 {self._describe(row, sprite_for(row))}：{self._missing_reason(row)}；"
                f"已检查 images_dir={self.images_dir or '未配置'}、cache_dir={self.cache_dir}",
            )
            return None
        with self._lock:
            cached = self._images.get(key)
            if cached is not None:
                self._images.move_to_end(key)
                return cached
        try:
            image = Image.open(path).convert("RGBA")
            image.load()
        except Exception as exc:
            with self._lock:
                self._failed.add(key)
            self._warn_once(
                "read:" + key,
                f"[宝可梦立绘] 读取失败 {self._describe(row, sprite_for(row))}：{path}；{type(exc).__name__}: {exc}",
            )
            return None
        with self._lock:
            self._images[key] = image
            # 有界 LRU：长期运行时不会把上千张立绘常驻内存。
            while len(self._images) > self.max_images:
                self._images.popitem(last=False)
        return image

    def clear_cache(self) -> None:
        with self._lock:
            self._images.clear()
            self._failed.clear()
            self._verified.clear()
            self._warnings.clear()
            self._download_failures.clear()
            self._contexts.clear()
            self._path_contexts.clear()

    def cache_size(self) -> int:
        """当前常驻内存的立绘数量（调试与测试用）。"""
        with self._lock:
            return len(self._images)

    # ---------- 异步预取 ----------
    def missing(self, rows: Iterable[Mapping[str, Any]]) -> list[tuple[int | str, Path]]:
        out: list[tuple[int | str, Path]] = []
        seen = set()
        for row in rows:
            local = self._local_path(row)
            if local is not None and self._valid_cache_file(local):
                continue
            target = self._cache_path(row)
            if target is None:
                continue
            key = str(target)
            if key not in seen and not self._valid_cache_file(target):
                out.append((sprite_for(row), target))
                seen.add(key)
        return out

    def _save_download(self, target: Path, data: bytes) -> None:
        """同目录原子提交，防止并行读取或取消任务留下半张图。"""
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_bytes(data)
            os.replace(temporary, target)
            with self._lock:
                key = str(target)
                self._images.pop(key, None)
                self._failed.discard(key)
                self._verified.pop(key, None)
        finally:
            temporary.unlink(missing_ok=True)

    async def prefetch(
        self,
        rows: Iterable[Mapping[str, Any]],
        limit: int = 30,
        *,
        wait: bool = True,
    ) -> int:
        """下载缺失立绘，返回成功数量。

        Args:
            rows: 需要立绘的图鉴行。
                闪光行副本可携带 ``_artwork_shiny=True``。
            limit: 单次最多下载数量。
            wait: 为 ``True`` 时等全部下载完成（抽卡前需要）；
                为 ``False`` 时只发起后台任务（图鉴浏览等场景）。
        """
        if not self.allow_download:
            # Render-time logging will report the individual missing identity.
            return 0
        rows = tuple(rows)
        todo = await asyncio.to_thread(lambda: self.missing(rows)[: max(0, limit)])
        if not todo:
            return 0
        contexts = {str(target): row for row in rows
                    if (target := self._cache_path(row)) is not None}
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import aiohttp
        except ImportError:
            self._warn_once("aiohttp", "[宝可梦立绘] 网络补图失败：未安装 aiohttp，无法请求官方素材源")
            return 0
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        async def run_one(session, sprite, target):
            async with semaphore:
                self._remember_context(target, contexts[str(target)], sprite)
                if self._is_shiny_target(target):
                    return await self._download(session, sprite, shiny=True)
                return await self._download(session, sprite)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = await asyncio.gather(
                *(run_one(session, dex, target) for dex, target in todo),
                return_exceptions=True,
            )
        saved = 0
        for (_dex, target), data in zip(todo, results):
            if isinstance(data, BaseException):
                if isinstance(data, asyncio.CancelledError):
                    raise data
                self._warn_once(
                    "prefetch:" + str(target),
                    f"[宝可梦立绘] 预取失败 {self._describe(contexts.get(str(target)), _dex)}：{type(data).__name__}: {data}",
                )
                continue
            if data is None:
                continue
            try:
                await asyncio.to_thread(self._save_download, target, data)
                saved += 1
            except OSError as exc:
                self._warn_once(
                    "write:" + str(target),
                    f"[宝可梦立绘] 缓存写入失败 {self._describe(contexts.get(str(target)), _dex)}：{target}；{type(exc).__name__}: {exc}",
                )
                continue
        return saved

    async def _download(
        self, session, dex: int | str, *, shiny: bool = False
    ) -> bytes | None:
        """依次尝试对应版本的高清、HOME 与精灵图，闪光图不混入普通缓存。"""
        sources = (
            (SHINY_SPRITE_URL, SHINY_SPRITE_URL_HOME, SHINY_SPRITE_URL_FALLBACK)
            if shiny
            else (SPRITE_URL, SPRITE_URL_HOME, SPRITE_URL_FALLBACK)
        )
        key = (str(dex), shiny)
        with self._lock:
            previous = self._download_failures.get(key)
            row = self._contexts.get(key)
        if previous and time.monotonic() - previous[0] < FAILURE_RETRY_SECONDS:
            return None
        reasons = []
        for url in sources:
            url = url.format(dex=dex)
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        reasons.append(f"{url} -> HTTP {resp.status}")
                        continue
                    content_type = resp.headers.get("Content-Type", "")
                    if "image" not in content_type.lower():
                        reasons.append(f"{url} -> 非图片 Content-Type={content_type or '空'}")
                        continue
                    data = await read_image_body(resp)
                    if data is None:
                        reasons.append(f"{url} -> 图片为空或超过 8 MiB")
                        continue
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                with self._lock:
                    self._download_failures.pop(key, None)
                return data
            except asyncio.CancelledError:
                self._warn_once(
                    "cancelled:" + str(key),
                    f"[宝可梦立绘] 预取请求已取消 {self._describe(row, dex, shiny)}：{url}；可能超过抽卡预取等待上限，缓存保留并允许下次重试",
                )
                raise
            except Exception as exc:
                reasons.append(f"{url} -> {type(exc).__name__}: {exc}")
        reason = "；".join(reasons)
        with self._lock:
            self._download_failures[key] = (time.monotonic(), reason)
            self._download_failures.move_to_end(key)
            while len(self._download_failures) > MAX_FAILURE_REASONS:
                self._download_failures.popitem(last=False)
        self._warn_once(
            "download:" + str(key),
            f"[宝可梦立绘] 下载失败 {self._describe(row, dex, shiny)}：{reason}；{FAILURE_RETRY_SECONDS} 秒内不重复请求",
        )
        return None

    def _cache_files(self) -> Iterable[Path]:
        for folder in (self.cache_dir, self.cache_dir / "forms", self.cache_dir / "shiny", self.cache_dir / "shiny/forms"):
            yield from folder.glob("*.png")

    def cleanup_cache(self, keep: Iterable[int] | None = None) -> int:
        """删除不再需要的缓存图，返回删除数量。"""
        if keep is None:
            return 0
        dexes = {int(dex) for dex in keep if self._cache_name(dex)}
        wanted = {self._cache_name(dex) for dex in dexes}
        wanted_forms = {str(sprite).replace("/", "-") + ".png" for (dex, _form), sprite in FORM_SPRITES.items() if dex in dexes}
        removed = 0
        if not self.cache_dir.is_dir():
            return 0
        for path in self._cache_files():
            names = wanted_forms if path.parent.name == "forms" else wanted
            if path.name not in names:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    def cached_count(self) -> int:
        """已缓存的本地立绘数量。"""
        if not self.cache_dir.is_dir():
            return 0
        return sum(1 for _ in self._cache_files())

    async def download_all(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        progress=None,
        chunk_size: int = 40,
    ) -> dict[str, int]:
        """批量下载全部缺失立绘；可重复执行，已下载的会跳过。

        Args:
            rows: 需要立绘的图鉴行；重复编号自动去重。
            progress: 每批回调 ``(已完成, 总数, 本次成功数)``。
            chunk_size: 每批数量，控制并发与进度回报粒度。

        Returns:
            ``{"total", "saved", "skipped", "failed"}``。
        """
        unique: dict[tuple[str, str, bool], Mapping[str, Any]] = {}
        for row in rows:
            if not self._cache_name(row.get("id")):
                continue
            dex = int(row.get("id"))
            unique.setdefault((str(dex), str(row.get("form_name") or ""), self._is_shiny(row)), row)

        total = len(unique)
        if not self.allow_download:
            return {"total": total, "saved": 0, "skipped": 0, "failed": 0}

        todo = await asyncio.to_thread(self.missing, unique.values())
        contexts = {str(target): row for row in unique.values()
                    if (target := self._cache_path(row)) is not None}
        unsupported = [row for row in unique.values()
                       if sprite_for(row) is None and self.path_for(row) is None]
        for row in unsupported:
            self._warn_once(
                f"unsupported:{row.get('id')}:{row.get('form_name')}",
                f"[宝可梦立绘] 批量补图无法匹配 {self._describe(row)}：{self._missing_reason(row)}",
            )
        skipped = total - len(todo) - len(unsupported)
        if not todo:
            return {"total": total, "saved": 0, "skipped": skipped, "failed": len(unsupported)}

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import aiohttp
        except ImportError:
            self._warn_once("aiohttp", "[宝可梦立绘] 批量补图失败：未安装 aiohttp，无法请求官方素材源")
            return {
                "total": total,
                "saved": 0,
                "skipped": skipped,
                "failed": len(todo) + len(unsupported),
            }

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        chunk_size = max(1, int(chunk_size))
        saved, failed = 0, len(unsupported)

        async def run_one(session, dex: int | str, target: Path) -> tuple[int | str, bytes | None]:
            async with semaphore:
                self._remember_context(target, contexts[str(target)], dex)
                if self._is_shiny_target(target):
                    data = await self._download(session, dex, shiny=True)
                else:
                    data = await self._download(session, dex)
                return dex, data

        async with aiohttp.ClientSession(timeout=timeout) as session:
            for start in range(0, len(todo), chunk_size):
                batch = todo[start : start + chunk_size]
                results = await asyncio.gather(
                    *(run_one(session, dex, target) for dex, target in batch)
                )
                for (_dex, target), (_result_dex, data) in zip(batch, results):
                    if not data:
                        failed += 1
                        continue
                    try:
                        await asyncio.to_thread(self._save_download, target, data)
                        saved += 1
                    except OSError as exc:
                        failed += 1
                        self._warn_once(
                            "write:" + str(target),
                            f"[宝可梦立绘] 批量缓存写入失败 {self._describe(contexts.get(str(target)), _dex)}：{target}；{type(exc).__name__}: {exc}",
                        )
                if progress is not None:
                    try:
                        progress(min(start + len(batch), len(todo)), len(todo), saved)
                    except Exception:
                        pass
        return {
            "total": total,
            "saved": saved,
            "skipped": skipped,
            "failed": failed,
        }
