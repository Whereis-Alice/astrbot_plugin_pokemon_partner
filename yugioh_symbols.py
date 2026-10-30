"""Yu-Gi-Oh-style attribute jewels and the user's reference level star.

Each of the eighteen Pokémon types has a separate single-character jewel. Its
glyph is part of the icon; callers should not repeat the type name beside it.
The level orb is isolated from the user-provided C.png (1359, 289, 1461, 391).
All resources are local, and public functions return independent RGBA copies.
"""

from __future__ import annotations

import math
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


def _draw_jewel(type_name: str) -> Image.Image:
    """Asset-preparation source; live rendering reads the bundled PNGs below."""
    glyph, tint, reflected = ATTRIBUTES.get(type_name, ("?", (60, 59, 69), (180, 177, 190)))
    size, centre, radius = 256, 127.5, 123.0
    data = []
    for y in range(size):
        v = (y - centre) / radius
        for x in range(size):
            u = (x - centre) / radius
            r = math.hypot(u, v)
            if r >= 1:
                data.append((0, 0, 0, 0))
                continue
            # Lower-left reflection, deep black middle, bright top-right glint:
            # the asymmetric lens follows the supplied DARK attribute jewel.
            lower = math.exp(-((u + .14) ** 2 / .85 + (v - .57) ** 2 / .19))
            upper = math.exp(-((u - .08) ** 2 / .45 + (v + .71) ** 2 / .11))
            hollow = math.exp(-((u - .12) ** 2 / .24 + (v + .02) ** 2 / .18))
            edge_light = max(0, u * -.52 + v * .55) * r ** 3
            color = _mix(tint, reflected, min(.92, lower * .82 + upper * .59 + edge_light * .40))
            color = _mix(color, (9, 5, 14), hollow * .90)
            rim = max(0.0, (r - .947) / .053)
            color = _mix(color, _mix((23, 17, 30), reflected, (1 - u + v) / 3), rim * .92)
            specular = math.exp(-((u + .27) ** 2 / .052 + (v + .72) ** 2 / .008))
            color = _mix(color, (255, 250, 226), specular * .86)
            data.append((*color, 255))
    result = Image.new("RGBA", (size, size))
    result.putdata(data)
    # The enlarged single glyph remains readable even in 30px matchup icons.
    face = load_card_font(177 if glyph != "?" else 184, "body")
    mask = Image.new("L", result.size)
    draw = ImageDraw.Draw(mask)
    bounds = draw.textbbox((0, 0), glyph, font=face, stroke_width=1)
    x = (size - bounds[2] + bounds[0]) / 2 - bounds[0]
    y = (size - bounds[3] + bounds[1]) / 2 - bounds[1] + 1
    draw.text((x, y), glyph, font=face, fill=255, stroke_width=1)
    shade = Image.new("RGBA", result.size, (17, 7, 21, 0))
    shade.putalpha(mask.filter(ImageFilter.MaxFilter(7)).filter(ImageFilter.GaussianBlur(1.2)))
    result.alpha_composite(shade, (1, 2))
    ink = Image.new("RGBA", result.size, (255, 252, 238, 255))
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
