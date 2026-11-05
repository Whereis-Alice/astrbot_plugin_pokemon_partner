"""Mega printed finishes, using the retained card stock and frame fragments.

Only Mega cards use these treatments. The outer dark ink becomes coloured
material while the scanned light-grey reflection and inner card remain intact.
The module needs only Pillow and keeps continuous colour fields in small caches.
"""
from __future__ import annotations

import math
from functools import lru_cache
from collections.abc import Sequence

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageMath, ImageOps

try:
    from .yugioh_frames import _frame
except ImportError:
    from yugioh_frames import _frame


_PIGMENTS = ((44, 112, 153), (119, 62, 154), (155, 57, 111),
             (158, 121, 54), (48, 127, 109))
_OUTER_COLOURS = ((23, 144, 157), (57, 94, 194), (144, 42, 187),
                  (190, 47, 112), (179, 143, 34), (26, 142, 98), (23, 144, 157))
_PORTRAIT_COLOURS = ((65, 206, 204), (84, 143, 231), (156, 99, 213),
                     (230, 104, 165), (237, 194, 99), (120, 208, 161), (65, 206, 204))


def _math(expression: str, **channels):
    # Expressions are fixed local arithmetic, never supplied by a message.
    # unsafe_eval replaced eval in recent Pillow; keep compatibility with 10.x.
    evaluate = getattr(ImageMath, 'unsafe_eval', None) or getattr(ImageMath, 'eval')
    return evaluate(expression, **channels)


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _spectrum(phase: float, palette):
    position = (phase % 1.0)*(len(palette)-1)
    index = math.floor(position)
    amount = position-index
    return tuple(a+(b-a)*amount for a, b in zip(palette[index], palette[index+1]))


def _pigment(nx: float, ny: float):
    phase = .08+.52*nx+.46*ny+.035*math.sin(ny*math.tau)
    weights = tuple(math.exp(4.4*math.cos((phase-index/5)*math.tau)) for index in range(5))
    total = sum(weights)
    return tuple(sum(weight*colour[channel] for weight, colour in zip(weights, _PIGMENTS))/total
                 for channel in range(3))


def _rails(width: int, height: int, border: int):
    bx, by = min(border, max(1, width//3)), min(border, max(1, height//3))
    return ((0, 0, width, by), (0, height-by, width, height),
            (0, by, bx, height-by), (width-bx, by, width, height-by))


@lru_cache(maxsize=2)
def _pigment_field(width: int, height: int):
    sw = min(width, 180)
    sh = max(1, round(sw*height/width))
    field = Image.new('RGB', (sw, sh))
    field.putdata([tuple(round(c) for c in _pigment(x/max(1, sw-1), y/max(1, sh-1)))
                   for y in range(sh) for x in range(sw)])
    return field.resize((width, height), Image.Resampling.BICUBIC)


@lru_cache(maxsize=5)
def _mega_outer(width: int, height: int, kind: str):
    """Colour original warm stock, retaining the neutral impressed mould."""
    paper = _frame('outer', width, height, 'C')
    scanned = _frame('outer', width, height, 'SHINY' if kind == 'SHINY' else 'SSR')
    result = scanned.copy()
    border = max(1, round(56*min(width/1080, height/1680)))
    red, green, blue, _ = paper.split()
    warm = ImageChops.multiply(
        ImageChops.subtract(red, green).point(lambda v: 255 if v > 9 else 0),
        ImageChops.subtract(red, blue).point(lambda v: 255 if v > 12 else 0))
    # The original preview used a small blurred selection, not a new bevel.
    warm = warm.filter(ImageFilter.GaussianBlur(.45))
    gray = ImageOps.grayscale(paper)
    for x0, y0, x1, y1 in _rails(width, height, border):
        patch = scanned.crop((x0, y0, x1, y1))
        source = gray.crop((x0, y0, x1, y1))
        mask = warm.crop((x0, y0, x1, y1))
        output = []
        for index, (rgba, value, coverage) in enumerate(zip(patch.getdata(), source.getdata(), mask.getdata())):
            y, x = divmod(index, x1-x0)
            amount = coverage/255*_clip((value-35)/25)*_clip((212-value)/30)
            if amount <= 0:
                output.append(rgba)
                continue
            pigment = _pigment((x+x0)/max(1, width-1), (y+y0)/max(1, height-1))
            colour = tuple(c*(.35+value/255*1.04) for c in pigment)
            if kind == 'SHINY':
                colour = tuple(c*.88+add for c, add in zip(colour, (19, 20, 18)))
            output.append(tuple(max(0, min(255, int(old*(1-amount)+new*amount)))
                                for old, new in zip(rgba[:3], colour))+(rgba[3],))
        patch.putdata(output)
        result.paste(patch, (x0, y0))
    # Cached fragments are immutable, including their clear aperture.
    left, top = round(56*width/1080), round(56*height/1680)
    ImageDraw.Draw(result).rectangle((left, top, width-left-1, height-top-1), fill=(0, 0, 0, 0))
    return result


def paint_mega_outer(base: Image.Image, kind: str) -> None:
    """Paint the approved printed Mega outer mould before the card panels."""
    if base.mode != 'RGBA':
        raise ValueError('Mega card base must be RGBA')
    base.alpha_composite(_mega_outer(*base.size, kind))


def paint_mega_dark_stock(base: Image.Image, kind: str) -> None:
    """Keep SSR's clouded stock, replacing its neutral dark ink with Mega hues."""
    if base.mode != 'RGBA':
        raise ValueError('Mega card base must be RGBA')
    if kind != 'SSR':
        return
    red, green, blue, alpha = base.split()
    high = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    low = ImageChops.darker(ImageChops.darker(red, green), blue)
    chroma = ImageChops.subtract(high, low)
    mask = _math("min(max((convert(h,'F')-6)/14,0),1)*"
                 "min(max((145-convert(h,'F'))/30,0),1)*"
                 "min(max((38-convert(c,'F'))/22,0),1)", h=high, c=chroma)
    value = ImageOps.grayscale(base)
    pigments = _pigment_field(*base.size).split()
    colour_luma = _math("convert(r,'F')*.299+convert(g,'F')*.587+convert(b,'F')*.114",
                        r=pigments[0], g=pigments[1], b=pigments[2])
    painted = [_math("convert(convert(o,'F')*(1-a)+"
                     "convert(p,'F')*convert(v,'F')/max(l,1)*a,'L')",
                     o=original, p=pigment, v=value, l=colour_luma, a=mask)
               for original, pigment in zip((red, green, blue), pigments)]
    base.paste(Image.merge('RGBA', (*painted, alpha)))


def finish_mega_outer(base: Image.Image) -> None:
    """Replace even deepest outer black ink with colour; retain light pixels.

The final 32px band is the user's marked area. Its scanned clouding controls
the light and dark texture. A shadow lift gives black samples visible pigment;
the smooth 50..100 transition leaves grey/white reflection pixels unchanged.
"""
    if base.mode != 'RGBA':
        raise ValueError('Mega card base must be RGBA')
    width, height = base.size
    border = max(1, round(32*min(width/1080, height/1680)))
    for x0, y0, x1, y1 in _rails(width, height, border):
        patch = base.crop((x0, y0, x1, y1))
        output = []
        for index, rgba in enumerate(patch.getdata()):
            high = max(rgba[:3])
            if high >= 100 or rgba[3] == 0:
                output.append(rgba)
                continue
            amount = _clip((100-high)/50)
            amount = amount*amount*(3-2*amount)
            value = sum(channel*weight for channel, weight in zip(rgba[:3], (.299, .587, .114)))
            target = value+40*math.exp(-value/40)
            y, x = divmod(index, x1-x0)
            phase = .075+.60*(x+x0)/max(1, width-1)+.58*(y+y0)/max(1, height-1)
            pigment = _spectrum(phase, _OUTER_COLOURS)
            luminance = sum(c*w for c, w in zip(pigment, (.299, .587, .114)))
            colour = tuple(c*target/luminance for c in pigment)
            output.append(tuple(max(0, min(255, round(old*(1-amount)+new*amount)))
                                for old, new in zip(rgba[:3], colour))+(rgba[3],))
        patch.putdata(output)
        base.paste(patch, (x0, y0))


@lru_cache(maxsize=2)
def _portrait_gradient(size, origin, card_size):
    width, height = size
    ox, oy = origin
    cw, ch = card_size
    sw = min(width, 160)
    sh = max(1, round(sw*height/width))
    field = Image.new('RGB', (sw, sh))
    colours = []
    for y in range(sh):
        ny = (oy+y*height/max(1, sh-1))/ch
        for x in range(sw):
            nx = (ox+x*width/max(1, sw-1))/cw
            phase = .10+nx*.70+ny*.55+.025*math.sin(ny*math.tau)
            colours.append(tuple(round(c) for c in _spectrum(phase, _PORTRAIT_COLOURS)))
    field.putdata(colours)
    return field.resize(size, Image.Resampling.BICUBIC).convert('RGBA')


def paint_mega_portrait(base: Image.Image, art: Image.Image, box: Sequence[int]) -> None:
    """Place unchanged official artwork with an external spectrum silhouette."""
    x, y, width, height = map(round, box)
    art = art.convert('RGBA')
    bounds = art.getchannel('A').getbbox()
    if not bounds:
        return
    art = art.crop(bounds)
    scale = min(3.0, (width-8)/art.width, height/art.height)
    art = art.resize((max(1, round(art.width*scale)), max(1, round(art.height*scale))),
                     Image.Resampling.LANCZOS)
    art = ImageEnhance.Sharpness(art).enhance(1.08)
    tx, ty = x+(width-art.width)//2, y+(height-art.height)//2
    padding = 24
    size = (art.width+padding*2, art.height+padding*2)
    origin = (tx-padding, ty-padding)
    alpha = Image.new('L', size)
    alpha.paste(art.getchannel('A'), (padding, padding))
    shadow = Image.new('RGBA', size, (0, 7, 12, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(8)).point(lambda a: round(a*.60)))
    base.alpha_composite(shadow, (origin[0]+4, origin[1]+8))
    expanded = alpha.filter(ImageFilter.MaxFilter(11))
    rim_alpha = ImageChops.subtract(expanded.filter(ImageFilter.GaussianBlur(.45)), alpha)
    window = Image.new('L', size)
    # Retain the artwork-window clipping used in the approved standalone proof.
    ImageDraw.Draw(window).rectangle((76-origin[0], 254-origin[1],
                                     base.width-77-origin[0], 1078-origin[1]), fill=255)
    rim_alpha = ImageChops.multiply(rim_alpha, window)
    rim = _portrait_gradient(size, origin, base.size).copy()
    rim.putalpha(rim_alpha.point(lambda a: round(a*.88)))
    base.alpha_composite(rim, origin)
    base.alpha_composite(art, (tx, ty))


__all__ = ['paint_mega_outer', 'paint_mega_dark_stock', 'finish_mega_outer', 'paint_mega_portrait']
