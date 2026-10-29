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
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image

try:
    from .image_download import read_image_body
except ImportError:
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
        self.max_images = MAX_CACHED_IMAGES

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
                return Path(found)
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
        # 全国编号图源是默认形态；形态行必须由本地数据集精确匹配。
        if row.get("form_name"):
            return None
        name = self._cache_name(row.get("id"))
        if not name:
            return None
        folder = self.cache_dir / "shiny" if self._is_shiny(row) else self.cache_dir
        return folder / name

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
            except Exception:
                valid = False
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
            return None
        with self._lock:
            cached = self._images.get(key)
            if cached is not None:
                self._images.move_to_end(key)
                return cached
        try:
            image = Image.open(path).convert("RGBA")
            image.load()
        except Exception:
            with self._lock:
                self._failed.add(key)
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

    def cache_size(self) -> int:
        """当前常驻内存的立绘数量（调试与测试用）。"""
        with self._lock:
            return len(self._images)

    # ---------- 异步预取 ----------
    def missing(self, rows: Iterable[Mapping[str, Any]]) -> list[tuple[int, Path]]:
        out: list[tuple[int, Path]] = []
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
                out.append((int(row["id"]), target))
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
            return 0
        todo = await asyncio.to_thread(lambda: self.missing(rows)[: max(0, limit)])
        if not todo:
            return 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import aiohttp
        except ImportError:
            return 0
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            results = await asyncio.gather(
                *(
                    self._download(session, dex, shiny=True)
                    if target.parent == self.cache_dir / "shiny"
                    else self._download(session, dex)
                    for dex, target in todo
                ),
                return_exceptions=True,
            )
        saved = 0
        for (_dex, target), data in zip(todo, results):
            if isinstance(data, Exception) or data is None:
                continue
            try:
                await asyncio.to_thread(self._save_download, target, data)
                saved += 1
            except OSError:
                continue
        return saved

    async def _download(
        self, session, dex: int, *, shiny: bool = False
    ) -> bytes | None:
        """依次尝试对应版本的高清、HOME 与精灵图，闪光图不混入普通缓存。"""
        sources = (
            (SHINY_SPRITE_URL, SHINY_SPRITE_URL_HOME, SHINY_SPRITE_URL_FALLBACK)
            if shiny
            else (SPRITE_URL, SPRITE_URL_HOME, SPRITE_URL_FALLBACK)
        )
        for url in sources:
            try:
                async with session.get(url.format(dex=dex)) as resp:
                    if resp.status != 200:
                        continue
                    content_type = resp.headers.get("Content-Type", "")
                    if "image" not in content_type.lower():
                        logger.debug(
                            f"宝可梦立绘 #{dex} 返回非图片内容: {content_type}"
                        )
                        continue
                    data = await read_image_body(resp)
                    if data is None:
                        continue
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                return data
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"宝可梦立绘下载失败 #{dex}: {type(exc).__name__}: {exc}")
        return None

    def cleanup_cache(self, keep: Iterable[int] | None = None) -> int:
        """删除不再需要的缓存图，返回删除数量。"""
        if keep is None:
            return 0
        wanted = {self._cache_name(dex) for dex in keep}
        removed = 0
        if not self.cache_dir.is_dir():
            return 0
        paths = (
            *self.cache_dir.glob("*.png"),
            *(self.cache_dir / "shiny").glob("*.png"),
        )
        for path in paths:
            if path.name not in wanted:
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
        return sum(1 for _ in self.cache_dir.glob("*.png")) + sum(
            1 for _ in (self.cache_dir / "shiny").glob("*.png")
        )

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
        unique: dict[tuple[int, bool], Mapping[str, Any]] = {}
        for row in rows:
            try:
                dex = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            unique.setdefault((dex, self._is_shiny(row)), row)

        total = len(unique)
        if not self.allow_download:
            return {"total": total, "saved": 0, "skipped": 0, "failed": 0}

        todo = await asyncio.to_thread(self.missing, unique.values())
        skipped = total - len(todo)
        if not todo:
            return {"total": total, "saved": 0, "skipped": skipped, "failed": 0}

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import aiohttp
        except ImportError:
            return {
                "total": total,
                "saved": 0,
                "skipped": skipped,
                "failed": len(todo),
            }

        timeout = aiohttp.ClientTimeout(total=self.timeout)
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        chunk_size = max(1, int(chunk_size))
        saved = failed = 0

        async def run_one(session, dex: int, target: Path) -> tuple[int, bytes | None]:
            async with semaphore:
                if target.parent == self.cache_dir / "shiny":
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
                    except OSError:
                        failed += 1
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
