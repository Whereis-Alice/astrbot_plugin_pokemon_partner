"""伙伴立绘获取与本地缓存。

优先级：

1. 配置的 ``images_dir``（pokemon-dataset-zh 的 ``data/images``）；
2. 插件数据目录 ``artwork/`` 下已缓存的官方图；
3. 可选的 PokéAPI 官方图下载（``fallback_web_image`` 控制）。

渲染路径要求同步返回 ``PIL.Image``，所以下载由 :meth:`ArtworkCache.prefetch`
在抽卡前批量完成（异步），渲染时只读缓存。
"""

from __future__ import annotations

import asyncio
import io
from collections import OrderedDict
from pathlib import Path
from typing import Any, Iterable, Mapping

from PIL import Image

try:
    from astrbot.api import logger
except ImportError:  # 脱离 AstrBot 环境单测
    import logging

    logger = logging.getLogger(__name__)

SPRITE_URL = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/"
    "pokemon/other/official-artwork/{dex}.png"
)
SPRITE_URL_FALLBACK = (
    "https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{dex}.png"
)
MAX_CACHED_IMAGES = 160


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
        self.max_images = MAX_CACHED_IMAGES

    # ---------- 同步读取（渲染线程） ----------
    def _local_path(self, row: Mapping[str, Any]) -> Path | None:
        if self.db is not None and self.images_dir is not None:
            try:
                found = self.db.find_image(dict(row), self.images_dir)
            except Exception:
                found = None
            if found:
                return Path(found)
        return None

    @staticmethod
    def _cache_name(dex: Any) -> str:
        try:
            return f"{int(dex):04d}.png"
        except (TypeError, ValueError):
            return ""

    def path_for(self, row: Mapping[str, Any]) -> Path | None:
        local = self._local_path(row)
        if local is not None:
            return local
        name = self._cache_name(row.get("id"))
        if not name:
            return None
        cached = self.cache_dir / name
        return cached if cached.is_file() else None

    def load(self, row: Mapping[str, Any]) -> Image.Image | None:
        """同步加载，供 Pillow 渲染回调使用。失败返回 ``None``。"""
        path = self.path_for(row)
        key = str(path or "")
        if not path or key in self._failed:
            return None
        cached = self._images.get(key)
        if cached is not None:
            self._images.move_to_end(key)
            return cached
        try:
            image = Image.open(path).convert("RGBA")
            image.load()
        except Exception:
            self._failed.add(key)
            return None
        self._images[key] = image
        # 有界 LRU：长期运行时不会把上千张立绘常驻内存。
        while len(self._images) > self.max_images:
            self._images.popitem(last=False)
        return image

    def clear_cache(self) -> None:
        self._images.clear()
        self._failed.clear()

    def cache_size(self) -> int:
        """当前常驻内存的立绘数量（调试与测试用）。"""
        return len(self._images)

    # ---------- 异步预取 ----------
    def missing(self, rows: Iterable[Mapping[str, Any]]) -> list[tuple[int, Path]]:
        out: list[tuple[int, Path]] = []
        for row in rows:
            if self._local_path(row) is not None:
                continue
            name = self._cache_name(row.get("id"))
            if not name:
                continue
            target = self.cache_dir / name
            if not target.is_file():
                out.append((int(row["id"]), target))
        return out

    async def prefetch(self, rows: Iterable[Mapping[str, Any]], limit: int = 30) -> int:
        """下载缺失立绘，返回成功数量。网络失败不抛异常。"""
        if not self.allow_download:
            return 0
        todo = self.missing(rows)[:limit]
        if not todo:
            return 0
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        try:
            import aiohttp
        except ImportError:
            return 0
        timeout = aiohttp.ClientTimeout(total=self.timeout)
        saved = 0
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for dex, target in todo:
                data = await self._download(session, dex)
                if data is None:
                    continue
                target.write_bytes(data)
                saved += 1
        return saved

    async def _download(self, session, dex: int) -> bytes | None:
        """依次尝试官方高清图与普通精灵图，兼容网络受限的环境。"""
        for url in (SPRITE_URL, SPRITE_URL_FALLBACK):
            try:
                async with session.get(url.format(dex=dex)) as resp:
                    if resp.status != 200:
                        continue
                    content_type = resp.headers.get("Content-Type", "")
                    if "image" not in content_type.lower():
                        logger.debug(f"伙伴立绘 #{dex} 返回非图片内容: {content_type}")
                        continue
                    declared = int(resp.headers.get("Content-Length") or 0)
                    if declared > 8 * 1024 * 1024:
                        continue
                    data = await resp.read()
                    if len(data) > 8 * 1024 * 1024:
                        continue
                with Image.open(io.BytesIO(data)) as image:
                    image.verify()
                return data
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"伙伴立绘下载失败 #{dex}: {type(exc).__name__}: {exc}")
        return None

    def cleanup_cache(self, keep: Iterable[int] | None = None) -> int:
        """删除不再需要的缓存图，返回删除数量。"""
        if keep is None:
            return 0
        wanted = {self._cache_name(dex) for dex in keep}
        removed = 0
        if not self.cache_dir.is_dir():
            return 0
        for path in self.cache_dir.glob("*.png"):
            if path.name not in wanted:
                try:
                    path.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed
