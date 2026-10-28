"""Deterministic Pillow scenery for five visibly different gacha materials.

The caller owns text, portraits, and opaque reading panels.  Scene generation never
uses network access, game state, or the global random generator.
"""

from __future__ import annotations

import colorsys
import math
import random
from functools import lru_cache
from itertools import pairwise

from PIL import Image, ImageDraw, ImageFilter

KINDS = ("C", "R", "SR", "SSR", "SHINY")
SPECTRUM = (
    (255, 116, 156),
    (255, 202, 111),
    (141, 229, 178),
    (112, 214, 249),
    (159, 146, 255),
    (240, 142, 244),
)
SEEDS = {"C": 15837, "R": 29011, "SR": 40709, "SSR": 65063, "SHINY": 90107}


def _mix(a, b, amount):
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _gradient(width, height, stops):
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        position = y / max(1, height - 1)
        for (start, first), (end, second) in pairwise(stops):
            if position <= end:
                color = _mix(
                    first, second, (position - start) / max(0.001, end - start)
                )
                break
        draw.line((0, y, width, y), fill=(*color, 255))
    return image


class Scene:
    def __init__(self, image, rng):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.rng = rng
        self.scale = image.width / 1080
        self.height = image.height / self.scale

    def points(self, points):
        return [(round(x * self.scale), round(y * self.scale)) for x, y in points]

    def box(self, values):
        return tuple(round(value * self.scale) for value in values)

    def polygon(self, points, fill, outline=None, width=1):
        self.draw.polygon(
            self.points(points),
            fill=fill,
            outline=outline,
            width=max(1, round(width * self.scale)),
        )

    def line(self, points, fill, width=1):
        self.draw.line(
            self.points(points),
            fill=fill,
            width=max(1, round(width * self.scale)),
            joint="curve",
        )

    def ellipse(self, box, fill=None, outline=None, width=1):
        self.draw.ellipse(
            self.box(box),
            fill=fill,
            outline=outline,
            width=max(1, round(width * self.scale)),
        )

    def arc(self, box, start, end, fill, width=1):
        self.draw.arc(
            self.box(box),
            start,
            end,
            fill=fill,
            width=max(1, round(width * self.scale)),
        )

    def glow(self, x, y, rx, ry, color, opacity=150, blur=42):
        # Local blurred tiles keep full-card effects inexpensive on tall cards.
        pad = blur * 3
        bounds = self.box((x - rx - pad, y - ry - pad, x + rx + pad, y + ry + pad))
        left, top, right, bottom = bounds
        tile = Image.new("RGBA", (max(1, right - left), max(1, bottom - top)))
        draw = ImageDraw.Draw(tile)
        draw.ellipse(
            (
                round(pad * self.scale),
                round(pad * self.scale),
                tile.width - round(pad * self.scale),
                tile.height - round(pad * self.scale),
            ),
            fill=(*color, opacity),
        )
        self.image.alpha_composite(
            tile.filter(ImageFilter.GaussianBlur(max(1, blur * self.scale))),
            (left, top),
        )
        self.draw = ImageDraw.Draw(self.image)

    def veil(self, painter, blur=0):
        layer = Image.new("RGBA", self.image.size)
        painter(Scene(layer, self.rng))
        if blur:
            layer = layer.filter(ImageFilter.GaussianBlur(blur * self.scale))
        self.image.alpha_composite(layer)
        self.draw = ImageDraw.Draw(self.image)


def _grain(scene, strength=11):
    width, height = max(1, scene.image.width // 3), max(1, scene.image.height // 3)
    noise = bytes(scene.rng.randrange(96, 161) for _ in range(width * height))
    layer = Image.frombytes("L", (width, height), noise).resize(
        scene.image.size, Image.Resampling.BILINEAR
    )
    film = Image.merge(
        "RGBA", (layer, layer, layer, Image.new("L", scene.image.size, strength))
    )
    scene.image.alpha_composite(film)
    scene.draw = ImageDraw.Draw(scene.image)


def _curve(start, control1, control2, end, steps=70):
    out = []
    for index in range(steps + 1):
        t = index / steps
        u = 1 - t
        out.append(
            (
                u * u * u * start[0]
                + 3 * u * u * t * control1[0]
                + 3 * u * t * t * control2[0]
                + t * t * t * end[0],
                u * u * u * start[1]
                + 3 * u * u * t * control1[1]
                + 3 * u * t * t * control2[1]
                + t * t * t * end[1],
            )
        )
    return out


def _stars(scene, count, region, colors, *, large=False):
    left, top, right, bottom = region
    for _ in range(count):
        x, y = scene.rng.uniform(left, right), scene.rng.uniform(top, bottom)
        radius = scene.rng.choice((0.7, 1, 1.4, 2, 2.6))
        color = scene.rng.choice(colors)
        scene.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
        if large and radius > 2:
            scene.line(((x - 6, y), (x + 6, y)), fill=color, width=1)
            scene.line(((x, y - 6), (x, y + 6)), fill=color, width=1)


def _forest(scene):
    scene.glow(346, 358, 220, 220, (255, 220, 127), 170, 85)
    scene.glow(826, 210, 220, 160, (199, 234, 149), 125, 88)
    scene.glow(290, 503, 240, 190, (176, 220, 112), 95, 66)
    for depth, color, count, thickness in (
        (0, (53, 116, 89, 105), 21, 12),
        (1, (23, 83, 63, 180), 14, 22),
    ):

        def trees(layer, depth=depth, color=color, count=count, thickness=thickness):
            for index in range(count):
                x = index * 1080 / max(1, count - 1) + layer.rng.uniform(-38, 38)
                crown = layer.rng.uniform(155, 370)
                base = 815 + depth * 32
                layer.polygon(
                    (
                        (x - thickness, base),
                        (x - thickness * 0.6, crown),
                        (x + thickness * 0.7, crown - 16),
                        (x + thickness, base),
                    ),
                    fill=color,
                )
                for side in (-1, 1):
                    branch_y = crown + layer.rng.uniform(65, 195)
                    layer.line(
                        (
                            (x, branch_y + 75),
                            (x + side * 65, branch_y),
                            (x + side * 110, branch_y - 30),
                        ),
                        color,
                        7 + depth * 3,
                    )
                for _ in range(5):
                    xx, yy = (
                        x + layer.rng.uniform(-70, 70),
                        crown + layer.rng.uniform(-30, 80),
                    )
                    r = layer.rng.uniform(38, 88)
                    layer.ellipse(
                        (xx - r, yy - r * 0.6, xx + r, yy + r * 0.6), fill=color
                    )

        scene.veil(trees, blur=5 if depth == 0 else 1)

    def beams(layer):
        for offset, breadth in ((0, 58), (134, 40), (276, 68)):
            layer.polygon(
                (
                    (757 + offset, 145),
                    (774 + offset, 149),
                    (305 + offset + breadth, 839),
                    (191 + offset, 839),
                ),
                fill=(255, 237, 162, 28),
            )

    scene.veil(beams, blur=9)
    for base, color, amplitude in (
        (755, (45, 103, 58), 26),
        (810, (20, 78, 45), 41),
        (916, (11, 52, 37), 55),
    ):
        line = [(x, base + math.sin(x / 184) * amplitude) for x in range(-20, 1121, 22)]
        scene.polygon(
            line + [(1120, scene.height + 20), (-20, scene.height + 20)], fill=color
        )

    def bokeh(layer):
        for _ in range(92):
            x, y = layer.rng.uniform(25, 1055), layer.rng.uniform(225, 860)
            r = layer.rng.uniform(3, 17)
            layer.ellipse(
                (x - r, y - r, x + r, y + r),
                fill=(230, 245, 168, layer.rng.randrange(13, 50)),
            )

    scene.veil(bokeh, blur=2.5)
    for x in (-22, 1081):
        scene.polygon(
            ((x - 32, 860), (x - 10, 170), (x + 22, 166), (x + 43, 900)),
            fill=(8, 47, 35),
        )
    for _ in range(108):
        x, y = scene.rng.uniform(-15, 1095), scene.rng.uniform(735, 965)
        blade = scene.rng.uniform(15, 55)
        scene.line(
            ((x, y), (x + scene.rng.uniform(-14, 14), y - blade)),
            fill=(68, 134, 66),
            width=2,
        )
    for x, y, angle in (
        (57, 734, -0.5),
        (1018, 736, 0.5),
        (17, 309, 0.4),
        (1054, 402, -0.4),
    ):
        for index in range(7):
            yy = y - index * 23
            scene.line(((x, y + 20), (x + angle * 35, yy)), fill=(33, 101, 52), width=3)
            for side in (-1, 1):
                xx = x + angle * 20
                scene.polygon(
                    ((xx, yy), (xx + side * 36, yy - 19), (xx + side * 29, yy + 4)),
                    fill=(43, 117, 57),
                )


def _crystal(scene, x, base, width, height, lean=0, pale=False):
    tip = (x + lean, base - height)
    left = (x - width * 0.55, base - height * 0.66)
    right = (x + width * 0.51, base - height * 0.67)
    bottom_left, bottom_right = (
        (x - width * 0.42, base),
        (x + width * 0.46, base - height * 0.03),
    )
    heel, spine = (
        (x + width * 0.05, base + height * 0.06),
        (x + width * 0.10, base - height * 0.37),
    )
    colors = (
        ((164, 229, 252), (39, 121, 172), (76, 172, 207), (16, 74, 122))
        if pale
        else ((112, 198, 235), (23, 86, 149), (49, 143, 190), (9, 49, 105))
    )
    scene.polygon(
        (tip, left, bottom_left, heel, bottom_right, right),
        fill=colors[3],
        outline=(80, 178, 226),
        width=2,
    )
    scene.polygon((tip, left, spine), fill=colors[0])
    scene.polygon((tip, spine, right), fill=colors[1])
    scene.polygon((left, bottom_left, heel, spine), fill=colors[2])
    scene.polygon((right, spine, heel, bottom_right), fill=colors[3])
    scene.line((tip, spine, heel), fill=(177, 243, 255), width=2)
    scene.line((tip, left, bottom_left), fill=(210, 249, 255), width=1)
    scene.line((right, bottom_right), fill=(56, 133, 191), width=3)


def _ice_cavern(scene):
    scene.glow(540, 484, 265, 218, (62, 207, 250), 140, 88)
    scene.glow(540, 250, 170, 140, (109, 193, 255), 95, 65)

    def shafts(layer):
        for x, angle in ((135, 240), (660, -265), (965, -190)):
            layer.polygon(
                ((x, 40), (x + 32, 40), (x + angle + 125, 830), (x + angle - 54, 830)),
                fill=(135, 224, 255, 23),
            )

    scene.veil(shafts, blur=8)
    for x, base, width, height, lean in (
        (15, 802, 142, 470, 41),
        (109, 745, 119, 327, -21),
        (188, 830, 140, 255, 26),
        (1045, 830, 170, 485, -32),
        (934, 794, 140, 322, 28),
        (842, 839, 135, 204, -21),
    ):
        _crystal(scene, x, base, width, height, lean, pale=height < 350)
    for _ in range(6):
        x = scene.rng.uniform(260, 805)
        y = scene.rng.uniform(700, 856)
        _crystal(
            scene,
            x,
            y,
            scene.rng.uniform(25, 49),
            scene.rng.uniform(54, 105),
            scene.rng.uniform(-12, 12),
        )

    def water(layer):
        for index in range(18):
            rx = 110 + index * 25
            cy = 765 + index * 6
            layer.ellipse(
                (540 - rx, cy - rx * 0.14, 540 + rx, cy + rx * 0.14),
                outline=(85, 203, 247, 53 - index * 2),
                width=2,
            )
        for _ in range(65):
            x, y = (
                layer.rng.uniform(0, 1080),
                layer.rng.uniform(830, min(1250, layer.height)),
            )
            length = layer.rng.uniform(12, 74)
            layer.line(((x, y), (x + length, y)), fill=(95, 180, 228, 34), width=1)

    scene.veil(water)
    _stars(
        scene,
        95,
        (115, 225, 967, 777),
        ((130, 230, 255), (79, 169, 226), (206, 248, 255)),
    )
    for x, y in ((165, 393), (935, 484), (416, 260)):
        scene.glow(x, y, 10, 10, (194, 247, 255), 170, 10)


def _nebula_stage(scene):
    scene.glow(770, 459, 277, 226, (133, 48, 197), 140, 83)
    scene.glow(211, 409, 280, 130, (163, 35, 121), 100, 80)

    def clouds(layer):
        paths = (
            _curve((-90, 588), (266, 154), (590, 724), (1180, 213)),
            _curve((-80, 177), (480, 766), (754, 91), (1170, 726)),
        )
        for index, path in enumerate(paths):
            for sample, (x, y) in enumerate(path[::4]):
                radius = 25 + 24 * math.sin(sample * 0.61) ** 2
                layer.ellipse(
                    (x - radius * 1.8, y - radius, x + radius * 1.8, y + radius),
                    fill=(207, 46, 159, 45) if index == 0 else (85, 74, 232, 49),
                )

    scene.veil(clouds, blur=22)

    def spotlights(layer):
        for source, target, spread, color in (
            ((564, 176), (947, 764), 165, (247, 139, 211, 51)),
            ((1043, 189), (648, 761), 139, (186, 162, 251, 57)),
            ((226, 168), (980, 716), 116, (246, 122, 174, 29)),
        ):
            x, y = source
            tx, ty = target
            layer.polygon(
                ((x - 10, y), (x + 10, y), (tx + spread, ty), (tx - spread, ty)),
                fill=color,
            )
            layer.polygon(
                (
                    (x - 3, y),
                    (x + 3, y),
                    (tx + spread * 0.2, ty),
                    (tx - spread * 0.2, ty),
                ),
                fill=(255, 217, 247, 37),
            )

    scene.veil(spotlights, blur=9)
    _stars(
        scene,
        210,
        (12, 160, 1068, 970),
        ((213, 187, 240), (254, 199, 224), (131, 116, 181)),
        large=True,
    )
    portal = (460, 209, 1080, 846)
    scene.arc(portal, 182, 358, fill=(18, 12, 37), width=23)
    scene.arc((464, 213, 1076, 842), 182, 358, fill=(138, 73, 158), width=12)
    scene.arc((468, 217, 1072, 838), 184, 350, fill=(251, 177, 225), width=3)
    scene.arc((477, 226, 1063, 829), 186, 348, fill=(78, 48, 119), width=3)
    for angle in range(185, 355, 6):
        shine = (0.5 + 0.5 * math.cos(math.radians(angle - 226))) ** 1.4
        color = _mix((78, 45, 117), (253, 195, 230), shine)
        scene.arc((460, 209, 1080, 846), angle, angle + 7, fill=color, width=12)
        radians = math.radians(angle)
        scene.line(
            (
                (770 + math.cos(radians) * 309, 527 + math.sin(radians) * 318),
                (770 + math.cos(radians) * 297, 527 + math.sin(radians) * 306),
            ),
            fill=(159, 97, 165),
            width=1,
        )
    for x in (465, 1075):
        for shift, width, color in (
            (0, 34, (24, 18, 40)),
            (-2, 26, (83, 48, 115)),
            (-7, 7, (223, 152, 203)),
            (8, 5, (48, 27, 74)),
        ):
            scene.line(((x + shift, 522), (x + shift, 724)), fill=color, width=width)
        for y in range(541, 717, 15):
            scene.line(((x - 12, y), (x + 11, y)), fill=(127, 75, 145), width=1)
    for depth in range(24, -1, -1):
        scene.ellipse(
            (336, 666 + depth, 1204, 789 + depth),
            fill=_mix((19, 15, 35), (79, 50, 99), 1 - depth / 24),
            outline=_mix((43, 25, 60), (188, 109, 164), 1 - depth / 24),
            width=1,
        )
    scene.ellipse(
        (345, 667, 1195, 785), fill=(38, 24, 57), outline=(232, 151, 204), width=3
    )
    scene.ellipse((378, 678, 1162, 774), outline=(95, 58, 129), width=2)
    for inset in (10, 25, 41):
        scene.ellipse(
            (345 + inset * 2, 667 + inset * 0.35, 1195 - inset * 2, 785 - inset * 0.35),
            outline=(104, 66, 139),
            width=1,
        )
    for x in range(427, 1160, 56):
        scene.line(((770, 681), (x, 771)), fill=(87, 53, 121), width=1)
    scene.glow(770, 741, 280, 13, (243, 105, 181), 80, 20)
    _stars(
        scene, 43, (394, 606, 1063, 832), ((249, 166, 210), (240, 203, 148)), large=True
    )


def _gold_ring(scene, cx, cy, radius, width=3):
    for angle in range(0, 360, 6):
        shine = (0.5 + 0.5 * math.cos(math.radians(angle - 228))) ** 1.5
        color = _mix((104, 67, 23), (250, 224, 157), shine)
        scene.arc(
            (cx - radius, cy - radius, cx + radius, cy + radius),
            angle,
            angle + 7,
            color,
            width,
        )


def _metal_frame(scene):
    h = scene.height
    for inset, color, width in (
        (15, (5, 7, 12), 12),
        (19, (134, 86, 31), 8),
        (24, (245, 212, 140), 2),
        (29, (54, 40, 24), 5),
        (34, (177, 133, 67), 1),
    ):
        scene.line(
            (
                (inset, inset),
                (1080 - inset, inset),
                (1080 - inset, h - inset),
                (inset, h - inset),
                (inset, inset),
            ),
            fill=color,
            width=width,
        )
    for side in (1, -1):
        origin = 20 if side == 1 else 1060
        for y in range(88, max(89, round(h - 87)), 44):
            scene.line(
                (
                    (origin - side * 4, y - 9),
                    (origin + side * 9, y),
                    (origin - side * 4, y + 9),
                ),
                fill=(190, 143, 70),
                width=1,
            )
    for x, y, sx, sy in (
        (34, 34, 1, 1),
        (1046, 34, -1, 1),
        (34, h - 34, 1, -1),
        (1046, h - 34, -1, -1),
    ):
        points = (
            (x, y),
            (x + sx * 103, y),
            (x + sx * 83, y + sy * 17),
            (x + sx * 35, y + sy * 17),
            (x + sx * 17, y + sy * 35),
            (x + sx * 17, y + sy * 83),
            (x, y + sy * 103),
        )
        scene.polygon(points, fill=(183, 125, 40), outline=(254, 221, 140), width=2)
        scene.line(
            (
                (x + sx * 7, y + sy * 70),
                (x + sx * 7, y + sy * 7),
                (x + sx * 70, y + sy * 7),
            ),
            fill=(247, 222, 163),
            width=2,
        )
        scene.polygon(
            (
                (x + sx * 22, y + sy * 22),
                (x + sx * 33, y + sy * 12),
                (x + sx * 44, y + sy * 22),
                (x + sx * 33, y + sy * 34),
            ),
            fill=(207, 155, 68),
            outline=(255, 229, 164),
        )
    for index in range(90):
        x1, x2 = 20 + index * 1040 / 90, 20 + (index + 1) * 1040 / 90
        color = _mix(
            (103, 61, 17), (255, 226, 145), (0.5 + 0.5 * math.sin(index * 0.09)) ** 2
        )
        scene.line(((x1, 20), (x2, 20)), fill=color, width=5)
        scene.line(((1080 - x1, h - 20), (1080 - x2, h - 20)), fill=color, width=5)


def _gold_ribbon(scene, points, width):
    # Rasterize just the ribbon's local bounds at 2x; the rest of the card stays 1x.
    mesh = []
    for index, (x, y) in enumerate(points):
        before, after = (
            points[max(0, index - 1)],
            points[min(len(points) - 1, index + 1)],
        )
        dx, dy = after[0] - before[0], after[1] - before[1]
        length = max(0.001, math.hypot(dx, dy))
        radius = (
            width
            * (0.7 + 0.4 * math.sin(index / max(1, len(points) - 1) * math.pi))
            / 2
        )
        mesh.append((x, y, -dy / length, dx / length, radius))
    pad = width * 0.67 + 5
    left = max(0, math.floor((min(x for x, _ in points) - pad) * scene.scale))
    top = max(0, math.floor((min(y for _, y in points) - pad) * scene.scale))
    right = min(
        scene.image.width, math.ceil((max(x for x, _ in points) + pad) * scene.scale)
    )
    bottom = min(
        scene.image.height, math.ceil((max(y for _, y in points) + pad) * scene.scale)
    )
    if right <= left or bottom <= top:
        return
    size = right - left, bottom - top
    high_size = size[0] * 2, size[1] * 2

    def outline(ratio):
        a, b = [], []
        for x, y, nx, ny, radius in mesh:
            r = radius * ratio
            a.append(
                (
                    (x + nx * r) * scene.scale * 2 - left * 2,
                    (y + ny * r) * scene.scale * 2 - top * 2,
                )
            )
            b.append(
                (
                    (x - nx * r) * scene.scale * 2 - left * 2,
                    (y - ny * r) * scene.scale * 2 - top * 2,
                )
            )
        return a + list(reversed(b))

    mask = Image.new("L", high_size)
    ImageDraw.Draw(mask).polygon(outline(1), fill=255)
    mask = mask.resize(size, Image.Resampling.LANCZOS)
    profile = Image.new("L", high_size)
    pd = ImageDraw.Draw(profile)
    for index in range(40):
        ratio = 1 - index / 40
        value = round(40 + (1 - ratio) ** 0.65 * 215)
        pd.polygon(outline(ratio), fill=value)
    profile = profile.resize(size, Image.Resampling.LANCZOS).filter(
        ImageFilter.GaussianBlur(max(0.5, scene.scale * 1.15))
    )
    small_w, small_h = max(1, size[0] // 3), max(1, size[1] // 3)
    raw = bytearray(small_w * small_h)
    for y in range(small_h):
        yy = (top + y * size[1] / small_h) / scene.scale
        for x in range(small_w):
            xx = (left + x * size[0] / small_w) / scene.scale
            raw[y * small_w + x] = round(
                255 * (0.55 + 0.45 * math.sin(xx * 0.004 - yy * 0.003) ** 2)
            )
    reflection = Image.frombytes("L", (small_w, small_h), bytes(raw)).resize(
        size, Image.Resampling.BICUBIC
    )
    shade = Image.blend(profile, reflection, 0.23)
    stops = (
        (0, (83, 44, 9)),
        (0.3, (169, 109, 21)),
        (0.6, (233, 177, 57)),
        (0.82, (255, 222, 129)),
        (1, (255, 251, 209)),
    )
    colors = []
    for value in range(256):
        for (start, first), (end, second) in pairwise(stops):
            if value / 255 <= end:
                colors.append(
                    _mix(first, second, (value / 255 - start) / (end - start))
                )
                break
    channels = [shade.point([color[index] for color in colors]) for index in range(3)]
    surface = Image.merge("RGBA", (*channels, mask))
    shadow_mask = mask.filter(ImageFilter.GaussianBlur(max(1, scene.scale * 3)))
    shadow = Image.new("RGBA", size, (110, 65, 12))
    shadow.putalpha(shadow_mask.point(lambda value: round(value * 0.65)))
    scene.image.alpha_composite(shadow, (left, top))
    scene.image.alpha_composite(surface, (left, top))
    scene.draw = ImageDraw.Draw(scene.image)


def _obsidian_archive(scene):
    for index in range(28):
        x = scene.rng.uniform(-50, 1020)
        y = scene.rng.uniform(20, scene.height)
        width, height = scene.rng.uniform(90, 300), scene.rng.uniform(120, 410)
        color = scene.rng.choice(
            ((13, 22, 32), (19, 28, 39), (9, 15, 23), (23, 29, 38))
        )
        scene.polygon(
            (
                (x, y),
                (x + width * 0.8, y - height * 0.25),
                (x + width, y + height * 0.6),
                (x + width * 0.3, y + height),
            ),
            fill=color,
            outline=(28, 34, 42),
        )
    scene.glow(540, 524, 292, 274, (211, 151, 41), 200, 84)
    scene.glow(613, 447, 175, 199, (255, 224, 120), 188, 66)
    scene.glow(600, 447, 74, 148, (255, 252, 208), 150, 53)

    def gold_beams(layer):
        for offset in (-80, 98, 262):
            layer.polygon(
                (
                    (754 + offset, 133),
                    (793 + offset, 143),
                    (373 + offset, 855),
                    (80 + offset, 796),
                ),
                fill=(255, 210, 81, 67),
            )

    scene.veil(gold_beams, blur=14)
    flowing = (
        _curve((-130, 747), (972, 1033), (100, 166), (1139, 261), steps=420),
        _curve((-129, 465), (441, 24), (582, 926), (1170, 489), steps=420),
    )

    def foil_light(layer):
        for path in flowing:
            layer.line(path, fill=(242, 172, 43, 130), width=130)

    scene.veil(foil_light, blur=24)
    _gold_ribbon(scene, flowing[0], 68)
    _gold_ribbon(scene, flowing[1], 103)
    molten = (
        _curve((-90, 406), (185, 859), (860, 101), (1150, 592)),
        _curve(
            (-65, scene.height * 0.78),
            (354, scene.height * 0.98),
            (717, scene.height * 0.45),
            (1150, scene.height * 0.84),
        ),
    )

    def lava_light(layer):
        for path in molten:
            layer.line(path, fill=(211, 121, 26, 98), width=19)

    scene.veil(lava_light, blur=19)
    for path in molten:
        scene.line(path, fill=(91, 53, 21), width=18)
        scene.line(path, fill=(213, 141, 46), width=9)
        scene.line(path, fill=(255, 220, 147), width=3)
    for radius, width in ((302, 4), (285, 2), (259, 2), (221, 1)):
        _gold_ring(scene, 540, 520, radius, width)
    for angle in range(0, 360, 15):
        radians = math.radians(angle)
        x, y = 540 + math.cos(radians) * 302, 520 + math.sin(radians) * 302
        scene.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(234, 198, 116))
    scene.line(((217, 520), (863, 520)), fill=(90, 70, 39), width=1)
    scene.line(((540, 199), (540, 842)), fill=(90, 70, 39), width=1)
    nodes = [
        (scene.rng.uniform(78, 1002), scene.rng.uniform(220, 800)) for _ in range(30)
    ]
    for index in range(0, len(nodes) - 2, 3):
        scene.line(nodes[index : index + 3], fill=(93, 77, 43), width=1)
    for x, y in nodes:
        scene.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(220, 180, 94))
    _stars(
        scene,
        235,
        (56, 153, 1024, 930),
        ((249, 226, 151), (202, 156, 61), (255, 246, 203)),
        large=True,
    )
    for x, y, radius in (
        (213, 368, 12),
        (838, 302, 17),
        (274, 764, 10),
        (809, 725, 15),
        (911, 516, 11),
    ):
        scene.polygon(
            (
                (x, y - radius),
                (x + 3, y - 3),
                (x + radius, y),
                (x + 3, y + 3),
                (x, y + radius),
                (x - 3, y + 3),
                (x - radius, y),
                (x - 3, y - 3),
            ),
            fill=(255, 242, 180),
        )
    _metal_frame(scene)


def _liquid_foil(width, height):
    small_w, small_h = max(1, width // 3), max(1, height // 3)
    raw = bytearray(small_w * small_h * 3)
    scale = 1080 / small_w
    for y in range(small_h):
        yy = y * scale
        drift = yy * 0.006 + math.cos(yy * 0.007) * 0.43
        for x in range(small_w):
            xx = x * scale
            wave = (
                drift
                + math.sin(xx * 0.008 + yy * 0.009) * 0.8
                + math.cos(xx * 0.004 - yy * 0.013) * 0.36
            )
            hue = (wave / math.tau) % 1
            satin = abs(math.sin(wave * 1.72)) ** 9
            color = colorsys.hsv_to_rgb(hue, 0.43 - satin * 0.30, 0.93 + satin * 0.07)
            offset = (y * small_w + x) * 3
            raw[offset : offset + 3] = bytes(round(channel * 255) for channel in color)
    return (
        Image.frombytes("RGB", (small_w, small_h), bytes(raw))
        .resize((width, height), Image.Resampling.BICUBIC)
        .convert("RGBA")
    )


def _rainbow_line(scene, points, width=4, opacity=255):
    for index, (a, b) in enumerate(pairwise(points)):
        hue = (index / max(1, len(points) - 1)) % 1
        color = tuple(
            round(channel * 255) for channel in colorsys.hsv_to_rgb(hue, 0.52, 1)
        )
        scene.line((a, b), fill=(*color, opacity), width=width)


def _prismatic_fluid(scene):
    scene.image = _liquid_foil(scene.image.width, scene.image.height)
    scene.draw = ImageDraw.Draw(scene.image)

    def flowing_light(layer):
        for offset in (-170, 80, 290):
            path = _curve(
                (-140, 280 + offset),
                (471, -36 + offset),
                (268, 1210 + offset),
                (1200, 471 + offset),
            )
            _rainbow_line(layer, path, 49, 95)
            layer.line(path, fill=(255, 255, 255, 75), width=12)

    scene.veil(flowing_light, blur=13)
    for offset in (-170, 80, 290):
        path = _curve(
            (-140, 280 + offset),
            (471, -36 + offset),
            (268, 1210 + offset),
            (1200, 471 + offset),
        )
        _rainbow_line(scene, path, 3, 185)

    def prism(layer):
        tip, left, right = (321, 225), (63, 672), (598, 647)
        center = (330, 510)
        layer.polygon(
            (tip, left, right),
            fill=(251, 254, 255, 104),
            outline=(255, 255, 255, 225),
            width=3,
        )
        layer.polygon((tip, left, center), fill=(211, 181, 247, 105))
        layer.polygon((tip, right, center), fill=(192, 246, 242, 135))
        layer.polygon((left, right, center), fill=(254, 235, 193, 115))
        layer.line((tip, center, left), fill=(255, 255, 255, 220), width=2)
        layer.line((center, right), fill=(255, 255, 255, 215), width=2)

    scene.veil(prism)

    def scattered_light(layer):
        for index, color in enumerate(SPECTRUM):
            layer.polygon(
                (
                    (508, 519 + index * 8),
                    (544, 543 + index * 9),
                    (1160, 662 + index * 34),
                    (1122, 630 + index * 35),
                ),
                fill=(*color, 108),
            )
        layer.polygon(
            ((84, 419), (-50, 171), (-30, 103), (110, 382)), fill=(255, 255, 255, 95)
        )

    scene.veil(scattered_light, blur=3)
    for index, color in enumerate(SPECTRUM):
        scene.line(
            ((534, 551 + index * 6), (1130, 724 + index * 31)),
            fill=(*color, 150),
            width=2,
        )
    _stars(
        scene, 76, (26, 178, 1054, 875), ((255, 255, 255), (245, 238, 255)), large=True
    )
    for x, y, radius in ((86, 324, 17), (659, 276, 14), (609, 735, 24), (229, 758, 11)):
        scene.polygon(
            (
                (x, y - radius),
                (x + 4, y - 4),
                (x + radius, y),
                (x + 4, y + 4),
                (x, y + radius),
                (x - 4, y + 4),
                (x - radius, y),
                (x - 4, y - 4),
            ),
            fill=(255, 255, 255),
            outline=(202, 174, 233),
            width=1,
        )
    h = scene.height
    scene.line(
        ((19, 19), (1061, 19), (1061, h - 19), (19, h - 19), (19, 19)),
        (218, 219, 240),
        9,
    )
    for side in (0, 1):
        points = [
            (31 + index * 1018 / 90, 27 if side == 0 else h - 27) for index in range(91)
        ]
        _rainbow_line(scene, points, 4)
    for inset in (25, 32):
        scene.line(((inset, 40), (inset, h - 40)), fill=(255, 255, 255), width=1)
        scene.line(
            ((1080 - inset, 40), (1080 - inset, h - 40)), fill=(255, 255, 255), width=1
        )


PALETTES = {
    "C": (
        (0, (40, 94, 80)),
        (0.24, (54, 129, 91)),
        (0.56, (23, 85, 55)),
        (1, (5, 39, 30)),
    ),
    "R": (
        (0, (8, 20, 48)),
        (0.33, (16, 68, 119)),
        (0.65, (8, 39, 78)),
        (1, (4, 15, 39)),
    ),
    "SR": (
        (0, (15, 11, 35)),
        (0.32, (49, 20, 79)),
        (0.65, (50, 13, 51)),
        (1, (13, 9, 27)),
    ),
    "SSR": (
        (0, (8, 13, 21)),
        (0.37, (15, 23, 34)),
        (0.72, (7, 11, 18)),
        (1, (4, 6, 10)),
    ),
    "SHINY": ((0, (233, 236, 252)), (1, (221, 237, 250))),
}
PAINTERS = {
    "C": _forest,
    "R": _ice_cavern,
    "SR": _nebula_stage,
    "SSR": _obsidian_archive,
    "SHINY": _prismatic_fluid,
}


@lru_cache(maxsize=8)
def _cached_scene(kind, width, height):
    scene = Scene(_gradient(width, height, PALETTES[kind]), random.Random(SEEDS[kind]))
    PAINTERS[kind](scene)
    _grain(scene, 8 if kind == "SHINY" else 11)
    return scene.image.convert("RGB").convert("RGBA")


def render_scene(kind: str, width: int, height: int) -> Image.Image:
    """Return an independent RGBA scene; coordinates scale with card width.

    The 1080px design has its hero around y=210..850. Cached originals remain
    untouched when callers alpha-composite portraits or panels onto the result.
    """
    normalized = str(kind).upper()
    if normalized not in KINDS:
        normalized = "C"
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise ValueError("场景尺寸必须为正数")
    return _cached_scene(normalized, width, height).copy()
