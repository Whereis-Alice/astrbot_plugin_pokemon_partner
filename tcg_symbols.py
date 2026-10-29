"""TCG-inspired enamel energy medallions for all eighteen Pokémon types.

The symbols are drawn locally, rather than using emoji, remote artwork or a font.
The familiar ten energy silhouettes retain their visual vocabulary; the other
eight types have separate marks so the main-series type data stays unambiguous.
"""

from __future__ import annotations

import math
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter


TYPE_COLORS = {
    "草": (99, 198, 46), "火": (239, 80, 38), "水": (49, 165, 220),
    "电": (247, 206, 42), "超能力": (195, 118, 189), "格斗": (210, 111, 40),
    "恶": (63, 116, 149), "钢": (171, 178, 174), "龙": (212, 186, 54),
    "一般": (227, 223, 211), "冰": (117, 216, 224), "地面": (183, 140, 74),
    "飞行": (155, 174, 230), "毒": (171, 117, 198), "岩石": (173, 151, 92),
    "虫": (164, 189, 55), "幽灵": (142, 130, 194), "妖精": (241, 160, 194),
}

_ALIASES = dict(zip(
    ("grass", "fire", "water", "lightning", "psychic", "fighting", "darkness",
     "metal", "dragon", "colorless", "ice", "ground", "flying", "poison", "rock",
     "bug", "ghost", "fairy"), TYPE_COLORS,
))
_ALIASES.update({"electric": "电", "dark": "恶", "steel": "钢", "normal": "一般"})


def _mix(a, b, amount):
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _gradient(size, stops):
    """A small vertical source keeps the medallion gradients inexpensive."""
    strip = Image.new("RGBA", (1, size))
    pixels = []
    index = 0
    for y in range(size):
        fraction = y / max(1, size - 1)
        while index + 1 < len(stops) - 1 and fraction > stops[index + 1][0]:
            index += 1
        left, right = stops[index], stops[index + 1]
        amount = (fraction - left[0]) / max(.0001, right[0] - left[0])
        pixels.append(_mix(left[1], right[1], max(0, min(1, amount))))
    strip.putdata(pixels)
    return strip.resize((size, size))


class _Pen:
    def __init__(self, image):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.scale = image.width / 256

    def box(self, box):
        return tuple(round(value * self.scale) for value in box)

    def points(self, points):
        return [(round(x * self.scale), round(y * self.scale)) for x, y in points]

    def ellipse(self, box, fill=255, outline=None, width=1):
        self.draw.ellipse(self.box(box), fill=fill, outline=outline,
                          width=max(1, round(width * self.scale)))

    def polygon(self, points, fill=255):
        self.draw.polygon(self.points(points), fill=fill)

    def line(self, points, fill=255, width=8):
        self.draw.line(self.points(points), fill=fill,
                       width=max(1, round(width * self.scale)), joint="curve")

    def arc(self, box, start, end, fill=255, width=8):
        self.draw.arc(self.box(box), start, end, fill=fill,
                      width=max(1, round(width * self.scale)))


def _curve(a, b, c, d, count=20):
    points = []
    for index in range(count + 1):
        t = index / count
        u = 1 - t
        points.append((u ** 3 * a[0] + 3 * u * u * t * b[0] + 3 * u * t * t * c[0] + t ** 3 * d[0],
                       u ** 3 * a[1] + 3 * u * u * t * b[1] + 3 * u * t * t * c[1] + t ** 3 * d[1]))
    return points


def _star(cx, cy, outer, inner, arms, angle=-math.pi / 2):
    return [(cx + (outer if index % 2 == 0 else inner) * math.cos(angle + index * math.pi / arms),
             cy + (outer if index % 2 == 0 else inner) * math.sin(angle + index * math.pi / arms))
            for index in range(arms * 2)]


def _glyph(kind, size):
    mask = Image.new("L", (size, size))
    pen = _Pen(mask)
    if kind == "草":
        pen.polygon(_curve((65, 185), (61, 110), (102, 63), (188, 52))
                    + _curve((188, 52), (178, 105), (186, 150), (65, 185)))
        pen.line([(60, 196), (91, 163), (133, 114), (177, 69)], width=10)
        for points in ([(100, 151), (87, 119)], [(122, 125), (124, 89)],
                       [(141, 105), (164, 111)], [(86, 169), (110, 172)]):
            pen.line(points, fill=0, width=5)
    elif kind == "火":
        pen.polygon(_curve((77, 186), (47, 159), (77, 125), (78, 106))
                    + _curve((78, 106), (78, 128), (91, 130), (96, 135))
                    + _curve((96, 135), (115, 109), (99, 69), (128, 43))
                    + _curve((128, 43), (126, 74), (178, 84), (167, 122))
                    + _curve((167, 122), (187, 100), (197, 126), (196, 145))
                    + _curve((196, 145), (206, 184), (162, 209), (127, 210))
                    + _curve((127, 210), (105, 210), (87, 200), (77, 186)))
        pen.polygon(_curve((121, 192), (104, 174), (119, 157), (132, 145))
                    + _curve((132, 145), (134, 163), (151, 171), (146, 187))
                    + _curve((146, 187), (143, 201), (129, 199), (121, 192)), fill=0)
    elif kind == "水":
        pen.polygon(_curve((128, 42), (108, 75), (64, 112), (64, 150))
                    + _curve((64, 150), (64, 194), (96, 216), (129, 217))
                    + _curve((129, 217), (177, 219), (207, 182), (192, 144))
                    + _curve((192, 144), (180, 105), (153, 77), (128, 42)))
        pen.arc((89, 127, 145, 183), 85, 183, fill=0, width=12)
    elif kind == "电":
        pen.polygon([(150, 43), (70, 137), (119, 137), (103, 219),
                     (186, 112), (135, 112)])
    elif kind == "超能力":
        pen.polygon(_curve((42, 129), (89, 65), (169, 65), (214, 129))
                    + _curve((214, 129), (169, 194), (89, 194), (42, 129)))
        pen.polygon(_curve((61, 129), (94, 92), (164, 92), (195, 129))
                    + _curve((195, 129), (164, 166), (93, 166), (61, 129)), fill=0)
        pen.ellipse((101, 100, 156, 158))
        pen.ellipse((119, 119, 139, 140), fill=0)
    elif kind == "格斗":
        pen.polygon([(71, 157), (58, 145), (53, 122), (60, 106), (73, 101),
                     (73, 69), (89, 61), (105, 66), (111, 55), (131, 50),
                     (145, 62), (159, 61), (176, 67), (179, 80), (191, 87),
                     (194, 112), (180, 160), (158, 185), (160, 213), (99, 213),
                     (97, 177), (84, 172)])
        pen.line([(77, 113), (126, 130), (151, 112), (180, 110)], fill=0, width=6)
        for points in ([(106, 69), (109, 101)], [(141, 66), (142, 99)],
                       [(172, 81), (166, 103)], [(101, 186), (156, 186)]):
            pen.line(points, fill=0, width=5)
    elif kind == "恶":
        pen.ellipse((57, 58, 199, 204))
        pen.ellipse((92, 37, 207, 159), fill=0)
        pen.polygon(_star(178, 84, 18, 7, 4))
        pen.ellipse((191, 119, 204, 132))
    elif kind == "钢":
        pen.polygon([(128, 48), (198, 89), (198, 167), (128, 208), (58, 167), (58, 89)])
        pen.polygon([(128, 73), (176, 101), (176, 155), (128, 183), (80, 155), (80, 101)], fill=0)
        pen.polygon([(89, 111), (167, 111), (128, 172)])
        pen.polygon([(113, 123), (143, 123), (128, 148)], fill=0)
    elif kind == "龙":
        pen.polygon(_curve((172, 52), (116, 35), (52, 73), (55, 132))
                    + _curve((55, 132), (59, 192), (132, 225), (188, 184))
                    + [(192, 148)]
                    + _curve((192, 148), (163, 183), (112, 179), (90, 147))
                    + _curve((90, 147), (61, 108), (111, 63), (165, 69)))
        pen.polygon([(99, 102), (97, 61), (119, 78), (142, 78), (152, 94),
                     (177, 86), (183, 53), (197, 87), (191, 112), (167, 124),
                     (181, 141), (147, 153), (125, 139), (134, 122), (109, 121)])
        pen.polygon([(143, 103), (160, 104), (150, 114)], fill=0)
    elif kind == "一般":
        pen.polygon(_star(128, 128, 78, 30, 6))
    elif kind == "冰":
        for angle in range(0, 360, 60):
            rad = math.radians(angle)
            end = (128 + 78 * math.cos(rad), 128 + 78 * math.sin(rad))
            pen.line([(128, 128), end], width=13)
            for length in (45, 65):
                joint = (128 + length * math.cos(rad), 128 + length * math.sin(rad))
                for delta in (-math.pi / 3, math.pi / 3):
                    pen.line([joint, (joint[0] - 22 * math.cos(rad + delta),
                                     joint[1] - 22 * math.sin(rad + delta))], width=8)
    elif kind == "地面":
        pen.polygon([(42, 181), (84, 85), (111, 126), (147, 56), (214, 181)])
        pen.polygon([(85, 106), (103, 146), (75, 139)], fill=0)
        pen.polygon([(147, 79), (169, 122), (140, 114)], fill=0)
        pen.line([(49, 200), (207, 200)], width=12)
    elif kind == "飞行":
        pen.polygon(_curve((60, 196), (50, 135), (115, 69), (202, 66))
                    + [(181, 97), (200, 90), (167, 125), (186, 116),
                       (144, 156), (162, 149), (110, 184), (129, 179), (81, 202)])
        pen.line([(83, 174), (151, 112)], fill=0, width=6)
    elif kind == "毒":
        pen.ellipse((71, 94, 184, 196))
        pen.polygon([(91, 163), (102, 210), (154, 210), (165, 163)])
        pen.ellipse((54, 58, 92, 96))
        pen.ellipse((174, 63, 211, 100))
        pen.ellipse((94, 121, 117, 151), fill=0)
        pen.ellipse((139, 121, 162, 151), fill=0)
        pen.line([(108, 180), (148, 180)], fill=0, width=6)
        pen.line([(119, 173), (119, 191)], fill=0, width=5)
        pen.line([(137, 173), (137, 191)], fill=0, width=5)
    elif kind == "岩石":
        pen.polygon([(111, 63), (149, 52), (179, 100), (166, 199),
                     (104, 213), (79, 147), (91, 86)])
        pen.line([(111, 67), (129, 122), (107, 207)], fill=0, width=6)
        pen.line([(131, 121), (174, 101)], fill=0, width=6)
        pen.line([(133, 126), (163, 194)], fill=0, width=5)
        pen.polygon([(41, 131), (64, 114), (78, 163), (61, 192), (40, 170)])
        pen.polygon([(194, 117), (213, 136), (213, 181), (183, 191)])
    elif kind == "虫":
        pen.ellipse((86, 100, 170, 189))
        pen.ellipse((103, 72, 153, 122))
        pen.line([(113, 85), (92, 56), (70, 48)], width=10)
        pen.line([(143, 85), (164, 56), (186, 48)], width=10)
        for y, outward in ((117, -19), (143, 0), (170, 20)):
            pen.line([(94, y), (66, y + outward), (52, y + outward + 16)], width=10)
            pen.line([(162, y), (190, y + outward), (204, y + outward + 16)], width=10)
        pen.line([(128, 109), (128, 184)], fill=0, width=7)
        pen.ellipse((111, 87, 121, 97), fill=0)
        pen.ellipse((135, 87, 145, 97), fill=0)
    elif kind == "幽灵":
        pen.polygon(_curve((66, 201), (46, 158), (57, 79), (98, 58))
                    + _curve((98, 58), (141, 36), (185, 67), (194, 113))
                    + _curve((194, 113), (200, 153), (170, 187), (162, 207))
                    + [(144, 186), (126, 208), (108, 186), (88, 207), (79, 184)])
        pen.ellipse((88, 104, 113, 138), fill=0)
        pen.ellipse((141, 104, 166, 138), fill=0)
        pen.polygon([(118, 150), (128, 170), (139, 150)], fill=0)
    elif kind == "妖精":
        pen.polygon(_curve((128, 181), (102, 165), (62, 136), (69, 104))
                    + _curve((69, 104), (77, 77), (117, 76), (128, 100))
                    + _curve((128, 100), (139, 75), (180, 76), (188, 106))
                    + _curve((188, 106), (198, 140), (151, 167), (128, 181)))
        pen.polygon(_star(128, 58, 24, 7, 4))
        pen.polygon(_star(49, 158, 18, 6, 4))
        pen.polygon(_star(208, 158, 18, 6, 4))
    return mask


@lru_cache(maxsize=128)
def _render(kind, size):
    pixels = max(256, size * 3)
    base = Image.new("RGBA", (pixels, pixels))
    pen = _Pen(base)
    color = TYPE_COLORS[kind]

    shadow = Image.new("RGBA", base.size)
    _Pen(shadow).ellipse((8, 11, 248, 251), fill=(5, 10, 18, 125))
    base.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(pixels / 128)))
    pen = _Pen(base)
    pen.ellipse((8, 8, 248, 248), fill=(14, 21, 25, 255))

    rim_mask = Image.new("L", base.size)
    _Pen(rim_mask).ellipse((13, 13, 243, 243))
    rim = _gradient(pixels, [(0, (239, 245, 242, 255)), (.24, (131, 151, 157, 255)),
                            (.46, (43, 54, 62, 255)), (.69, (63, 72, 78, 255)),
                            (.88, (180, 190, 187, 255)), (1, (72, 85, 87, 255))])
    base.alpha_composite(Image.composite(rim, Image.new("RGBA", base.size), rim_mask))
    pen = _Pen(base)
    pen.ellipse((24, 24, 232, 232), fill=(18, 32, 34, 255))
    sphere_mask = Image.new("L", base.size)
    _Pen(sphere_mask).ellipse((29, 29, 227, 227))
    sphere = _gradient(pixels, [(0, (*_mix(color, (255, 255, 230), .4), 255)),
                               (.28, (*_mix(color, (245, 255, 235), .25), 255)),
                               (.57, (*color, 255)),
                               (1, (*_mix(color, (5, 27, 36), .67), 255))])
    base.alpha_composite(Image.composite(sphere, Image.new("RGBA", base.size), sphere_mask))

    sheen = Image.new("RGBA", base.size)
    sheen_pen = _Pen(sheen)
    sheen_pen.ellipse((32, 26, 204, 151), fill=(255, 255, 238, 36))
    sheen_pen.ellipse((45, 33, 187, 104), fill=(255, 255, 247, 35))
    base.alpha_composite(Image.composite(sheen.filter(ImageFilter.GaussianBlur(pixels / 40)),
                                       Image.new("RGBA", base.size), sphere_mask))
    pen = _Pen(base)
    pen.arc((17, 17, 239, 239), 187, 292, fill=(249, 255, 250, 210), width=3)
    pen.arc((24, 24, 232, 232), 34, 139, fill=(*_mix(color, (235, 255, 233), .42), 255), width=4)
    pen.arc((33, 33, 223, 223), 197, 269, fill=(*_mix(color, (255, 255, 242), .59), 230), width=3)

    glyph = _glyph(kind, pixels)
    lip = Image.new("L", base.size)
    lip.paste(glyph, (0, max(1, round(pixels / 160))))
    base.alpha_composite(Image.composite(Image.new("RGBA", base.size, (255, 255, 229, 86)),
                                       Image.new("RGBA", base.size), lip))
    ink = (12, 23, 27, 255) if kind in {"水", "恶", "钢", "冰", "飞行"} else (19, 22, 18, 255)
    base.alpha_composite(Image.composite(Image.new("RGBA", base.size, ink),
                                       Image.new("RGBA", base.size), glyph))
    return base.resize((size, size), Image.Resampling.LANCZOS)


def render_energy_icon(type_name: str, size: int = 64) -> Image.Image:
    """Return an isolated RGBA energy medallion at the requested pixel size."""
    if isinstance(size, bool) or not isinstance(size, int) or not 8 <= size <= 512:
        raise ValueError("能量图标尺寸须为 8 至 512 的整数")
    name = str(type_name or "").strip()
    kind = name if name in TYPE_COLORS else _ALIASES.get(name.casefold(), "一般")
    return _render(kind, size).copy()
