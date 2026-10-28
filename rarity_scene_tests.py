"""Scenery stays deterministic, scalable, isolated, and independent of game RNG."""

from __future__ import annotations

import hashlib
import random
import time

from rarity_scenes import KINDS, _cached_scene, render_scene


def main():
    _cached_scene.cache_clear()
    game_rng = random.getstate()
    hashes = set()
    digests = {}
    measurements = {}
    for kind in KINDS:
        start = time.perf_counter()
        original = render_scene(kind, 1080, 1500)
        elapsed = time.perf_counter() - start
        assert original.mode == "RGBA" and original.size == (1080, 1500)
        assert original.getchannel("A").getextrema() == (255, 255)
        digest = hashlib.sha256(original.tobytes()).hexdigest()
        hashes.add(digest)
        digests[kind] = digest
        original.putpixel((50, 50), (0, 0, 0, 0))
        again = render_scene(kind, 1080, 1500)
        assert digest == hashlib.sha256(again.tobytes()).hexdigest(), (
            "调用方修改污染了场景缓存"
        )
        measurements[kind] = round(elapsed, 3)
    assert len(hashes) == 5, "稀有度未产生不同场景"
    assert random.getstate() == game_rng, "场景改变了全局抽卡随机状态"

    for kind in KINDS:
        for width, height in ((308, 516), (471, 650), (1080, 2600), (96, 180), (1, 1)):
            image = render_scene(kind, width, height)
            assert image.size == (width, height) and image.mode == "RGBA"
    assert _cached_scene.cache_info().currsize <= 8
    assert random.getstate() == game_rng
    assert (
        render_scene("unknown", 96, 180).tobytes()
        == render_scene("C", 96, 180).tobytes()
    )
    for width, height in ((0, 20), (20, 0), (-1, 20)):
        try:
            render_scene("C", width, height)
        except ValueError:
            pass
        else:
            raise AssertionError("无效尺寸未拒绝")
    _cached_scene.cache_clear()
    for kind in KINDS:
        assert (
            digests[kind]
            == hashlib.sha256(render_scene(kind, 1080, 1500).tobytes()).hexdigest()
        ), "重建场景不确定"
    print(
        f"5 deterministic scenes, 25 sizes, opaque RGBA, cache isolation and RNG independence passed; cold seconds={measurements}"
    )


if __name__ == "__main__":
    main()
