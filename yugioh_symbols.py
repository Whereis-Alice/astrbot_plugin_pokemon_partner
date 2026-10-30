"""Yu-Gi-Oh-style attribute jewels and the user's reference level star.

Each of the eighteen Pokémon types has a separate single-character jewel. Its
glyph is part of the icon; callers should not repeat the type name beside it.
The level orb is isolated from the user-provided C.png (1359, 289, 1461, 391).
All resources are local, and public functions return independent RGBA copies.
"""

from __future__ import annotations

import math
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

try:
    from .yugioh_fonts import load_card_font
except ImportError:
    from yugioh_fonts import load_card_font


# Core tint, reflected edge and white engraved glyph. Distinct Pokémon types
# deliberately remain separate rather than being mapped to seven TCG attributes.
ATTRIBUTES = {
    "草": ("草", (12, 98, 26), (139, 207, 79)),
    "火": ("火", (177, 28, 9), (255, 143, 35)),
    "水": ("水", (11, 60, 147), (101, 181, 242)),
    "电": ("电", (141, 95, 5), (255, 209, 52)),
    "超能力": ("超", (122, 21, 98), (244, 131, 200)),
    "格斗": ("斗", (130, 45, 16), (216, 125, 59)),
    "恶": ("恶", (37, 12, 40), (154, 83, 163)),
    "钢": ("钢", (45, 60, 65), (181, 205, 207)),
    "龙": ("龙", (53, 23, 122), (152, 126, 218)),
    "一般": ("普", (79, 66, 53), (215, 199, 169)),
    "冰": ("冰", (4, 99, 126), (145, 233, 244)),
    "地面": ("地", (113, 65, 20), (199, 151, 67)),
    "飞行": ("飞", (64, 74, 142), (170, 190, 243)),
    "毒": ("毒", (91, 21, 112), (200, 112, 224)),
    "岩石": ("岩", (91, 87, 36), (185, 180, 108)),
    "虫": ("虫", (66, 99, 9), (185, 212, 60)),
    "幽灵": ("灵", (48, 29, 91), (155, 127, 183)),
    "妖精": ("妖", (145, 44, 93), (250, 162, 198)),
}

_ALIASES = dict(zip(
    ("grass", "fire", "water", "electric", "psychic", "fighting", "dark",
     "steel", "dragon", "normal", "ice", "ground", "flying", "poison",
     "rock", "bug", "ghost", "fairy"), ATTRIBUTES,
))
_ALIASES.update({"lightning": "电", "darkness": "恶", "metal": "钢", "colorless": "一般"})
_ICON_DIR = Path(__file__).resolve().parent / "assets" / "yugioh" / "icons"
_STAR_PATH = _ICON_DIR / "level-star.png"
_FILES = dict(zip((*ATTRIBUTES, "未知"),
    ("grass", "fire", "water", "electric", "psychic", "fighting", "dark",
     "steel", "dragon", "normal", "ice", "ground", "flying", "poison",
     "rock", "bug", "ghost", "fairy", "unknown")))


def _size(value: int) -> int:
    value = int(value)
    if not 1 <= value <= 1024:
        raise ValueError("属性和星级图标尺寸应介于 1 和 1024 之间")
    return value


def _mix(a, b, amount):
    amount = max(0.0, min(1.0, amount))
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))


def _draw_jewel(type_name: str, *, face=None) -> Image.Image:
    """Asset-preparation source; live rendering reads the bundled PNGs below."""
    glyph, tint, reflected = ATTRIBUTES.get(type_name, ("?", (60, 59, 69), (180, 177, 190)))
    size, centre, radius = 256, 127.5, 124.0
    rng = random.Random(901 + sum(ord(char) for char in type_name))
    data = []
    for y in range(size):
        v = (y - centre) / radius
        for x in range(size):
            u = (x - centre) / radius
            r = math.hypot(u, v)
            if r >= 1:
                data.append((0, 0, 0, 0))
                continue
            # The reference is printed enamel: a dark ink well, broad coloured
            # reflections, and a thin struck rim rather than a glass hotspot.
            lower = math.exp(-((u + .30) ** 2 / .68 + (v - .60) ** 2 / .22))
            upper = math.exp(-((u + .43) ** 2 / .40 + (v + .63) ** 2 / .18))
            hollow = math.exp(-((u - .21) ** 2 / .61 + (v + .04) ** 2 / .39))
            cloud = .5 + .25 * math.sin(u * 17 + 1.3 * math.sin(v * 11))
            cloud += .25 * math.sin(v * 21 - u * 9)
            color = _mix(tint, reflected, .16 + .49 * lower + .23 * upper)
            color = _mix(color, (5, 4, 8), .84 * hollow)
            color = _mix(color, reflected, cloud * .055)
            if r > .947:
                # Only the lower-left cut edge catches light. A complete pale
                # ring would turn the reference's jewel into a metal badge.
                direction = max(0.0, min(1.0, -.52 * u + .48 * v))
                edge = _mix(_mix(tint, (16, 10, 19), .54),
                            _mix(reflected, (245, 237, 216), .35), direction)
                highlight = math.exp(-((r - .971) / .013) ** 2)
                color = _mix(color, edge, highlight * (.3 + .7 * direction))
                color = _mix(color, (12, 8, 17), max(0.0, (r - .986) / .014) * .64)
            # Fine, quiet pigment variation remains visible at source size.
            grain = rng.choice((-2, -1, 0, 0, 0, 1, 2))
            color = tuple(max(0, min(255, c + grain)) for c in color)
            alpha = round(255 * min(1, (1 - r) * radius))
            data.append((*color, alpha))
    result = Image.new("RGBA", (size, size))
    result.putdata(data)
    # The inscription face has the reference's straight cuts and sharp endings.
    # This generator is offline; the live path below only reads cached images.
    face = face or load_card_font(210, "symbol")
    mask = Image.new("L", result.size)
    draw = ImageDraw.Draw(mask)
    draw.text((8, 8), glyph, font=face, anchor="lt", fill=255, stroke_width=1)
    mask = mask.crop(mask.getbbox())
    scale = min(185 / mask.width, 191 / mask.height)
    mask = mask.resize((round(mask.width * scale), round(mask.height * scale)),
                       Image.Resampling.LANCZOS)
    centred = Image.new("L", result.size)
    centred.paste(mask, ((size - mask.width) // 2, (size - mask.height) // 2 + 2))
    mask = centred
    shade = Image.new("RGBA", result.size, (17, 7, 21, 0))
    shade.putalpha(mask.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(.65)))
    result.alpha_composite(shade, (1, 2))
    ink = Image.new("RGBA", (1, size))
    ink.putdata([(*_mix((255, 254, 239), (237, 233, 209), y / (size - 1)), 255)
                 for y in range(size)])
    ink = ink.resize(result.size)
    ink.putalpha(mask)
    result.alpha_composite(ink)
    return result


@lru_cache(maxsize=20)
def _jewel(type_name: str) -> Image.Image:
    """Load prepared artwork so a first draw never builds 18 pixel fields."""
    with Image.open(_ICON_DIR / f"attribute-{_FILES[type_name]}.png") as source:
        return source.convert("RGBA")


@lru_cache(maxsize=128)
def _attribute(type_name: str, size: int) -> Image.Image:
    return _jewel(type_name).resize((size, size), Image.Resampling.LANCZOS)


def render_attribute_icon(type_name: str, size: int = 80) -> Image.Image:
    """Return the specified Pokémon type's standalone attribute jewel."""
    type_name = str(type_name).strip()
    type_name = _ALIASES.get(type_name.lower(), type_name)
    if type_name not in ATTRIBUTES:
        type_name = "未知"
    return _attribute(type_name, _size(size)).copy()


@lru_cache(maxsize=12)
def _level_star(size: int) -> Image.Image:
    with Image.open(_STAR_PATH) as source:
        return source.convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)


def render_level_star(size: int = 46) -> Image.Image:
    """Return the reference's orange-red orb and bright yellow five-point star."""
    return _level_star(_size(size)).copy()
