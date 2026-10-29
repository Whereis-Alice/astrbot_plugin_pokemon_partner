"""Deterministic paper, reverse foil and special-foil TCG print materials.

The images contain print stock and scenery only.  Card geometry, portraits and
text are composed by the caller.  Small raster fields provide grain and optical
colour; fine linework is drawn at the requested size so engraving stays crisp.
The two public functions share twelve cache entries and always return copies.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

try:
    from .tcg_symbols import TYPE_COLORS
except ImportError:
    from tcg_symbols import TYPE_COLORS


_KINDS = frozenset({"C", "R", "SR", "SSR", "SHINY"})
_TAU = math.tau
_ALIASES = dict(zip(
    ("grass", "fire", "water", "electric", "psychic", "fighting", "dark",
     "steel", "dragon", "normal", "ice", "ground", "flying", "poison",
     "rock", "bug", "ghost", "fairy"),
    ("草", "火", "水", "电", "超能力", "格斗", "恶", "钢", "龙", "一般",
     "冰", "地面", "飞行", "毒", "岩石", "虫", "幽灵", "妖精"),
))
_ALIASES.update({"lightning": "电", "darkness": "恶", "metal": "钢", "colorless": "一般"})


def _mix(left, right, amount):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(a + (b - a) * amount) for a, b in zip(left, right))


def _rgb(red, green, blue):
    return tuple(max(0, min(255, round(value))) for value in (red, green, blue))


def _spectrum(phase):
    """A continuous optical interference colour, rather than a flat hue wash."""
    return _rgb(*(137 + 112 * math.cos(_TAU * (phase - shift))
                  for shift in (0, 1 / 3, 2 / 3)))


def _seed(kind, type_name, art=False):
    return 4309 + sum((index + 13) * ord(char)
                      for index, char in enumerate(kind + type_name)) + 901 * art


def _normalise(kind, type_name, width, height):
    kind = str(kind).upper()
    if kind not in _KINDS:
        kind = "C"
    type_name = _ALIASES.get(str(type_name).lower(), str(type_name))
    if type_name not in TYPE_COLORS:
        type_name = "一般"
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise ValueError("TCG material dimensions must be positive")
    return kind, type_name, width, height


def _field(kind, type_name, width, height, art=False):
    """Compute grain, highlights and interference below display resolution."""
    scale = min(1.0, 360 / max(width, height * .72))
    size = max(1, round(width * scale)), max(1, round(height * scale))
    rng = random.Random(_seed(kind, type_name, art))
    type_colour = TYPE_COLORS[type_name]
    shade = _mix(type_colour, (20, 29, 43), .70)
    pixels = []
    for y in range(size[1]):
        v = y / max(1, size[1] - 1)
        for x in range(size[0]):
            u = x / max(1, size[0] - 1)
            grain = rng.uniform(-1.0, 1.0)
            edge = min(1.0, ((u - .48) ** 2 + (v - .36) ** 2) * 1.7)
            glow = math.exp(-((u - .38) ** 2 / .22 + (v - .22) ** 2 / .11))
            if kind == "C":
                if art:
                    sky = _mix((239, 237, 214), type_colour, .29)
                    bottom = _mix(type_colour, (111, 125, 112), .52)
                    colour = _mix(sky, bottom, v * .34)
                    colour = _mix(colour, (255, 248, 222), glow * .40)
                else:
                    colour = _mix((249, 239, 210), type_colour, .055 + v * .035)
                    colour = _mix(colour, (218, 195, 143), edge * .12)
                noise = grain * 3 + math.sin(x * .93 + y * .17) * .7
            elif kind == "R":
                brushed = math.sin(y * 4.83 + .32 * math.sin(x * .2))
                if art:
                    colour = _mix(shade, type_colour, .17 + glow * .32)
                    colour = _mix(colour, (150, 182, 192), glow * .22)
                else:
                    metal = 178 + 42 * glow - 17 * edge + 7 * math.sin((u + v * .22) * 8)
                    colour = _mix((metal, metal + 4, metal + 7), type_colour, .15)
                # Narrow interference bands occupy only the reflected side of silver.
                band = math.exp(-((u - .75 + .18 * math.sin(v * 5)) / .055) ** 2)
                band += .65 * math.exp(-((u - .17 + v * .22) / .035) ** 2)
                colour = _mix(colour, _spectrum(u * 2.7 + v * .8), band * (.21 if art else .20))
                noise = grain * 2.2 + brushed * 1.3
            elif kind == "SR":
                wave = u * .83 + v * .56 + .125 * math.sin(v * 7 + u * 4)
                pearl = _spectrum(wave + .14 * math.sin(u * 11 - v * 6))
                metal = _mix((40, 71, 99), type_colour, .26)
                colour = _mix(metal, pearl, .50 + .19 * math.sin((u - v) * 5))
                ridge = math.exp(-((u - .72 + v * .36 + .06 * math.sin(v * 9)) / .095) ** 2)
                colour = _mix(colour, (248, 232, 199), ridge * .46 + glow * .18)
                colour = _mix(colour, (18, 29, 69), edge * .20)
                noise = grain * 3.0
            elif kind == "SSR":
                gold = .5 + .5 * math.sin(u * 5.5 + v * 2.8)
                streak = math.exp(-((u - .27 + v * .27) / .13) ** 2)
                colour = _mix((153, 99, 23), (238, 195, 80), .28 + gold * .34 + glow * .18)
                colour = _mix(colour, (255, 243, 170), streak * (.51 if art else .64))
                colour = _mix(colour, (91, 62, 17), edge * .16)
                noise = grain * 2.6
            else:
                phase = u * 1.4 - v * .8 + .12 * math.sin(v * 9 + u * 6)
                pearl = _mix((236, 240, 246), _spectrum(phase), .18 + .10 * edge)
                colour = _mix(pearl, (255, 252, 227), glow * .48)
                band = math.exp(-((u - .65 + v * .38 + .05 * math.sin(v * 8)) / .055) ** 2)
                colour = _mix(colour, _spectrum(phase + .2), band * .24)
                noise = grain * 2.0
            pixels.append(_rgb(*(channel + noise for channel in colour)) + (255,))
    field = Image.new("RGBA", size)
    field.putdata(pixels)
    return field.resize((width, height), Image.Resampling.BICUBIC)


class _Ink:
    """Normalised drawing coordinates preserve the material at any aspect ratio."""

    def __init__(self, image):
        self.image = image
        self.draw = ImageDraw.Draw(image)
        self.width, self.height = image.size
        self.unit = max(.1, min(self.width, self.height) / 900)

    def points(self, points):
        return [(round(x * self.width), round(y * self.height)) for x, y in points]

    def line(self, points, colour, width=1):
        self.draw.line(self.points(points), fill=colour,
                       width=max(1, round(width * self.unit)), joint="curve")

    def polygon(self, points, colour):
        self.draw.polygon(self.points(points), fill=colour)

    def ellipse(self, box, colour):
        x0, y0, x1, y1 = box
        self.draw.ellipse((round(x0 * self.width), round(y0 * self.height),
                           round(x1 * self.width), round(y1 * self.height)), fill=colour)


def _layer(image, paint, blur=0):
    overlay = Image.new("RGBA", image.size)
    paint(_Ink(overlay))
    if blur:
        overlay = overlay.filter(ImageFilter.GaussianBlur(blur))
    image.alpha_composite(overlay)


def _four_star(ink, x, y, radius, colour, horizontal=.68):
    ry = radius * ink.width / ink.height
    points = [(x, y - ry), (x + radius * .16, y - ry * .16),
              (x + radius * horizontal, y), (x + radius * .16, y + ry * .16),
              (x, y + ry), (x - radius * .16, y + ry * .16),
              (x - radius * horizontal, y), (x - radius * .16, y - ry * .16)]
    ink.polygon(points, colour)


def _stars(image, seed, count, shiny=False, gold=False, muted=False):
    rng = random.Random(seed)

    def paint(ink):
        for index in range(count):
            x, y = rng.random(), rng.random()
            radius = rng.uniform(.0008, .0028)
            alpha = rng.randrange(40, 116) if muted else rng.randrange(92, 212)
            colour = (239, 194, 98, alpha) if gold else (246, 249, 255, alpha)
            if shiny and index % 4 == 0:
                radius = rng.uniform(.008, .018)
                _four_star(ink, x, y, radius, (255, 255, 250, alpha))
                _four_star(ink, x, y, radius * .44, (255, 255, 255, min(245, alpha + 50)))
            else:
                ry = radius * ink.width / ink.height
                ink.ellipse((x - radius, y - ry, x + radius, y + ry), colour)
    _layer(image, paint)


def _paper_fibres(image, seed, art=False):
    rng = random.Random(seed)

    def paint(ink):
        count = min(6000, max(140, round(image.width * image.height / 190)))
        for index in range(count):
            x, y = rng.random(), rng.random()
            length = rng.uniform(.0005, .0046)
            angle = rng.uniform(0, _TAU)
            delta = length * math.sin(angle) * ink.width / ink.height
            colour = (103, 81, 43, 12 if art else 17) if index % 3 else (255, 254, 242, 30)
            ink.line([(x, y), (x + length * math.cos(angle), y + delta)], colour)
    _layer(image, paint)


def _type_underprint(image, type_name):
    """Small dispersed line glyphs imitate the reverse-foil underprint sheet."""
    def paint(ink):
        tone = (22, 44, 55, 21)
        step = .145
        for row in range(-1, 11):
            for col in range(-1, 9):
                cx, cy = col * step + (row % 2) * step / 2, row * step * .73
                sx, sy = .028, .028 * ink.width / ink.height
                if type_name in {"草", "虫"}:
                    leaf = [(cx - sx, cy + sy), (cx - sx * .65, cy - sy * .1),
                            (cx + sx, cy - sy), (cx + sx * .75, cy + sy * .2), (cx - sx, cy + sy)]
                    ink.line(leaf, tone)
                    ink.line([(cx - sx, cy + sy), (cx + sx, cy - sy)], tone)
                elif type_name in {"水", "冰", "飞行"}:
                    for offset in (-.6, .3):
                        points = [(cx + sx * (t / 8 - 1), cy + sy * (offset + .26 * math.sin(t * .65)))
                                  for t in range(17)]
                        ink.line(points, tone)
                elif type_name in {"火", "电", "龙"}:
                    ink.line([(cx + sx * .5, cy - sy), (cx - sx * .4, cy - sy * .05),
                              (cx + sx * .35, cy - sy * .05), (cx - sx * .5, cy + sy)], tone)
                elif type_name in {"幽灵", "超能力", "妖精", "恶"}:
                    _four_star(ink, cx, cy, sx * .65, tone)
                    ink.line([(cx - sx, cy), (cx, cy - sy), (cx + sx, cy), (cx, cy + sy)], tone)
                else:
                    ink.line([(cx, cy - sy), (cx + sx, cy - sy * .35), (cx + sx * .6, cy + sy),
                              (cx - sx * .6, cy + sy), (cx - sx, cy - sy * .35), (cx, cy - sy)], tone)
                    ink.line([(cx - sx, cy - sy * .35), (cx + sx, cy - sy * .35)], tone)
    _layer(image, paint)


def _silver_brush(image, seed):
    rng = random.Random(seed)

    def paint(ink):
        count = min(1800, max(100, image.height))
        for _ in range(count):
            y, x = rng.random(), rng.uniform(-.06, .92)
            length = rng.uniform(.015, .18)
            tone = (249, 253, 255, rng.randrange(5, 19)) if rng.random() > .45 else (21, 43, 50, rng.randrange(4, 13))
            ink.line([(x, y), (x + length, y + rng.uniform(-.0007, .0007))], tone)
        for offset in (.15, .73):
            for strand in range(11):
                points = [(offset + strand * .0028 - v * .17 + .022 * math.sin(v * 9), v)
                          for v in (index / 170 for index in range(171))]
                hue = _spectrum(strand / 11 + offset)
                ink.line(points, hue + (28,), 1.0)
    _layer(image, paint)


def _rainbow_engraving(image, art=False):
    """Three independent swept line families give full-art foil embossed relief."""
    def paint(ink):
        families = ((.045, .115, 6.2, .42), (.11, .07, 9.6, -.31), (.22, .043, 13.5, .58))
        for family, (amplitude, ripple, frequency, slope) in enumerate(families):
            count = 57 if art else 88
            for line_index in range(-12, count):
                base = line_index / (count - 15)
                phase = line_index * .055 + family * .74
                points = []
                for index in range(171):
                    u = index / 170
                    v = base + slope * (u - .5) + amplitude * math.sin(u * frequency + phase)
                    v += ripple * math.sin(u * frequency * .37 - base * 4 + family)
                    points.append((u, v))
                colour = _mix(_spectrum(base * .72 + family / 3), (240, 246, 247), .52)
                alpha = (39, 29, 22)[family] if art else (62, 43, 34)[family]
                ink.line([(u, v + .0010) for u, v in points], (12, 21, 51, alpha // 2), 1.1)
                ink.line(points, colour + (alpha,), 1.0)
        # Woven micro-engraving at two sides, with a sparse unpatterned centre.
        for index in range(35):
            offset = index * .006
            for side in (-.15, .82):
                points = [(side + offset + .045 * math.sin(v * 12 + index * .12), v)
                          for v in (n / 130 for n in range(131))]
                ink.line(points, (233, 226, 255, 29), .9)
    _layer(image, paint)


def _gold_etch(image, art=False):
    """An engraved wave lattice and fan-shaped guilloche, free of central disks."""
    def paint(ink):
        for line_index in range(-30, 130):
            offset = line_index * .010
            points = []
            for index in range(191):
                u = index / 190
                v = offset + .20 * math.sin(u * 5.4 + offset * 3.6) + .04 * math.sin(u * 18 - offset * 12)
                points.append((u, v))
            ink.line([(u, v + .001) for u, v in points], (4, 7, 8, 98), 1.3)
            ink.line(points, (180, 135, 48, 35 if art else 49), .95)
        # Banknote-like interlaced rosette rays originate beyond the crop.
        cx, cy = (.98, 1.13) if art else (1.16, .58)
        for ray in range(111):
            angle = -math.pi + ray * .0143
            points = []
            for index in range(201):
                radius = .06 + index / 200 * 1.65
                twist = angle + .028 * math.sin(radius * 36 + ray * .23)
                x = cx + math.cos(twist) * radius
                y = cy + math.sin(twist) * radius * ink.width / ink.height
                points.append((x, y))
            colour = (238, 191, 83, 57 if ray % 5 == 0 else 29)
            ink.line(points, colour, .9)
        # Fine diagonal gold leaf seams have uneven, slightly doubled edges.
        for seam in (.16, .75):
            points = [(seam + v * .21 + .009 * math.sin(v * 28), v)
                      for v in (n / 220 for n in range(221))]
            ink.line(points, (14, 13, 8, 148), 4)
            ink.line([(u + .0013, v) for u, v in points], (234, 183, 61, 90), 1.3)
    _layer(image, paint)


def _gold_granules(image, seed):
    rng = random.Random(seed)

    def paint(ink):
        count = min(7600, max(300, image.width * image.height // 120))
        for index in range(count):
            x, y = rng.random(), rng.random()
            radius = rng.uniform(.00025, .00115)
            ry = radius * ink.width / ink.height
            brightness = rng.randrange(100, 218)
            alpha = rng.randrange(22, 69) if index % 7 else rng.randrange(65, 137)
            ink.ellipse((x, y, x + radius, y + ry), (brightness, round(brightness * .76), round(brightness * .32), alpha))
    _layer(image, paint)


def _pearl_contours(image, art=False):
    def paint(ink):
        for index in range(-20, 85):
            base = index * .018
            points = []
            for n in range(181):
                u = n / 180
                v = base - u * .34 + .048 * math.sin(u * 8 + base * 4)
                v += .027 * math.sin(u * 17 - base * 6)
                points.append((u, v))
            colour = _mix(_spectrum(base + .2), (255, 251, 255), .41)
            ink.line([(u, v + .001) for u, v in points], (106, 140, 166, 17), 1)
            ink.line(points, colour + (40 if art else 66,), 1.25)
        # Four-point foil stamps are deliberately an array, not random confetti.
        cols, rows = 10, 14 if image.height > image.width else 7
        for row in range(rows):
            for col in range(cols):
                x = (col + .42 + (row % 2) * .47) / cols
                y = (row + .56) / rows
                radius = .014 + .004 * math.sin(row * 3 + col)
                tint = _mix(_spectrum(col * .13 - row * .10), (255, 255, 255), .65)
                _four_star(ink, x + .001, y + .0007, radius, (130, 148, 179, 31))
                _four_star(ink, x, y, radius, tint + (99 if art else 135,))
                _four_star(ink, x, y, radius * .44, (255, 255, 255, 164))
    _layer(image, paint)


def _landscape(image, type_name, seed, silver=False):
    """Layered illustrated habitat, with brushy silhouettes around the portrait."""
    rng = random.Random(seed)
    base = TYPE_COLORS[type_name]
    water = type_name in {"水", "冰"}
    forest = type_name in {"草", "虫", "一般", "妖精"}
    dark = type_name in {"恶", "幽灵", "毒", "超能力"}
    hot = type_name in {"火", "龙"}
    rock = type_name in {"格斗", "地面", "岩石", "钢", "电"}
    if dark and not silver:
        tint = Image.new("RGBA", image.size, _mix(base, (78, 67, 111), .40) + (64,))
        image.alpha_composite(tint)

    def haze(ink):
        for cloud in range(14):
            x = rng.uniform(-.14, .95)
            y = rng.uniform(.10, .55)
            length = rng.uniform(.10, .30)
            depth = rng.uniform(.015, .055)
            colour = (220, 231, 239, 14) if silver else (255, 249, 226, 31)
            ink.ellipse((x, y, x + length, y + depth), colour)
        # Soft sun/moon illuminates scenery behind, above the central subject.
        glow = (233, 239, 246, 27) if silver else (255, 246, 204, 47)
        ink.ellipse((.08, .02, .38, .45), glow)
    _layer(image, haze, max(1, image.width / 100))

    def terrain(ink):
        for layer_index, level in enumerate((.56, .69, .84)):
            points = [(-.03, 1.03)]
            for n in range(81):
                x = n / 80
                y = level + .025 * math.sin(x * (8 + layer_index * 2) + layer_index * 3)
                y += .035 * math.sin(x * 18 + layer_index * 1.8)
                if rock or hot or type_name in {"冰", "龙", "飞行"}:
                    y -= .12 * abs(math.sin(x * 13 + layer_index * 2))
                points.append((x, y))
            points.append((1.03, 1.03))
            if silver:
                colour = _mix(base, (27, 46, 65), .69 - layer_index * .08) + (185,)
            elif hot:
                colour = _mix((191, 134, 102), base, .2 + layer_index * .16) + (170 + layer_index * 22,)
            elif dark:
                colour = _mix(base, (44, 57, 83), .32 + layer_index * .2) + (171 + layer_index * 28,)
            else:
                colour = _mix(base, (91, 130, 101), .47 + layer_index * .12) + (135 + layer_index * 42,)
            ink.polygon(points, colour)
            ink.line(points[1:-1], _mix(colour[:3], (250, 247, 218), .5) + (56,), 2.2)
        if water:
            ink.polygon([(0, .72), (.39, .73), (.56, .59), (.65, .60), (.49, .80), (.83, 1), (0, 1)],
                        _mix(base, (184, 225, 228), .5) + (155,))
            for index in range(32):
                y = .72 + index * .009
                x = .30 + .09 * math.sin(index * .29)
                ink.line([(x - .15, y), (x + .11 + index * .004, y)], (240, 249, 234, 71), 1.1)
        if hot:
            path = [(.15 + .56 * (n / 60) + .03 * math.sin(n * .13), .64 + n / 60 * .40) for n in range(61)]
            ink.line(path, (189, 84, 32, 81), 13)
            ink.line(path, (250, 176, 67, 94), 4)
        # Watercolour brush strokes and flecks break the clean terrain edges.
        for index in range(520):
            x, y = rng.random(), rng.uniform(.56, 1.02)
            colour = _mix(base, (255, 244, 208), rng.uniform(.25, .75)) + (rng.randrange(10, 31),)
            ink.line([(x, y), (x + rng.uniform(.002, .025), y + rng.uniform(-.005, .005))], colour, rng.uniform(1, 5))
        if forest or type_name in {"飞行", "毒", "幽灵"}:
            for side in (0, 1):
                for index in range(7):
                    x = side + (1 if side == 0 else -1) * rng.uniform(-.035, .19)
                    y = rng.uniform(.67, .99)
                    size = rng.uniform(.045, .105)
                    tone = _mix(base, (48, 83, 71), .62) + (170,)
                    if silver:
                        tone = _mix(base, (22, 44, 59), .70) + (181,)
                    ink.line([(x, y + size), (x - size * .13, y - size * .70)], tone, 3.5)
                    for branch in range(8):
                        bx = x + rng.uniform(-size * .57, size * .57)
                        by = y - size * .45 + rng.uniform(-size * .6, size * .3)
                        ink.ellipse((bx - size * .44, by - size * .3, bx + size * .40, by + size * .24), tone)
        if rock or type_name in {"冰", "龙", "恶"}:
            for side in (0, 1):
                for index in range(6):
                    x = side + (1 if side == 0 else -1) * rng.uniform(-.02, .17)
                    y, size = rng.uniform(.73, 1.04), rng.uniform(.05, .14)
                    tone = _mix(base, (81, 93, 105), .54)
                    if type_name == "冰":
                        tone = _mix(base, (226, 245, 241), .45)
                    ink.polygon([(x - size * .4, y), (x - size * .2, y - size),
                                 (x + size * .17, y - size * .9), (x + size * .5, y)], tone + (180,))
                    ink.polygon([(x - size * .2, y - size), (x + size * .02, y - size * .63),
                                 (x + size * .13, y), (x - size * .4, y)], _mix(tone, (235, 239, 213), .4) + (82,))
                    ink.line([(x - size * .2, y - size), (x + size * .17, y - size * .9)], (245, 242, 213, 88), 1)
    _layer(image, terrain)
    if silver:
        _stars(image, seed + 20, 125, muted=True)
    elif type_name in {"妖精", "电", "毒", "幽灵", "超能力"}:
        _stars(image, seed + 20, 35, shiny=type_name == "妖精", muted=True)
    _paper_fibres(image, seed + 41, art=True)


def _cloud_flow(image, type_name, seed, shiny=False):
    rng = random.Random(seed)
    base = TYPE_COLORS[type_name]

    def mist(ink):
        for family in range(6):
            points = []
            for n in range(121):
                u = n / 120
                v = .16 + family * .139 - .15 * u + .09 * math.sin(u * 5.7 + family * .65)
                points.append((u, v))
            colour = _mix(_spectrum(family / 5 + .32), base, .32)
            colour = _mix(colour, (249, 248, 255), .62 if shiny else .16)
            ink.line(points, colour + (90 if shiny else 46,), 47 + family * 4)
    _layer(image, mist, max(1, image.width / 48))

    def currents(ink):
        for family in range(7):
            for strand in range(5):
                points = []
                for n in range(161):
                    u = n / 160
                    v = .02 + family * .18 + strand * .002 - u * .32
                    v += .085 * math.sin(u * 5.8 + family * .72) + .012 * math.sin(u * 23 + family)
                    points.append((u, v))
                colour = _mix(_spectrum(family / 7 + strand * .035), (254, 245, 226), .57)
                ink.line(points, colour + (76 if shiny else 98,), 1.35)
        # Tall wisps curve from the sides into a bright distant horizon.
        for side in (-1, 1):
            for strand in range(22):
                phase = strand * .005
                points = []
                for n in range(131):
                    v = n / 130
                    u = .5 + side * (.44 - .28 * v + .065 * math.sin(v * 8 + strand * .045)) + side * phase
                    points.append((u, v))
                ink.line(points, (255, 248, 219, 35 if shiny else 48), .95)
        for _ in range(27):
            x, y = rng.random(), rng.random()
            _four_star(ink, x, y, rng.uniform(.002, .006), (247, 251, 255, rng.randrange(89, 179)))
    _layer(image, currents)


def _foil_scene(image, type_name, seed):
    """Gilded faceted terrain and fine reflected contours on black foil."""
    rng = random.Random(seed)

    def paint(ink):
        for depth, level in enumerate((.59, .73, .88)):
            points = [(0, 1.04)]
            for n in range(32):
                x = n / 31
                y = level - .045 * math.sin(n * .71 + depth) - rng.uniform(0, .075)
                points.append((x, y))
            points.append((1, 1.04))
            tone = ((61, 51, 30), (38, 34, 23), (23, 24, 22))[depth]
            ink.polygon(points, tone + (210,))
            ridge = points[1:-1]
            ink.line(ridge, (228, 182, 75, 130 - depth * 24), 1.5)
            for index in range(24):
                offset = index * .007
                ink.line([(u, v + offset + .004 * math.sin(u * 32 + index)) for u, v in ridge],
                         (192, 147, 53, 23 + depth * 4), .95)
        # Foil facets occupy the edges; the silhouette centre remains readable.
        for side in (0, 1):
            for index in range(5):
                x = side + (1 if side == 0 else -1) * rng.uniform(-.06, .20)
                y, size = rng.uniform(.51, .98), rng.uniform(.07, .19)
                shape = [(x, y - size), (x + size * .48, y - size * .35),
                         (x + size * .30, y + size), (x - size * .38, y + size * .25)]
                ink.polygon(shape, (101, 79, 34, 79))
                ink.line(shape + shape[:1], (225, 182, 78, 116), 1.1)
                ink.line([(x, y - size), (x + size * .10, y + size * .28), (x + size * .30, y + size)],
                         (241, 211, 131, 93), 1)
    _layer(image, paint)
    _gold_etch(image, art=True)
    _gold_granules(image, seed)
    _stars(image, seed + 87, 46, gold=True, muted=True)


def _pearl_highlights(image):
    def glow(ink):
        for x, y, radius in ((.16, .18, .072), (.85, .38, .059), (.68, .81, .039)):
            _four_star(ink, x, y, radius * 1.9, (255, 255, 242, 48))
    _layer(image, glow, max(.4, image.width / 100))

    def points(ink):
        for x, y, radius in ((.16, .18, .072), (.85, .38, .059), (.68, .81, .039)):
            _four_star(ink, x, y, radius, (255, 255, 251, 215))
            _four_star(ink, x, y, radius * .56, (255, 255, 255, 251))
            ry = radius * ink.width / ink.height
            ink.line([(x - radius * 1.18, y), (x + radius * 1.18, y)], (255, 255, 255, 107), 1)
            ink.line([(x, y - ry * 1.28), (x, y + ry * 1.28)], (255, 255, 255, 114), 1)
    _layer(image, points)


@lru_cache(maxsize=12)
def _render_cached(which, kind, type_name, width, height):
    image = _field(kind, type_name, width, height, art=which == "art")
    seed = _seed(kind, type_name, which == "art")
    if which == "art":
        if kind in {"C", "R"}:
            _landscape(image, type_name, seed, silver=kind == "R")
            if kind == "R":
                _silver_brush(image, seed + 5)
        elif kind == "SR":
            _cloud_flow(image, type_name, seed)
            _rainbow_engraving(image, art=True)
            _stars(image, seed + 51, 80, muted=True)
        elif kind == "SSR":
            _foil_scene(image, type_name, seed)
        else:
            _cloud_flow(image, type_name, seed, shiny=True)
            _pearl_contours(image, art=True)
            _stars(image, seed + 51, 45, shiny=True, muted=True)
            _pearl_highlights(image)
    elif kind == "C":
        _paper_fibres(image, seed)
    elif kind == "R":
        _type_underprint(image, type_name)
        _silver_brush(image, seed)
        _stars(image, seed + 51, 83, muted=True)
    elif kind == "SR":
        _rainbow_engraving(image)
        _stars(image, seed + 51, 112, muted=True)
    elif kind == "SSR":
        _gold_etch(image)
        _gold_granules(image, seed)
    else:
        _pearl_contours(image)
        _pearl_highlights(image)
        _stars(image, seed + 51, 58, shiny=True, muted=True)
    return image


def render_print_surface(kind: str, type_name: str, width: int, height: int) -> Image.Image:
    """Return RGBA print stock for C/R/SR/SSR/SHINY and a main-series type."""
    return _render_cached("surface", *_normalise(kind, type_name, width, height)).copy()


def render_art_backdrop(kind: str, type_name: str, width: int, height: int) -> Image.Image:
    """Return an RGBA illustrated or foil habitat with space for a portrait."""
    return _render_cached("art", *_normalise(kind, type_name, width, height)).copy()
