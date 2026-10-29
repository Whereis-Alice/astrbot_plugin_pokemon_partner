"""Original card stocks inspired by Yu-Gi-Oh's print and foil vocabulary.

Stock, picture window and reading paper are independent compositions. Materials
are generated deterministically at a small sampling resolution; engraved lines
are drawn at output resolution. Public callers receive copies of cached images.
No scanned card, network request, Pokémon portrait, text or state is used here.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache

from PIL import Image, ImageDraw

try:
    from .tcg_symbols import TYPE_COLORS
except ImportError:
    from tcg_symbols import TYPE_COLORS


KINDS = ("C", "R", "SR", "SSR", "SHINY")
READING_INKS = {
    "C": (48, 35, 24), "R": (52, 32, 26), "SR": (23, 41, 63),
    "SSR": (246, 227, 181), "SHINY": (45, 40, 66),
}
READING_MUTED = {
    "C": (77, 56, 36), "R": (84, 50, 37), "SR": (41, 59, 77),
    "SSR": (224, 200, 150), "SHINY": (71, 57, 86),
}
TITLE_INKS = {
    "C": (40, 28, 19), "R": (239, 235, 228), "SR": (235, 242, 246),
    "SSR": (255, 227, 151), "SHINY": (43, 37, 62),
}
_SEEDS = {"C": 17003, "R": 24029, "SR": 39113, "SSR": 57119, "SHINY": 79133}
_ALIASES = dict(zip(
    ("grass", "fire", "water", "electric", "psychic", "fighting", "dark",
     "steel", "dragon", "normal", "ice", "ground", "flying", "poison",
     "rock", "bug", "ghost", "fairy"),
    ("草", "火", "水", "电", "超能力", "格斗", "恶", "钢", "龙", "一般",
     "冰", "地面", "飞行", "毒", "岩石", "虫", "幽灵", "妖精"),
))


def _mix(a, b, amount):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _rgb(values):
    return tuple(max(0, min(255, round(value))) for value in values)


def _spectrum(phase):
    return tuple(round(163 + 90 * math.cos(math.tau * (phase - shift)))
                 for shift in (0, 1 / 3, 2 / 3))


def _normalise(kind, width, height, type_name="一般"):
    kind = str(kind).upper()
    if kind not in KINDS:
        kind = "C"
    width, height = int(width), int(height)
    if width < 1 or height < 1:
        raise ValueError("Yu-Gi-Oh material dimensions must be positive")
    type_name = _ALIASES.get(str(type_name).lower(), str(type_name))
    if type_name not in TYPE_COLORS:
        type_name = "一般"
    return kind, width, height, type_name


def _seed(kind, layer, type_name):
    return _SEEDS[kind] + sum((i + 7) * ord(c) for i, c in enumerate(layer + type_name))


def _print_relief(colour, kind, layer, u, v):
    """Mesoscopic print relief survives the card's usual 360px chat preview.

    This is coherent warped stock, not amplified random noise: each finish has
    its own veins, rolled-metal ridges or optical cell structure. Reading stock
    retains the same forms with a restricted luminance excursion.
    """
    reading = layer == "reading"
    cloud = .5 + .25 * math.sin(u * 17 + 2 * math.sin(v * 9))
    cloud += .25 * math.sin(v * 23 - u * 8 + math.sin(u * 11))
    if kind == "C":
        phase = u * 38 + v * 27 + 4 * math.sin(v * 12 - u * 7) + 2 * math.sin(u * 23 + v * 8)
        vein = (.5 + .5 * math.sin(phase)) ** 6
        mica = (.5 + .5 * math.sin(phase + .85)) ** 5
        colour = _mix(colour, (113, 76, 32), vein * (.095 if reading else .24))
        colour = _mix(colour, (245, 225, 172), mica * (.14 if reading else .31))
        return _mix(colour, (170, 132, 69), cloud * (.075 if reading else .13))
    if kind == "R":
        phase = u * 53 + v * 19 + 3.5 * math.sin(v * 15 + u * 11)
        ridge = (.5 + .5 * math.sin(phase)) ** 7
        glint = (.5 + .5 * math.sin(phase + .55)) ** 9
        colour = _mix(colour, (87, 43, 27), ridge * (.095 if reading else .27))
        colour = _mix(colour, (252, 211, 158), glint * (.13 if reading else .32))
        return _mix(colour, (174, 110, 71), cloud * (.05 if reading else .13))
    if kind == "SR":
        phase = u * 39 - v * 28 + 2.5 * math.sin(v * 12 + u * 9)
        crack = (.5 + .5 * math.sin(phase)) ** 14
        colour = _mix(colour, (17, 39, 74), crack * (.075 if reading else .30))
        frost = (.5 + .5 * math.sin(phase + .37)) ** 12
        colour = _mix(colour, (191, 230, 245), frost * (.15 if reading else .32))
        return _mix(colour, (106, 158, 195), cloud * (.06 if reading else .17))
    if kind == "SSR":
        phase = u * 41 + v * 31 + 4.2 * math.sin(v * 10 - u * 8)
        fracture = (.5 + .5 * math.sin(phase)) ** 16
        colour = _mix(colour, (0, 4, 10), cloud * (.22 if reading else .43))
        colour = _mix(colour, (194, 148, 59), fracture * (.095 if reading else .27))
        gold = (.5 + .5 * math.sin(phase + .50)) ** 16
        return _mix(colour, (102, 79, 39), gold * (.13 if reading else .27))
    phase = u * 21 - v * 15 + .33 * math.sin(v * 23)
    prism = _spectrum(phase / math.tau)
    cell = .5 + .5 * math.sin(u * 53) * math.sin(v * 49)
    colour = _mix(colour, prism, cell * (.07 if reading else .19))
    reflection = (.5 + .5 * math.sin(u * 45 + v * 39)) ** 10
    return _mix(colour, (255, 251, 243), reflection * (.12 if reading else .32))


def _field(kind, layer, width, height, type_name):
    # Sampling resolves 30-180px physical material forms without a full-size
    # Python pixel loop; at most 91k pixels are sampled per cached finish.
    scale = min(1.0, 252 / width, 360 / height)
    sw, sh = max(1, round(width * scale)), max(1, round(height * scale))
    rng = random.Random(_seed(kind, layer, type_name))
    tc = TYPE_COLORS[type_name]
    values = []
    for y in range(sh):
        v = y / max(1, sh - 1)
        for x in range(sw):
            u = x / max(1, sw - 1)
            noise = rng.uniform(-1, 1)
            centre = math.exp(-((u - .46) ** 2 / .14 + (v - .38) ** 2 / .22))
            edge = min(1, ((u - .5) ** 2 + (v - .5) ** 2) * 2)
            wave = .5 + .5 * math.sin(u * 9 + v * 6 + math.sin(v * 11) * .6)
            if layer == "reading":
                if kind == "C":
                    colour = _mix((224, 203, 156), (204, 177, 122), edge * .45)
                    colour = _mix(colour, (242, 226, 191), centre * .20)
                    grain = noise * 1.7 + math.sin(x * .4 + y * .2) * .4
                elif kind == "R":
                    colour = _mix((226, 201, 168), (195, 155, 113), edge * .23)
                    colour = _mix(colour, (244, 217, 189), centre * .13)
                    grain = noise * 1.3 + math.sin(y * 2.4) * .5
                elif kind == "SR":
                    colour = _mix((190, 211, 218), (167, 191, 207), edge * .32)
                    colour = _mix(colour, (217, 231, 228), wave * .12)
                    grain = noise * .9
                elif kind == "SSR":
                    colour = _mix((29, 28, 26), (45, 37, 26), wave * .36)
                    colour = _mix(colour, (61, 49, 31), centre * .13)
                    grain = noise * .8
                else:
                    colour = _mix((217, 219, 226), (224, 216, 229), wave * .35)
                    colour = _mix(colour, _spectrum(u * .73 - v * .41), .045)
                    colour = _mix(colour, (232, 240, 233), centre * .17)
                    grain = noise * .7
            elif layer == "stock":
                if kind == "C":
                    colour = _mix((189, 143, 74), (224, 190, 127), centre * .70)
                    colour = _mix(colour, (122, 79, 33), edge * .28)
                    colour = _mix(colour, (226, 197, 143), wave * .07)
                    grain = noise * 3.5
                elif kind == "R":
                    colour = _mix((130, 66, 39), (205, 134, 77), centre * .80)
                    band = math.exp(-((u - .27 + v * .12) / .10) ** 2)
                    colour = _mix(colour, (247, 193, 135), band * .36)
                    colour = _mix(colour, (76, 35, 27), edge * .31)
                    grain = noise * 2.3 + math.sin(y * 5.1) * 1.4
                elif kind == "SR":
                    colour = _mix((28, 52, 94), (74, 122, 166), centre * .72)
                    colour = _mix(colour, (105, 157, 182), wave * .12)
                    colour = _mix(colour, (18, 35, 64), edge * .28)
                    grain = noise * 1.5
                elif kind == "SSR":
                    colour = _mix((16, 18, 24), (50, 38, 23), wave * .25 + centre * .15)
                    beam = math.exp(-((u - .19 + v * .12) / .04) ** 2)
                    colour = _mix(colour, (205, 156, 60), beam * .26)
                    grain = noise * 1.8
                else:
                    colour = _mix((188, 190, 207), (227, 231, 233), centre * .82)
                    band = math.exp(-((u - .78 + v * .29) / .08) ** 2)
                    colour = _mix(colour, _spectrum(u * 2.2 - v * 1.4), .09 + band * .29)
                    colour = _mix(colour, (170, 166, 191), edge * .13)
                    grain = noise * 1.2
            else:
                if kind == "C":
                    colour = _mix((213, 186, 139), (100, 119, 92), v * .78)
                    colour = _mix(colour, tc, .05 + centre * .08)
                    colour = _mix(colour, (242, 218, 158), centre * .29)
                    grain = noise * 2.4
                elif kind == "R":
                    colour = _mix((59, 36, 35), (67, 106, 109), centre * .79)
                    colour = _mix(colour, (160, 94, 48), (1 - u) ** 3 * .46)
                    colour = _mix(colour, tc, centre * .07)
                    grain = noise * 1.7
                elif kind == "SR":
                    colour = _mix((12, 29, 67), (77, 131, 174), centre * .84)
                    colour = _mix(colour, tc, centre * .07)
                    ridge = math.exp(-((u - .78 + v * .24) / .04) ** 2)
                    colour = _mix(colour, (168, 217, 231), ridge * .32)
                    grain = noise * 1.3
                elif kind == "SSR":
                    colour = _mix((9, 14, 24), (57, 49, 36), centre * .71)
                    colour = _mix(colour, tc, centre * .045)
                    beam = math.exp(-((u - .76 + v * .35) / .045) ** 2)
                    colour = _mix(colour, (196, 153, 71), beam * .26)
                    grain = noise * 1.7
                else:
                    colour = _mix((175, 186, 208), (221, 232, 232), centre * .60)
                    phase = u * 1.25 - v * .83 + .10 * math.sin(v * 8)
                    band = math.exp(-((u - .85 + v * .37) / .14) ** 2)
                    colour = _mix(colour, _spectrum(phase), .10 + band * .26)
                    colour = _mix(colour, (252, 243, 222), centre * .18)
                    grain = noise * .8
            colour = _print_relief(colour, kind, layer, u, v)
            values.append((*_rgb(c + grain for c in colour), 255))
    im = Image.new("RGBA", (sw, sh))
    im.putdata(values)
    return im.resize((width, height), Image.Resampling.BICUBIC)


def _poly(draw, points, width, height, fill):
    draw.polygon([(round(x * width), round(y * height)) for x, y in points], fill=fill)


def _spark(draw, x, y, radius, fill, *, diamond=False):
    if diamond:
        draw.polygon(((x, y - radius), (x + radius * .56, y),
                      (x, y + radius), (x - radius * .56, y)), fill=fill)
    else:
        draw.line((x - radius, y, x + radius, y), fill=fill, width=1)
        draw.line((x, y - radius, x, y + radius), fill=fill, width=1)
        draw.ellipse((x - 1, y - 1, x + 1, y + 1), fill=fill)


def _curve(a, b, c, d, steps=24):
    points = []
    for i in range(steps + 1):
        t = i / steps
        q = 1 - t
        points.append((q ** 3 * a[0] + 3 * q * q * t * b[0] + 3 * q * t * t * c[0] + t ** 3 * d[0],
                       q ** 3 * a[1] + 3 * q * q * t * b[1] + 3 * q * t * t * c[1] + t ** 3 * d[1]))
    return points


def _relief_detail(base, kind, layer, type_name):
    """A second engraving scale: paper swirls, copper scrolls and foil facets.

    Geometry is tens of physical pixels wide. It remains visible after JPEG
    encoding and chat downscaling; fine grain from the original engraving pass
    sits above this relief. The reading pass uses the same geometry with softer
    ink and compressed contrast rather than replacing it with a blank panel.
    """
    width, height = base.size
    s = width / 1080
    # Keep sensible drawing geometry for the supported tiny-size edge case.
    if width < 12 or height < 12:
        return base
    rng = random.Random(_seed(kind, layer, type_name) + 1423)
    reading = layer == "reading"
    overlay = Image.new("RGBA", base.size)
    d = ImageDraw.Draw(overlay)
    if kind == "C":
        # Widely spaced knots and distorted mica contours emulate the swirled
        # paper of a classic Normal Monster instead of a flat yellow gradient.
        pitch = max(30, round(178 * s))
        for yy in range(-pitch, height + pitch, pitch):
            for xx in range(-pitch, width + pitch, pitch):
                cx = xx + rng.uniform(.12, .82) * pitch
                cy = yy + rng.uniform(.12, .92) * pitch
                phase = rng.uniform(0, math.tau)
                skew = rng.uniform(.65, 1.55)
                for ring in range(2):
                    r = pitch * (.22 + ring * .13)
                    points = []
                    for step in range(65):
                        a = step * math.tau / 64
                        rr = r * (1 + .12 * math.sin(a * 3 + phase) + .07 * math.sin(a * 5 - phase))
                        points.append((cx + math.cos(a) * rr * skew,
                                       cy + math.sin(a) * rr / skew))
                    shade = (99, 64, 27, 18 if reading else 38)
                    light = (252, 225, 158, 27 if reading else 66)
                    d.line(points, fill=shade, width=max(1, round((2 if reading else 3) * s)), joint="curve")
                    d.line([(x + 2 * s, y - 2 * s) for x, y in points], fill=light,
                           width=max(1, round((2 if reading else 3) * s)), joint="curve")
        # Long fibres look pressed into the mottled stock at full resolution.
        for _ in range(round(width * height / 3600)):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            span = rng.uniform(18, 64) * s
            bend = rng.uniform(-3, 3) * s
            d.line(_curve((x, y), (x + span * .3, y + bend),
                          (x + span * .7, y - bend), (x + span, y)),
                   fill=(103, 70, 32, 23 if reading else 42), width=max(1, round(s)))
    elif kind == "R":
        # Embossed acanthus-like scrollwork is sunk into rolled copper. This
        # introduces real repeat geometry and directional raised highlights.
        pitch = max(42, round(226 * s))
        for row, yy in enumerate(range(-pitch // 2, height + pitch, pitch)):
            for xx in range(-pitch, width + pitch, pitch):
                # Real engraved stock has broken repeats and uneven polish;
                # jitter, size and direction keep the plate from wallpapering.
                if rng.random() < .19:
                    continue
                cx = xx + pitch * ((.65 if row % 2 else .15) + rng.uniform(-.15, .15))
                cy = yy + pitch * rng.uniform(.37, .64)
                flip = rng.choice((-1, 1))
                turn = rng.uniform(-.30, .65)
                sweep = rng.uniform(.84, 1.18)
                radius = rng.uniform(.27, .37)
                points = []
                for i in range(61):
                    a = turn + i * math.tau * sweep / 60
                    r = pitch * (radius - (radius - .07) * i / 60)
                    points.append((cx + flip * math.cos(a) * r, cy + math.sin(a) * r))
                stroke = max(1, round((3 if reading else 5) * s))
                d.line([(x + 2 * s, y + 2 * s) for x, y in points],
                       fill=(54, 29, 18, 28 if reading else 101), width=stroke, joint="curve")
                d.line(points, fill=(251, 199, 130, 51 if reading else 136), width=stroke, joint="curve")
                for sign in (-1, 1):
                    stem = _curve((cx - flip * pitch * .08, cy + sign * pitch * .15),
                                  (cx - flip * pitch * .23, cy + sign * pitch * .38),
                                  (cx + flip * pitch * .20, cy + sign * pitch * .47),
                                  (cx + flip * pitch * .36, cy + sign * pitch * .31))
                    d.line(stem, fill=(250, 194, 118, 37 if reading else 106),
                           width=max(1, round((2 if reading else 4) * s)), joint="curve")
                    for offset in (.33, .60):
                        p = stem[round(offset * 24)]
                        leaf = _curve(p, (p[0] + pitch * .02, p[1] - sign * pitch * .11),
                                      (p[0] + pitch * .15, p[1] - sign * pitch * .13),
                                      (p[0] + pitch * .14, p[1] - sign * pitch * .02))
                        leaf += _curve(leaf[-1], (p[0] + pitch * .08, p[1] + sign * pitch * .05),
                                       (p[0] + pitch * .02, p[1] + sign * pitch * .03), p)
                        d.polygon(leaf, fill=(230, 155, 83, 21 if reading else 45))
                        d.line(leaf, fill=(249, 194, 125, 38 if reading else 95),
                               width=max(1, round((2 if reading else 3) * s)), joint="curve")
        # Dense paired lines provide satin grain underneath the larger relief.
        for y in range(0, height, max(3, round(9 * s))):
            d.line((0, y, width, y), fill=(61, 31, 20, 10 if reading else 23), width=max(1, round(s)))
            d.line((0, y + 2 * s, width, y + 2 * s), fill=(255, 221, 171, 13 if reading else 30), width=max(1, round(s)))
    elif kind in {"SR", "SSR"}:
        # An irregular triangular tessellation gives cut crystal / polished
        # obsidian actual faces; it is deliberately separate from orbit decor.
        pitch = max(35, round((155 if kind == "SR" else 190) * s))
        columns = math.ceil(width / pitch) + 3
        rows = math.ceil(height / pitch) + 3
        vertices = {}
        for row in range(rows):
            for col in range(columns):
                vertices[row, col] = ((col - 1 + rng.uniform(-.21, .21)) * pitch,
                                      (row - 1 + rng.uniform(-.22, .22)) * pitch)
        for row in range(rows - 1):
            for col in range(columns - 1):
                a, b, c, e = (vertices[row, col], vertices[row, col + 1],
                              vertices[row + 1, col], vertices[row + 1, col + 1])
                triangles = ((a, b, e), (a, e, c)) if (row + col) % 2 else ((a, b, c), (b, e, c))
                for triangle in triangles:
                    if kind == "SR":
                        bright = rng.random() > .5
                        tint = (195, 230, 245) if bright else (11, 35, 82)
                        alpha = rng.randint(7, 17) if reading else rng.randint(17, 45)
                        d.polygon(triangle, fill=(*tint, alpha))
                        d.line((*triangle, triangle[0]), fill=(13, 37, 79, 20 if reading else 84),
                               width=max(1, round((2 if reading else 4) * s)), joint="curve")
                        d.line([(x - s, y - s) for x, y in (*triangle, triangle[0])],
                               fill=(215, 247, 252, 40 if reading else 111),
                               width=max(1, round((2 if reading else 3) * s)), joint="curve")
                    else:
                        tint = (0, 2, 10) if rng.random() > .43 else (95, 76, 41)
                        d.polygon(triangle, fill=(*tint, rng.randint(16, 30) if reading else rng.randint(20, 65)))
                        d.line((*triangle, triangle[0]), fill=(229, 187, 94, 20 if reading else 86),
                               width=max(1, round((2 if reading else 3) * s)), joint="curve")
                        d.line([(x + 2 * s, y + 2 * s) for x, y in (*triangle, triangle[0])],
                               fill=(0, 0, 6, 69 if reading else 146),
                               width=max(1, round((2 if reading else 4) * s)), joint="curve")
                    # Short engraved hatch fans follow one crystal/stone face.
                    if rng.random() < .33:
                        first, second, third = triangle
                        for n in range(4):
                            t = .18 + n * .09
                            start = (first[0] * (1 - t) + second[0] * t,
                                     first[1] * (1 - t) + second[1] * t)
                            end = (first[0] * (1 - t) + third[0] * t,
                                   first[1] * (1 - t) + third[1] * t)
                            colour = (208, 243, 249) if kind == "SR" else (204, 165, 82)
                            d.line((start, end), fill=(*colour, 18 if reading else 58), width=max(1, round(s)))
        if kind == "SSR":
            # Torn metallic leaf islands are 25-100px wide, not isolated dots.
            for _ in range(round(width * height / 33000)):
                x, y = rng.uniform(0, width), rng.uniform(0, height)
                radius = rng.uniform(15, 47) * s
                points = []
                for i in range(13):
                    angle = i * math.tau / 12
                    r = radius * rng.uniform(.38, 1)
                    points.append((x + math.cos(angle) * r, y + math.sin(angle) * r * .48))
                d.polygon(points, fill=(204, 159, 66, 12 if reading else 54))
                d.line(points, fill=(251, 213, 126, 17 if reading else 97), width=max(1, round(2 * s)))
    else:
        # Crisp crossed prismatic cells, mirrored facets and many small diamond
        # reflections reproduce foil rather than a smooth rainbow background.
        pitch = max(12, round(51 * s))
        for row, y in enumerate(range(-pitch, height + pitch, pitch)):
            for col, x in enumerate(range(-pitch, width + pitch, pitch)):
                phase = col * .11 + row * .16
                tint = _spectrum(phase)
                alpha = 18 if reading else 41
                d.polygon(((x, y), (x + pitch, y), (x, y + pitch)), fill=(*tint, alpha))
                d.polygon(((x + pitch, y), (x + pitch, y + pitch), (x, y + pitch)),
                          fill=(252, 250, 245, 24 if reading else 74))
                dark_alpha = 17 if reading else 55
                d.line((x, y, x + pitch, y + pitch), fill=(106, 91, 157, dark_alpha),
                       width=max(1, round((1.5 if reading else 2.5) * s)))
                d.line((x + pitch, y, x, y + pitch), fill=(247, 251, 255, 35 if reading else 116),
                       width=max(1, round((2 if reading else 3) * s)))
                if (row + col * 3) % 4 == 0:
                    cx, cy = x + pitch / 2, y + pitch / 2
                    r = pitch * .22
                    diamond = ((cx, cy - r), (cx + r * .67, cy), (cx, cy + r), (cx - r * .67, cy))
                    d.polygon(diamond, fill=(248, 250, 255, 33 if reading else 128))
                    d.line((diamond[0], diamond[2]), fill=(*tint, 34 if reading else 96),
                           width=max(1, round((1.5 if reading else 2) * s)))
        for x in range(0, width, pitch):
            d.line((x, 0, x, height), fill=(80, 84, 130, 24 if reading else 75),
                   width=max(1, round((1.5 if reading else 2) * s)))
            d.line((x + 2 * s, 0, x + 2 * s, height), fill=(253, 254, 250, 55 if reading else 162),
                   width=max(1, round(2 * s)))
        for y in range(0, height, pitch):
            d.line((0, y, width, y), fill=(97, 81, 142, 24 if reading else 70),
                   width=max(1, round((1.5 if reading else 2) * s)))
            d.line((0, y + 2 * s, width, y + 2 * s), fill=(247, 252, 255, 55 if reading else 161),
                   width=max(1, round(2 * s)))
    return Image.alpha_composite(base, overlay)


def _engraving(base, kind, layer, type_name):
    width, height = base.size
    s = width / 900
    rng = random.Random(_seed(kind, layer, type_name) + 919)
    overlay = Image.new("RGBA", base.size)
    d = ImageDraw.Draw(overlay)
    reading = layer == "reading"
    if kind == "C":
        # Irregular short fibres never resemble a uniform dot/noise filter.
        for _ in range(190 if reading else 450):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            length = rng.uniform(3, 21) * max(.5, s)
            d.line((x, y, x + length, y + rng.uniform(-1.5, 1.5) * s),
                   fill=(101, 69, 27, 12 if reading else 18), width=1)
        if layer == "art":
            # Layered old-illustration hills leave the centre clear for artwork.
            for i, colour in enumerate(((91, 107, 78, 74), (66, 88, 68, 87), (53, 71, 57, 106))):
                points = [(0, 1), (0, .73 + i * .08)]
                points += [(j / 30, .77 + i * .07 + .055 * math.sin(j / 30 * 8 + i)) for j in range(31)]
                points.append((1, 1))
                _poly(d, points, width, height, colour)
            for _ in range(23):
                x = rng.choice((rng.uniform(.02, .19), rng.uniform(.81, .98))) * width
                y = rng.uniform(.72, .98) * height
                h = rng.uniform(10, 38) * s
                d.line((x, y, x - h * .25, y - h), fill=(202, 193, 130, 82), width=max(1, round(s)))
    elif kind == "R":
        # Straight rolled-metal strands, with a warm satin highlight.
        for y in range(0, height, max(3, round(7 * s))):
            alpha = rng.randint(5, 11) if reading else rng.randint(9, 25)
            d.line((0, y, width, y + rng.uniform(-1, 1) * s), fill=(252, 213, 171, alpha), width=1)
        if layer == "art":
            for i in range(5):
                inset = (55 + i * 21) * s
                # Partial cold-silver circles retain an unfoiled dark picture area.
                d.arc((inset, height * .12 + i * 12 * s, width - inset, height * .92 - i * 12 * s),
                      211, 339, fill=(190, 207, 205, 37 - i * 4), width=max(1, round(s)))
            for _ in range(42):
                x, y = rng.uniform(0, width), rng.uniform(0, height)
                if .22 * width < x < .78 * width:
                    continue
                d.ellipse((x, y, x + 1.5 * s, y + 1.5 * s), fill=(214, 155, 90, 80))
    elif kind == "SR":
        # Frosted ceremonial circles are structural, not floating spell text.
        cx, cy = width * .50, height * (.49 if layer == "art" else .34)
        for radius in ((.20, .25, .38, .41) if layer == "art" else (.55, .61)):
            r = width * radius
            d.ellipse((cx - r, cy - r, cx + r, cy + r),
                      outline=(169, 219, 232, 8 if reading else 37), width=max(1, round(s)))
        if not reading:
            for i in range(48):
                a = i * math.tau / 48
                r1, r2 = width * .392, width * (.405 if i % 4 else .425)
                d.line((cx + math.cos(a) * r1, cy + math.sin(a) * r1,
                        cx + math.cos(a) * r2, cy + math.sin(a) * r2),
                       fill=(180, 220, 233, 66), width=max(1, round(s)))
            for side in (0, 1):
                _poly(d, ((side, .05), (.14 if side == 0 else .86, .28),
                          (side, .83)), width, height, (156, 203, 235, 14))
                _poly(d, ((side, .90), (.10 if side == 0 else .90, .54),
                          (side, .23)), width, height, (215, 234, 241, 12))
    elif kind == "SSR":
        # Celestial engraving and random gold flecks occupy the open margins.
        for _ in range(44 if reading else 240):
            x, y = rng.uniform(0, width), rng.uniform(0, height)
            r = rng.uniform(.3, 1.3) * max(.6, s)
            d.ellipse((x, y, x + r, y + r), fill=(227, 184, 95, 9 if reading else rng.randint(30, 97)))
        if not reading:
            cx, cy = width * .48, height * .45
            for r in (width * .34, width * .43, width * .46):
                d.ellipse((cx - r, cy - r * .70, cx + r, cy + r * .70),
                          outline=(203, 153, 56, 43), width=max(1, round(s)))
            for i in range(18):
                a = i * math.tau / 18 - .12
                r = width * .435
                x, y = cx + math.cos(a) * r, cy + math.sin(a) * r * .70
                _spark(d, x, y, (4 if i % 3 else 7) * s, (233, 192, 98, 142), diamond=i % 3 == 0)
            points = [(width * x, height * y) for x, y in ((.08, .19), (.19, .11), (.31, .18), (.17, .28))]
            d.line(points, fill=(220, 174, 79, 66), width=max(1, round(s)))
            for x, y in points:
                _spark(d, x, y, 4 * s, (248, 219, 151, 174))
    else:
        # Orthogonal prismatic micro-grid differs from diagonal rainbow washes.
        pitch = max(8, round((21 if layer == "art" else 17) * s))
        for x in range(0, width, pitch):
            alpha = 5 if reading else (23 if layer == "art" else 16)
            d.line((x, 0, x, height), fill=(252, 251, 250, alpha), width=1)
            d.line((x + 2, 0, x + 2, height), fill=(77, 81, 131, alpha // 2), width=1)
        for y in range(0, height, pitch):
            alpha = 5 if reading else (22 if layer == "art" else 15)
            d.line((0, y, width, y), fill=(246, 252, 253, alpha), width=1)
            d.line((0, y + 2, width, y + 2), fill=(126, 89, 151, alpha // 2), width=1)
        if not reading:
            for _ in range(48 if layer == "art" else 76):
                x, y = rng.uniform(0, width), rng.uniform(0, height)
                # Sparkles accumulate near edges; the portrait silhouette remains clear.
                if layer == "art" and .27 * width < x < .73 * width and .20 * height < y < .83 * height:
                    continue
                _spark(d, x, y, rng.uniform(2, 6) * s, (252, 251, 255, rng.randint(95, 188)), diamond=True)
            if layer == "art":
                _poly(d, ((.00, .42), (.22, .08), (.53, .00), (.26, .40)), width, height, (255, 251, 249, 26))
                _poly(d, ((1, .29), (.79, .76), (.45, 1), (.90, .93)), width, height, (253, 231, 255, 25))
                _poly(d, ((0, .83), (.18, .53), (.37, .94), (.16, 1)), width, height, (202, 243, 247, 24))
    return Image.alpha_composite(base, overlay)


@lru_cache(maxsize=15)
def _material(kind, layer, width, height, type_name):
    stock = _field(kind, layer, width, height, type_name)
    stock = _relief_detail(stock, kind, layer, type_name)
    return _engraving(stock, kind, layer, type_name)


def render_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return the card's tinted paper/metal stock as an opaque RGBA copy."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "stock", width, height, type_name).copy()


def render_art_scene(kind: str, type_name: str, width: int, height: int) -> Image.Image:
    """Return a separate picture-window scene with its own rarity composition."""
    kind, width, height, type_name = _normalise(kind, width, height, type_name)
    return _material(kind, "art", width, height, type_name).copy()


def render_reading_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return low-amplitude, stock-matched reading paper; no pure-white panel."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "reading", width, height, type_name).copy()
