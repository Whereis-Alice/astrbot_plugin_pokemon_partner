"""Five independent Poké Ball display discs, with transparent circular exteriors."""

from __future__ import annotations

import colorsys
import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

KINDS = ("C", "R", "SR", "SSR", "SHINY")
SEEDS = {"C": 7819, "R": 17483, "SR": 38501, "SSR": 65003, "SHINY": 97651}


def _mix(first, second, amount):
    return tuple(round(a + (b - a) * amount) for a, b in zip(first, second))


class Disc:
    def __init__(self, image):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.scale = image.width / 470

    def box(self, values):
        return tuple(round(value * self.scale) for value in values)

    def points(self, values):
        return [(round(x * self.scale), round(y * self.scale)) for x, y in values]

    def ellipse(self, box, fill=None, outline=None, width=1):
        self.draw.ellipse(
            self.box(box),
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

    def polygon(self, points, fill, outline=None, width=1):
        self.draw.polygon(
            self.points(points),
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

    def overlay(self, painter, blur=0):
        layer = Image.new("RGBA", self.image.size)
        painter(Disc(layer))
        if blur:
            layer = layer.filter(ImageFilter.GaussianBlur(blur * self.scale))
        self.image.alpha_composite(layer)
        self.draw = ImageDraw.Draw(self.image)


def _base(size, top, bottom):
    image = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        draw.line((0, y, size, y), fill=(*_mix(top, bottom, y / max(1, size - 1)), 255))
    return image


def _ball_equator(disc, top, bottom, line, button, *, metallic=False):
    # The equator and low-key central clasp are shared structural signatures.
    def hemispheres(layer):
        layer.ellipse((42, 42, 428, 428), fill=top)
        layer.polygon(((42, 235), (428, 235), (428, 432), (42, 432)), fill=bottom)

    disc.overlay(hemispheres)
    disc.line(((43, 235), (427, 235)), fill=line, width=5 if metallic else 4)
    if metallic:
        disc.line(((45, 239), (425, 239)), fill=(255, 238, 181, 135), width=1)
    disc.ellipse((207, 207, 263, 263), fill=button, outline=line, width=3)
    disc.ellipse(
        (218, 218, 252, 252),
        fill=button,
        outline=_mix(line[:3], button[:3], 0.6),
        width=1,
    )
    disc.ellipse((228, 228, 242, 242), outline=line, width=1)


def _metal_ring(disc, radius, width, dark, bright, *, gap=None):
    for angle in range(0, 360, 2):
        if gap and any(start <= angle <= end for start, end in gap):
            continue
        amount = (0.5 + 0.5 * math.cos(math.radians(angle - 225))) ** 1.55
        disc.arc(
            (235 - radius, 235 - radius, 235 + radius, 235 + radius),
            angle,
            angle + 3,
            fill=_mix(dark, bright, amount),
            width=width,
        )


def _observation(disc, rng):
    _ball_equator(
        disc,
        (220, 222, 206, 210),
        (251, 250, 241, 240),
        (190, 202, 180),
        (249, 250, 238),
    )
    disc.ellipse((14, 14, 456, 456), outline=(208, 215, 191), width=2)
    disc.arc((8, 8, 462, 462), 205, 311, (85, 120, 64), 5)
    disc.arc((8, 8, 462, 462), 39, 97, (85, 120, 64), 5)
    disc.arc((22, 22, 448, 448), 106, 198, (225, 226, 203), 2)
    for x, y, rotation in ((78, 102, -0.2), (398, 357, 0.4)):
        for index in range(3):
            xx, yy = x + index * rotation * 20, y + index * 20
            disc.polygon(
                (
                    (xx, yy - 13),
                    (xx + 15, yy - 5),
                    (xx + 11, yy + 12),
                    (xx - 2, yy + 8),
                ),
                fill=(125, 148, 100),
                outline=(102, 130, 75),
            )
            disc.line(
                ((xx + 4, yy - 8), (xx + 5, yy + 9)), fill=(215, 225, 192), width=1
            )
    for angle in range(112, 193, 10):
        a = math.radians(angle)
        disc.line(
            (
                (235 + math.cos(a) * 216, 235 + math.sin(a) * 216),
                (235 + math.cos(a) * 221, 235 + math.sin(a) * 221),
            ),
            (165, 183, 143),
            1,
        )

    def paper(layer):
        for _ in range(125):
            x, y = rng.uniform(53, 417), rng.uniform(53, 417)
            layer.line(((x, y), (x + rng.uniform(2, 6), y)), (151, 162, 127, 19), 1)

    disc.overlay(paper)


def _ice_lens(disc, rng):
    def refractions(layer):
        center = (235, 235)
        for index in range(12):
            first, second = math.radians(index * 30), math.radians((index + 1) * 30)
            a = (235 + math.cos(first) * 205, 235 + math.sin(first) * 205)
            b = (235 + math.cos(second) * 205, 235 + math.sin(second) * 205)
            color = ((212, 243, 252, 135), (113, 184, 223, 93), (235, 251, 255, 165))[
                index % 3
            ]
            layer.polygon((center, a, b), fill=color, outline=(222, 249, 255, 114))
        layer.polygon(
            ((-17, 210), (287, -54), (408, -17), (67, 289)), fill=(255, 255, 255, 56)
        )

    disc.overlay(refractions)
    _ball_equator(
        disc, (151, 205, 230, 72), (215, 244, 250, 82), (119, 169, 197), (217, 243, 252)
    )
    _metal_ring(
        disc, 225, 16, (67, 114, 151), (243, 252, 255), gap=((38, 58), (168, 189))
    )
    disc.ellipse((25, 25, 445, 445), outline=(218, 246, 255), width=2)
    for index in range(12):
        angle = math.radians(index * 30 + 15)
        x, y = 235 + math.cos(angle) * 220, 235 + math.sin(angle) * 220
        disc.polygon(
            ((x, y - 8), (x + 6, y), (x, y + 8), (x - 6, y)),
            fill=(216, 248, 255),
            outline=(91, 151, 190),
            width=1,
        )
    for radius in (160, 176):
        disc.arc(
            (235 - radius, 235 - radius, 235 + radius, 235 + radius),
            49,
            123,
            (240, 254, 255),
            2,
        )


def _amethyst(disc, rng):
    def lighting(layer):
        layer.polygon(
            ((105, 25), (130, 25), (353, 410), (217, 410)), fill=(246, 172, 225, 39)
        )
        layer.polygon(
            ((392, 58), (410, 81), (141, 414), (63, 365)), fill=(192, 160, 253, 40)
        )

    disc.overlay(lighting, blur=6)
    _ball_equator(
        disc,
        (58, 37, 84, 85),
        (40, 26, 63, 90),
        (147, 105, 163),
        (62, 41, 89),
        metallic=True,
    )
    _metal_ring(
        disc, 224, 17, (52, 34, 77), (247, 182, 226), gap=((30, 68), (133, 187))
    )
    disc.arc((30, 30, 440, 440), 197, 319, (188, 130, 209), 3)
    disc.arc((33, 33, 437, 437), 40, 121, (79, 49, 120), 2)
    for angle in range(0, 360, 9):
        a = math.radians(angle)
        disc.line(
            (
                (235 + math.cos(a) * 205, 235 + math.sin(a) * 205),
                (235 + math.cos(a) * 214, 235 + math.sin(a) * 214),
            ),
            (120, 83, 149),
            1,
        )
    for angle in (25, 89, 132, 210, 279):
        a = math.radians(angle)
        x, y = 235 + math.cos(a) * 172, 235 + math.sin(a) * 172
        disc.polygon(((x, y - 5), (x + 4, y), (x, y + 5), (x - 4, y)), (222, 167, 230))
    for _ in range(27):
        x, y = rng.uniform(67, 403), rng.uniform(67, 403)
        disc.ellipse((x - 1, y - 1, x + 1, y + 1), fill=(170, 128, 200))


def _gold_coin(disc, rng):
    def reflection(layer):
        for width, color in (
            (94, (241, 176, 49, 125)),
            (51, (255, 229, 139, 163)),
            (13, (255, 252, 211, 183)),
        ):
            layer.line(((-24, 347), (495, 105)), color, width)

    disc.overlay(reflection, blur=8)
    _ball_equator(
        disc,
        (67, 51, 27, 70),
        (18, 28, 34, 77),
        (189, 148, 67),
        (37, 37, 31),
        metallic=True,
    )
    _metal_ring(disc, 223, 24, (95, 57, 13), (255, 237, 164))
    _metal_ring(disc, 204, 4, (202, 155, 72), (87, 57, 23))
    for radius in (178, 160):
        _metal_ring(disc, radius, 1, (68, 56, 35), (169, 137, 70))
    for angle in range(0, 360, 15):
        a = math.radians(angle)
        disc.line(
            (
                (235 + math.cos(a) * 211, 235 + math.sin(a) * 211),
                (235 + math.cos(a) * 221, 235 + math.sin(a) * 221),
            ),
            (126, 85, 31),
            2,
        )
    nodes = [(rng.uniform(88, 382), rng.uniform(90, 380)) for _ in range(15)]
    for index in range(0, 15, 3):
        disc.line(nodes[index : index + 3], (111, 91, 48), 1)
    for x, y in nodes:
        disc.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=(219, 181, 90))
    for angle in (217, 284, 36, 128):
        a = math.radians(angle)
        x, y = 235 + math.cos(a) * 192, 235 + math.sin(a) * 192
        disc.polygon(
            ((x, y - 6), (x + 5, y), (x, y + 6), (x - 5, y)), fill=(242, 211, 134)
        )


def _holographic(disc, rng):
    def foil(layer):
        for index in range(16):
            a, b = math.radians(index * 22.5), math.radians((index + 1) * 22.5)
            first = (235 + math.cos(a) * 212, 235 + math.sin(a) * 212)
            second = (235 + math.cos(b) * 212, 235 + math.sin(b) * 212)
            color = tuple(
                round(value * 255) for value in colorsys.hsv_to_rgb(index / 16, 0.24, 1)
            )
            layer.polygon(
                ((235, 235), first, second),
                fill=(*color, 78),
                outline=(255, 255, 255, 78),
            )
        layer.polygon(
            ((-24, 120), (66, -25), (496, 311), (472, 376)), fill=(255, 255, 255, 92)
        )

    disc.overlay(foil)
    _ball_equator(
        disc, (241, 229, 251, 85), (230, 247, 249, 91), (187, 191, 213), (251, 252, 255)
    )
    _metal_ring(
        disc, 225, 14, (166, 181, 207), (255, 255, 255), gap=((61, 91), (167, 191))
    )
    for band, start, end in ((0, 192, 330), (1, 11, 125), (2, 240, 317)):
        radius = 221 - band * 9
        for angle in range(start, end, 2):
            hue = ((angle - start) / max(1, end - start) + band * 0.17) % 1
            color = tuple(
                round(value * 255) for value in colorsys.hsv_to_rgb(hue, 0.45, 1)
            )
            disc.arc(
                (235 - radius, 235 - radius, 235 + radius, 235 + radius),
                angle,
                angle + 3,
                color,
                3,
            )
    for radius, start, end in ((171, 43, 152), (164, 199, 277)):
        disc.arc(
            (235 - radius, 235 - radius, 235 + radius, 235 + radius),
            start,
            end,
            (255, 255, 255),
            3,
        )
    for x, y, r in ((104, 103, 10), (355, 373, 12), (375, 135, 6)):
        disc.polygon(
            (
                (x, y - r),
                (x + 2, y - 2),
                (x + r, y),
                (x + 2, y + 2),
                (x, y + r),
                (x - 2, y + 2),
                (x - r, y),
                (x - 2, y - 2),
            ),
            fill=(255, 255, 255),
            outline=(184, 154, 216),
        )


PALETTES = {
    "C": ((244, 244, 225), (251, 250, 239)),
    "R": ((110, 182, 218), (216, 246, 251)),
    "SR": ((53, 32, 83), (78, 48, 99)),
    "SSR": ((23, 31, 39), (47, 44, 34)),
    "SHINY": ((241, 234, 252), (223, 246, 248)),
}
PAINTERS = {
    "C": _observation,
    "R": _ice_lens,
    "SR": _amethyst,
    "SSR": _gold_coin,
    "SHINY": _holographic,
}


@lru_cache(maxsize=10)
def _cached_emblem(kind, diameter):
    size = diameter * 2
    top, bottom = PALETTES[kind]
    disc = Disc(_base(size, top, bottom))
    PAINTERS[kind](disc, random.Random(SEEDS[kind]))
    mask = Image.new("L", (size, size))
    padding = min(2, size // 4)
    ImageDraw.Draw(mask).ellipse(
        (padding, padding, size - padding - 1, size - padding - 1), fill=255
    )
    disc.image.putalpha(mask)
    return disc.image.resize((diameter, diameter), Image.Resampling.LANCZOS)


def render_emblem(kind: str, diameter: int) -> Image.Image:
    """Return a private RGBA ball disc; no text, portraits, or game RNG access."""
    normalized = str(kind).upper()
    if normalized not in KINDS:
        normalized = "C"
    diameter = int(diameter)
    if diameter < 1:
        raise ValueError("圆盘直径必须为正数")
    return _cached_emblem(normalized, diameter).copy()
