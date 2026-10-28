"""Public-renderer visual regressions: text contrast, artwork, bounds and editions.

Run ``python rarity_visual_tests.py`` without downloads or production state.
Optional ``--output DIR --art-dir DIR`` also writes real-art review sheets.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import math
import os
import re
import sys
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

_runtime = None
if "ASTRBOT_ROOT" not in os.environ:
    _runtime = tempfile.TemporaryDirectory(prefix="partner-visual-")
    os.environ["ASTRBOT_ROOT"] = _runtime.name

from PIL import Image, ImageChops, ImageColor, ImageDraw, ImageFilter

# The renderer only needs AstrBot's logger. Keep these isolated Pillow checks
# independent of an installed SDK's platform imports and runtime startup.
if "astrbot.api" not in sys.modules:
    sys.modules.setdefault("astrbot", ModuleType("astrbot"))
    _api = ModuleType("astrbot.api")
    _api.logger = logging.getLogger("rarity-visual-tests")
    sys.modules["astrbot.api"] = _api

from gacha import DrawItem
from partner_cards import render_draw_card


CONTEXT = dict(nickname="真新镇的小智 · 收藏与图鉴验证", remaining=2,
               daily_limit=3, dust=1280, pity_ssr=24, ssr_pity_after=30)
ROW = {
    "id": "25", "id_raw": "0025", "category": "鼠宝可梦", "height": "0.4m", "weight": "6.0kg",
    "abilities": "静电", "hidden_abilities": "避雷针", "catch_rate": "190",
    "weak_to": "地面×2", "resist_to": "电×0.5", "immune_to": "测试免疫",
    "egg_groups": "陆上/妖精", "gender_ratio": "♂50%♀50%", "evolution": "皮丘 → 皮卡丘 → 雷丘",
    "profile": "性格温顺，喜欢与训练家散步。它会用脸颊上的电气袋储存电力。",
    "stats": "HP35/攻击55/防御40/特攻50/特防50/速度90（总和320）",
}
BASE = DrawItem("25", 25, "0025", "皮卡丘", "电", 320, "C", False, True, 0, 1, ROW)
EDITIONS = (("C", False), ("R", False), ("SR", False), ("SSR", False), ("SSR", True))
MARKERS = ((249, 18, 37), (28, 242, 47), (31, 53, 247), (22, 233, 249))
BODY_MARKERS = ("鼠宝可梦", "静电", "避雷针", "捕获度", "弱点", "抗性", "免疫", "陆上",
                "蛋群", "性别", "进化", "性格温顺", "0.4m", "6.0kg")


@dataclass
class TextRecord:
    value: str
    box: tuple
    size: int
    contrast: float


def luminance(rgb):
    channels = [value / 255 for value in rgb[:3]]
    linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
    return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722


def contrast_ratio(foreground, background):
    if len(foreground) == 4:
        alpha = foreground[3] / 255
        foreground = tuple(alpha * f + (1 - alpha) * b for f, b in zip(foreground[:3], background[:3]))
    a, b = sorted((luminance(foreground), luminance(background)))
    return (b + .05) / (a + .05)


def portrait():
    """A transparent portrait with distinct extremities exposes cropping/overlays."""
    result = Image.new("RGBA", (280, 340))
    draw = ImageDraw.Draw(result)
    draw.rounded_rectangle((78, 42, 206, 298), radius=48, fill=(219, 13, 186, 255))
    draw.line((140, 30, 140, 312), fill=(219, 13, 186, 255), width=14)
    draw.line((24, 170, 256, 170), fill=(219, 13, 186, 255), width=14)
    for box, color in zip(((125, 15, 155, 44), (240, 155, 270, 185),
                           (125, 298, 155, 328), (10, 155, 40, 185)), MARKERS):
        draw.rectangle(box, fill=(*color, 255))
    return result


def _rgb(value):
    if isinstance(value, str):
        return ImageColor.getrgb(value)
    if isinstance(value, int):
        return (value, value, value)
    return tuple(value)


def capture_render(items, loader, *, record_composites=None, **context):
    records = []
    owners = []
    original = ImageDraw.ImageDraw.text
    original_composite = Image.Image.alpha_composite
    original_paste = Image.Image.paste

    def transfer(source, target, offset):
        if not isinstance(source, Image.Image):
            return
        for index, owner in enumerate(owners):
            if owner == id(source):
                record = records[index]
                record.box = tuple(value + offset[axis % 2] for axis, value in enumerate(record.box))
                owners[index] = id(target)

    def composite(target, source_image, dest=(0, 0), source=(0, 0)):
        result = original_composite(target, source_image, dest=dest, source=source)
        if record_composites is not None and source_image.mode == "RGBA" \
                and source_image.width == source_image.height and 300 <= source_image.width <= 650:
            alpha = source_image.getchannel("A")
            size = source_image.width
            if max(alpha.getpixel(point) for point in ((0, 0), (size-1, 0), (0, size-1), (size-1, size-1))) <= 8 \
                    and alpha.getpixel((size//2, size//2)) >= 220:
                record_composites.append((dest[0], dest[1], size))
        transfer(source_image, target, (dest[0]-source[0], dest[1]-source[1]))
        return result

    def paste(target, source_image, box=None, mask=None):
        result = original_paste(target, source_image, box=box, mask=mask)
        if isinstance(box, tuple) and len(box) >= 2:
            transfer(source_image, target, box[:2])
        return result

    def capture(draw, xy, value, *args, **kwargs):
        text = str(value)
        font, anchor = kwargs.get("font"), kwargs.get("anchor")
        box = draw.textbbox(xy, text, font=font, anchor=anchor,
                            stroke_width=kwargs.get("stroke_width", 0))
        width, height = max(1, math.ceil(box[2] - box[0])), max(1, math.ceil(box[3] - box[1]))
        mask = Image.new("L", (width, height))
        # Use the unpatched method when constructing the glyph mask.
        original(ImageDraw.Draw(mask), (xy[0] - box[0], xy[1] - box[1]), text,
                 font=font, anchor=anchor, fill=255)
        pixels = mask.load()
        points = [(x, y) for y in range(0, height, 2) for x in range(0, width, 2) if pixels[x, y] >= 224]
        points = points[::max(1, len(points) // 64)]
        color = _rgb(kwargs.get("fill", (255, 255, 255)))
        ratios = []
        for x, y in points:
            xx, yy = round(box[0] + x), round(box[1] + y)
            if 0 <= xx < draw._image.width and 0 <= yy < draw._image.height:
                ratios.append(contrast_ratio(color, _rgb(draw._image.getpixel((xx, yy)))))
        records.append(TextRecord(text, box, getattr(font, "size", 0), min(ratios, default=21)))
        owners.append(id(draw._image))
        return original(draw, xy, value, *args, **kwargs)

    with patch.object(ImageDraw.ImageDraw, "text", capture), \
         patch.object(Image.Image, "alpha_composite", composite), \
         patch.object(Image.Image, "paste", paste):
        encoded = render_draw_card(items, image_loader=loader, **dict(CONTEXT, **context))
    return Image.open(io.BytesIO(encoded)).convert("RGB"), records


def meaningful(record):
    return bool(re.search(r"[一-龥\d♂♀]", record.value)) or record.value in ("HP", "NEW")


def coalesce_shadows(records):
    """The solid glyph, rather than its decorative shadow, determines contrast."""
    result = []
    for record in records:
        index = next((i for i, previous in enumerate(result)
                      if previous.value == record.value and previous.size == record.size
                      and max(abs(a-b) for a, b in zip(previous.box, record.box)) <= 6), None)
        if index is None:
            result.append(record)
        elif record.contrast > result[index].contrast:
            result[index] = record
    return result


def assert_text(image, records, *, single):
    records = coalesce_shadows(records)
    content = [record for record in records if meaningful(record) and record.value.strip()]
    for record in content:
        left, top, right, bottom = record.box
        assert 24 <= left < right <= image.width - 24, ("文字越过左右边界", record)
        assert 18 <= top < bottom <= image.height - 12, ("文字被画布裁切", record)
        assert record.size >= 24, ("中文或状态字过小", record)
        # Large names/titles can use the large-text contrast threshold.
        required = 3 if record.size >= 48 else 4.5
        assert record.contrast >= required - .03, ("文字与实际背景对比不足", round(record.contrast, 2), required, record)
        if single and any(marker in record.value for marker in BODY_MARKERS):
            assert record.size >= 28, ("单抽正文过小", record)
    for index, a in enumerate(content):
        for b in content[index + 1:]:
            overlap = max(a.box[0], b.box[0]) < min(a.box[2], b.box[2]) and max(a.box[1], b.box[1]) < min(a.box[3], b.box[3])
            assert not overlap, ("信息文字相互重叠", a.value, b.value)
    return {"minimum_information_font": min(record.size for record in content),
            "minimum_information_contrast": round(min(record.contrast for record in content), 2),
            "text_count": len(content)}


def matching_points(image, color, *, region=None):
    left, top, right, bottom = region or (0, 0, image.width, image.height)
    pixels = image.load()
    return [(x, y) for y in range(max(0, int(top)), min(image.height, int(bottom)), 2)
            for x in range(max(0, int(left)), min(image.width, int(right)), 2)
            if max(abs(a-b) for a, b in zip(pixels[x, y][:3], color)) <= 30]


def assert_portrait(image, records):
    body = matching_points(image, (219, 13, 186))
    assert len(body) >= 500, "加载器提供的透明立绘没有进入最终图片"
    bounds = (min(x for x, _ in body), min(y for _, y in body), max(x for x, _ in body), max(y for _, y in body))
    centers = []
    region = (bounds[0]-250, bounds[1]-100, bounds[2]+250, bounds[3]+100)
    for color in MARKERS:
        points = matching_points(image, color, region=region)
        assert len(points) >= 12, ("立绘头顶、左右末端或底部被裁切", color, bounds)
        centers.append((sum(x for x, _ in points)/len(points), sum(y for _, y in points)/len(points)))
    red, green, blue, cyan = centers
    assert red[1] < blue[1] and cyan[0] < green[0], ("立绘末端布局发生裁切或错位", centers)
    for record in coalesce_shadows(records):
        if meaningful(record):
            assert not any(record.box[0] <= x <= record.box[2] and record.box[1] <= y <= record.box[3] for x, y in body), ("立绘遮挡信息文字", record.value)
    return bounds


def scene_edges(image, records, portrait_box):
    """Ignore glyphs/art and remove hue: compare the remaining scene structure."""
    gray = image.convert("L")
    draw = ImageDraw.Draw(gray)
    for record in records:
        draw.rectangle(tuple(round(value) for value in record.box), fill=128)
    draw.rectangle(portrait_box, fill=128)
    gray = gray.crop((24, 200, image.width-24, min(image.height, 900))).resize((258, 175))
    return gray.filter(ImageFilter.FIND_EDGES).point(lambda value: 255 if value > 18 else 0)


def assert_original_elements(image, records, circular_assets):
    """Retain the user's Poké Ball disc, red rule, stat bars and lower columns."""
    pixels = image.load()
    red_rule = max(sum(pixels[x, y][0] > 150 and pixels[x, y][1] < 105 and pixels[x, y][2] < 105
                       for x in range(40, image.width-40)) for y in range(24, 66))
    assert red_rule >= image.width*.65, "原版顶部红细线缺失"
    for x, y in ((20, 20), (image.width-20, 20), (20, image.height-20), (image.width-20, image.height-20)):
        assert max(abs(a-b) for a, b in zip(pixels[x, y], (238, 237, 231))) <= 12, "原版外框圆角缺失"
    assert any(x+size/2 < image.width*.45 and 210 <= y <= 400 for x, y, size in circular_assets), "左侧独立精灵球圆盘缺失"
    labels = ("HP", "攻击", "防御", "特攻", "特防", "速度")
    colors = ((239, 93, 105), (247, 158, 76), (239, 197, 62),
              (91, 162, 238), (67, 190, 148), (177, 119, 235))
    stat_labels = []
    for label, color in zip(labels, colors):
        matches = [record for record in records if record.value == label]
        assert len(matches) == 1 and matches[0].box[0] > image.width/2, ("右侧六项能力标签缺失或错位", label)
        record = matches[0]
        stat_labels.append(record)
        points = matching_points(image, color, region=(record.box[2]+4, record.box[1], image.width-40, record.box[3]))
        assert len(points) >= 15, ("原版彩色能力条缺失", label)
    assert all(a.box[1] < b.box[1] for a, b in zip(stat_labels, stat_labels[1:])), "六项能力条未纵向排列"
    left = next(record for record in records if record.value == "宝可梦资料")
    right = next(record for record in records if record.value == "图鉴摘要")
    assert left.box[0] < image.width/2 < right.box[0] and abs(left.box[1]-right.box[1]) <= 3, "原版下方双栏资料布局缺失"
    assert left.box[1] > stat_labels[-1].box[3]+24, "下方资料侵入能力条区域"


def write_real_previews(folder, art_dir):
    from gacha import GachaRules, build_partners
    from pokedex_db import PokedexDB

    partners = build_partners(PokedexDB(), GachaRules.from_config({"include_alternate_forms": True}))
    selections = []
    folder.mkdir(parents=True, exist_ok=True)
    for dex, (rarity, shiny) in zip((1, 25, 448, 384, 151), EDITIONS):
        partner = next(p for p in partners if p.dex == dex and not p.row.get("form_name"))
        item = DrawItem(partner.key, partner.dex, partner.dex_raw, partner.name, partner.types,
                        partner.bst, rarity, shiny, True, 0, 1, partner.row)
        selections.append(item)
        def loader(row):
            folder = art_dir / "shiny" if row.get("_artwork_shiny") is True else art_dir
            path = folder / f"{int(row['id']):04d}.png"
            if not path.is_file():
                raise AssertionError(f"视觉验证缺少真实立绘: {path}")
            with Image.open(path) as image:
                return image.convert("RGBA")
        image, records = capture_render([item], loader)
        assert_text(image, records, single=True)
        image.save(folder / f"real-{rarity if not shiny else 'SHINY'}.jpg", quality=94)
    mixed = selections + [replace(item, is_new=False, dust=120) for item in selections]
    data = render_draw_card(mixed, image_loader=loader, **CONTEXT)
    (folder / "real-ten.jpg").write_bytes(data)
    thumbs = []
    for name in ("C", "R", "SR", "SSR", "SHINY"):
        with Image.open(folder / f"real-{name}.jpg") as image:
            thumb = image.copy()
            thumb.thumbnail((360, 820))
            thumbs.append(thumb)
    sheet = Image.new("RGB", (len(thumbs)*384, max(image.height for image in thumbs)+24), (232, 230, 226))
    for index, image in enumerate(thumbs):
        sheet.paste(image, (index*384+12, 12))
    sheet.save(folder / "real-editions-360px.png")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--art-dir", type=Path)
    args = parser.parse_args()
    sprite = portrait()
    report, structures = {}, {}
    editions = [replace(BASE, rarity=rarity, shiny=shiny) for rarity, shiny in EDITIONS]
    for item in editions:
        key = "SHINY" if item.shiny else item.rarity
        circular_assets = []
        image, records = capture_render([item], lambda _: sprite.copy(), record_composites=circular_assets)
        report[key] = assert_text(image, records, single=True)
        bounds = assert_portrait(image, records)
        assert_original_elements(image, records, circular_assets)
        report[key]["original_frame_elements"] = True
        structures[key] = scene_edges(image, records, bounds)
        joined = "".join(record.value for record in records).replace(" ", "")
        for value in ("皮卡丘", "0025", "静电", "避雷针", "190", "地面×2", "电×0.5", "陆上", "性别", "皮丘", "性格温顺", "1280", "24/30", "2/3"):
            assert value in joined, ("卡片遗漏资料", key, value)
        assert image.width == 1080 and image.height < 3200, ("画布异常", key, image.size)
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)
            image.save(args.output / f"fixture-{key}.jpg", quality=94)
    differences = {}
    for index, (a, first) in enumerate(structures.items()):
        for b, second in list(structures.items())[index+1:]:
            difference = ImageChops.difference(first, second)
            ratio = 1 - difference.histogram()[0] / (difference.width*difference.height)
            differences[f"{a}/{b}"] = round(ratio, 4)
            assert ratio >= .015, ("去色后场景轮廓相同，可能只是换色", a, b, ratio)
    report["scene_outline_differences"] = differences
    long = replace(BASE, name="超级长名称宝可梦（特别地区与额外形态）"*3,
                   types="草/毒/飞行/龙/恶", row=dict(ROW, profile=ROW["profile"]*20,
                       evolution="皮丘 →（亲密度提升）皮卡丘 →（雷之石）雷丘；"*8,
                       abilities="静电、避雷针、发光、同步"*5))
    for item in editions:
        candidate = replace(long, rarity=item.rarity, shiny=item.shiny)
        image, records = capture_render([candidate], lambda _: sprite.copy())
        key = "long-" + ("SHINY" if item.shiny else item.rarity)
        report[key] = assert_text(image, records, single=True)
        assert_portrait(image, records)
        assert image.height < 3200
        assert any("…" in record.value for record in records), ("长资料没有明确截断标记", key)
    for count in (2, 10):
        items = (editions + [replace(item, is_new=False, dust=120) for item in editions])[:count]
        image, records = capture_render(items, lambda _: sprite.copy())
        report[f"multi-{count}"] = assert_text(image, records, single=False)
        assert image.width == 1080 and image.height < 3400
        values = [record.value for record in records]
        assert sum("新伙伴" in value for value in values) == min(count, 5), ("多抽NEW状态缺失", count)
        assert sum("120" in value and "星尘" in value for value in values) == max(0, count-5), ("多抽重复奖励缺失", count)
    image, records = capture_render([replace(BASE, shiny=True, is_new=False, dust=80, row={})], None)
    report["missing-art-data"] = assert_text(image, records, single=True)
    assert any("暂无" in record.value or "未缓存" in record.value for record in records)
    assert any("80" in record.value and "星尘" in record.value for record in records)
    if args.output:
        (args.output / "visual-regression.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.art_dir:
            write_real_previews(args.output / "real-art", args.art_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
