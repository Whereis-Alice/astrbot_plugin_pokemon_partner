"""The themed discs retain the original ball signature and safe image ownership."""

from __future__ import annotations

import hashlib
import random
from unittest.mock import patch

from PIL import ImageDraw

from rarity_emblems import KINDS, _cached_emblem, render_emblem


def main():
    _cached_emblem.cache_clear()
    game_rng = random.getstate()
    originals = {}
    structures = set()
    line_original = ImageDraw.ImageDraw.line
    arc_original = ImageDraw.ImageDraw.arc
    polygon_original = ImageDraw.ImageDraw.polygon
    for kind in KINDS:
        calls = []

        def line(draw, xy, *args, _records=calls, **kwargs):
            _records.append(("line", tuple(xy)))
            return line_original(draw, xy, *args, **kwargs)

        def arc(draw, xy, start, end, *args, _records=calls, **kwargs):
            _records.append(("arc", tuple(xy), start, end))
            return arc_original(draw, xy, start, end, *args, **kwargs)

        def polygon(draw, xy, *args, _records=calls, **kwargs):
            _records.append(("polygon", tuple(xy)))
            return polygon_original(draw, xy, *args, **kwargs)

        with (
            patch.object(ImageDraw.ImageDraw, "line", line),
            patch.object(ImageDraw.ImageDraw, "arc", arc),
            patch.object(ImageDraw.ImageDraw, "polygon", polygon),
            patch.object(
                ImageDraw.ImageDraw,
                "text",
                side_effect=AssertionError("圆盘不应绘制文本"),
            ),
        ):
            image = render_emblem(kind, 470)
        assert image.mode == "RGBA" and image.size == (470, 470)
        assert image.getpixel((0, 0))[3] == 0 and image.getpixel((469, 469))[3] == 0
        assert image.getpixel((235, 235))[3] == 255
        assert ("line", ((86, 470), (854, 470))) in calls, "精灵球横向赤道遗漏"
        assert any(call[0] == "arc" for call in calls), "圆盘断弧遗漏"
        structures.add(hashlib.sha256(repr(calls).encode()).hexdigest())
        originals[kind] = hashlib.sha256(image.tobytes()).hexdigest()
        image.putpixel((235, 235), (0, 0, 0, 0))
        assert (
            originals[kind]
            == hashlib.sha256(render_emblem(kind, 470).tobytes()).hexdigest()
        )
    assert len(structures) == 5, "五档没有独立的图形结构"
    assert len(set(originals.values())) == 5
    for kind in KINDS:
        for diameter in (1, 2, 16, 96, 190, 600):
            image = render_emblem(kind, diameter)
            assert image.size == (diameter, diameter) and image.mode == "RGBA"
            if diameter >= 16:
                assert image.getpixel((0, 0))[3] == 0
    assert random.getstate() == game_rng
    _cached_emblem.cache_clear()
    for kind in KINDS:
        assert (
            originals[kind]
            == hashlib.sha256(render_emblem(kind, 470).tobytes()).hexdigest()
        )
    assert render_emblem("unknown", 190).tobytes() == render_emblem("C", 190).tobytes()
    for diameter in (0, -1):
        try:
            render_emblem("C", diameter)
        except ValueError:
            pass
        else:
            raise AssertionError("无效圆盘尺寸未拒绝")
    print(
        "5 disc structures, ball equator, transparent exterior, 30 sizes, deterministic rebuild, cache isolation and RNG independence passed"
    )


if __name__ == "__main__":
    main()
