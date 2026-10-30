"""Clouded print stocks reconstructed from the supplied monster-card references.

The local swatches contain only bare stock. Their soft, irregular clouds replace
the engraved contours, facets and grids used by the previous design. Composition
and typography belong to ``yugioh_cards``; this module supplies plain materials.
All public results are independent RGBA copies of a bounded cache.
"""
from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
import random

from PIL import Image, ImageDraw, ImageOps

try:
    from .tcg_symbols import TYPE_COLORS
except ImportError:
    from tcg_symbols import TYPE_COLORS


KINDS = ("C", "R", "SR", "SSR", "SHINY")
READING_INKS = {kind: (32, 26, 29) for kind in KINDS}
READING_MUTED = {kind: (57, 48, 55) for kind in KINDS}
TITLE_INKS = {
    "C": (40, 24, 17), "R": (252, 244, 249), "SR": (30, 18, 42),
    "SSR": (233, 191, 83), "SHINY": (51, 57, 87),
}
_PAPER = {
    "C": ((224, 201, 179), (247, 234, 215)),
    "R": ((223, 196, 216), (246, 231, 240)),
    "SR": ((215, 202, 229), (241, 231, 245)),
    "SSR": ((227, 225, 218), (251, 246, 232)),
    "SHINY": ((223, 226, 232), (252, 250, 246)),
}
_ASSETS = Path(__file__).parent / "assets" / "yugioh"
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


@lru_cache(maxsize=5)
def _stock_source(kind):
    with Image.open(_ASSETS / f"{kind.lower()}-stock.jpg") as opened:
        return opened.convert("RGB")


def _clouds(kind, width, height):
    source = _stock_source(kind)
    # Preserve the physical cloud scale in a shorter reading area as well.
    scale = width / source.width
    target_height = max(height, round(source.height * scale))
    source = source.resize((width, target_height), Image.Resampling.LANCZOS)
    offset = (target_height - height) // 2
    return source.crop((0, offset, width, offset + height))


def _spectrum(phase):
    return tuple(round(193 + 52 * math.cos(math.tau * (phase - shift)))
                 for shift in (0, 1 / 3, 2 / 3))


def _pearl_wash(base, amount):
    """A low-opacity optical tint preserves the reference's white silver base."""
    width, height = base.size
    sw = min(width, 180)
    sh = min(height, max(1, round(sw * height / width)))
    glaze = Image.new("RGB", (sw, sh))
    values = []
    for y in range(sh):
        v = y / max(1, sh - 1)
        for x in range(sw):
            u = x / max(1, sw - 1)
            values.append(_spectrum(u * .83 - v * .37))
    glaze.putdata(values)
    glaze = glaze.resize((width, height), Image.Resampling.BICUBIC)
    return Image.blend(base.convert("RGB"), glaze, amount)


def _artwork_field(kind, type_name, width, height):
    """Soft picture-window depth, with the portrait centre kept clear."""
    source_kind = "C" if kind == "SSR" else kind
    source = ImageOps.autocontrast(ImageOps.grayscale(_clouds(source_kind, width, height)), cutoff=1)
    source = Image.blend(source, Image.new("L", source.size, 128), .43)
    tc = TYPE_COLORS[type_name]
    darks = {
        "C": _mix((74, 91, 74), tc, .10),
        "R": _mix((48, 43, 87), tc, .14),
        "SR": _mix((42, 50, 83), tc, .18),
        "SSR": _mix((14, 29, 38), tc, .09),
        "SHINY": _mix((63, 75, 113), tc, .12),
    }
    lights = {
        "C": _mix((238, 225, 185), tc, .13),
        "R": _mix((207, 205, 230), tc, .18),
        "SR": _mix((205, 218, 235), tc, .15),
        "SSR": _mix((198, 191, 133), tc, .08),
        "SHINY": _mix((243, 233, 246), tc, .10),
    }
    dark, light = darks[kind], lights[kind]
    field = ImageOps.colorize(source, _mix(dark, light, .14), _mix(dark, light, .55))
    sw = min(width, 192)
    sh = min(height, max(1, round(sw * height / width)))
    glow = Image.new("L", (sw, sh))
    values = []
    for y in range(sh):
        v = y / max(1, sh - 1)
        for x in range(sw):
            u = x / max(1, sw - 1)
            halo = math.exp(-(((u - .46) / .48) ** 2 + ((v - .43) / .58) ** 2) * 1.8)
            mist = math.exp(-((v - .86 + .035 * math.sin(u * 8)) / .17) ** 2)
            values.append(round(halo * 145 + mist * 19))
    glow.putdata(values)
    glow = glow.resize((width, height), Image.Resampling.BICUBIC)
    field = Image.composite(Image.new("RGB", field.size, light), field, glow)
    if kind == "SHINY":
        field = _pearl_wash(field, .12)
    field = field.convert("RGBA")
    # Fine, irregular foil glints only occupy the open portrait margins.
    if kind in {"SR", "SSR", "SHINY"}:
        rng = random.Random(_SEEDS[kind] + sum(map(ord, type_name)))
        overlay = Image.new("RGBA", field.size)
        draw = ImageDraw.Draw(overlay)
        scale = width / 900
        for index in range(95 if kind == "SHINY" else 35):
            x, y = rng.random() * width, rng.random() * height
            if .24 * width < x < .76 * width and .13 * height < y < .88 * height:
                continue
            color = _spectrum(rng.random()) if kind == "SHINY" else (241, 222, 167)
            radius = max(.4, rng.uniform(.4, 1.5) * scale)
            draw.ellipse((x-radius, y-radius, x+radius, y+radius),
                         fill=(*color, rng.randint(75, 165)))
            if index % 11 == 0:
                arm = max(1, 3 * scale)
                draw.line((x-arm, y, x+arm, y), fill=(*color, 118), width=1)
                draw.line((x, y-arm, x, y+arm), fill=(*color, 118), width=1)
        field.alpha_composite(overlay)
    return field


@lru_cache(maxsize=15)
def _material(kind, layer, width, height, type_name):
    if layer == "art":
        return _artwork_field(kind, type_name, width, height)
    base = _clouds(kind, width, height)
    if layer == "reading":
        if kind == "SSR":
            base = _clouds("C", width, height)
        gray = ImageOps.autocontrast(ImageOps.grayscale(base), cutoff=2)
        gray = Image.blend(gray, Image.new("L", gray.size, 128), .48)
        # Keep effect boxes light, with compressed cloud contrast for black type.
        base = ImageOps.colorize(gray, *_PAPER[kind])
        if kind == "SHINY":
            base = _pearl_wash(base, .035)
    elif kind == "SSR":
        base = Image.blend(base, Image.new("RGB", base.size, (7, 11, 13)), .27)
    elif kind == "SHINY":
        base = _pearl_wash(base, .135)
    return base.convert("RGBA")


def render_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return the reference's clouded card stock as an opaque RGBA copy."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "stock", width, height, type_name).copy()


def render_art_scene(kind: str, type_name: str, width: int, height: int) -> Image.Image:
    """Return a calm printed picture background with a species-type tint."""
    kind, width, height, type_name = _normalise(kind, width, height, type_name)
    return _material(kind, "art", width, height, type_name).copy()


def render_reading_stock(kind: str, width: int, height: int) -> Image.Image:
    """Return lightly clouded, stock-matched paper for the effect text box."""
    kind, width, height, type_name = _normalise(kind, width, height)
    return _material(kind, "reading", width, height, type_name).copy()
