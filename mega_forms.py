"""Mega form labels and original local Pokémon Mega Evolution symbols.

Recognition uses the explicit form field; ordinary species, Gigantamax and
Primal Reversion never receive Mega card treatment. Item artwork is packaged
locally so card rendering performs no network requests.
"""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import json
from pathlib import Path
import re
import unicodedata

from PIL import Image

try:
    from .card_text import paragraph_text, single_line_text
except ImportError:
    from card_text import paragraph_text, single_line_text

_ROOT = Path(__file__).resolve().parent/'assets/yugioh/mega'


def is_mega(row: Mapping) -> bool:
    """Recognize an explicitly named Mega form, not a species-name substring."""
    form = single_line_text(row.get('form_name') or '')
    return form.startswith('超级') or bool(re.match(r'^mega(?:\s+|-)', form, re.I))


def mega_name(row: Mapping, fallback: str) -> str:
    """Use the short Mega form name in the card's name rail."""
    if not is_mega(row):
        return single_line_text(fallback)
    form = single_line_text(row.get('form_name') or '')
    # Colour/stance variants retain their identity in the original row and art;
    # the title needs only the Mega name, including its actual X/Y/Z suffix.
    return re.split(r'[（(]', form, maxsplit=1)[0].rstrip() or single_line_text(fallback)


def mega_profile(row: Mapping) -> str:
    """Prefer existing passages about this Mega form in a shared species entry.

    Some bundled species profiles describe the base form and several alternate
    forms together. Preserve original wording; if no complete form passage can
    be identified confidently, return the original profile unchanged.
    """
    profile = paragraph_text(row.get('profile') or '').strip()
    if not is_mega(row) or not profile:
        return profile
    form = single_line_text(row.get('form_name') or '')
    pattern = ''.join(
        r'\s*[XＸxｘ]' if char in 'XＸxｘ' else
        r'\s*[YＹyｙ]' if char in 'YＹyｙ' else
        r'\s*[ZＺzｚ]' if char in 'ZＺzｚ' else re.escape(char)
        for char in form
    )
    # Avoid treating the common name as a prefix of a distinct X/Y/Z form.
    if not unicodedata.normalize('NFKC', form).rstrip().endswith(('X','Y','Z')):
        pattern += r'(?!\s*[XYZＸＹＺxyzｘｙｚ])'
    passages = []
    for match in re.finditer(pattern, profile):
        tail = re.search(r'\s', profile[match.end():])
        end = match.end()+tail.start() if tail else len(profile)
        passage = profile[match.start():end].strip()
        # A mention by itself or an abbreviated cross-reference is insufficient.
        if len(passage)-len(match.group()) < 16:
            continue
        if passage not in passages:
            passages.append(passage)
        if len(passages) == 2:
            break
    return '\n'.join(passages) if passages else profile


@lru_cache(maxsize=1)
def _stone_index() -> dict[tuple[int, str], str]:
    manifest = json.loads((_ROOT/'sources.json').read_text(encoding='utf-8'))
    return {(int(entry['dex']), entry['variant']): entry['filename']
            for entry in manifest['stones']}


def mega_stone_key(row: Mapping) -> str | None:
    """Return the correct packaged item filename for the explicit Mega form."""
    if not is_mega(row):
        return None
    try:
        dex = int(row.get('id') or row.get('id_raw') or row.get('national_id'))
    except (TypeError, ValueError):
        return None
    # Mega Rayquaza uses Dragon Ascent rather than a Mega Stone.
    if dex == 384:
        return None
    form = unicodedata.normalize('NFKC', single_line_text(row.get('form_name') or ''))
    # English species such as Skarmory and Scrafty end in "y"; that is not a
    # form suffix. Chinese names and explicit separated X/Y/Z remain accepted.
    match = re.search(r'(?<![a-z])([XYZ])(?=\s*(?:[（(]|$))', form, re.I)
    variant = match.group(1).upper() if match else ''
    return _stone_index().get((dex, variant))


@lru_cache(maxsize=128)
def _fit_asset(filename: str, width: int, height: int) -> Image.Image:
    if width < 1 or height < 1:
        raise ValueError('Mega symbol dimensions must be positive')
    with Image.open(_ROOT/filename) as opened:
        image = opened.convert('RGBA')
    bounds = image.getchannel('A').getbbox()
    if bounds:
        image = image.crop(bounds)
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    return image


def render_mega_emblem(width: int, height: int) -> Image.Image:
    """Fit the original official Mega emblem without adding a badge surround."""
    return _fit_asset('mega-evolution.png', int(width), int(height)).copy()


def render_mega_stone(row: Mapping, width: int, height: int) -> Image.Image | None:
    """Fit an original matching item sprite; absent/non-stone forms return None."""
    filename = mega_stone_key(row)
    if filename is None:
        return None
    return _fit_asset(filename, int(width), int(height)).copy()


__all__ = ['is_mega', 'mega_name', 'mega_profile', 'mega_stone_key',
           'render_mega_emblem', 'render_mega_stone']
