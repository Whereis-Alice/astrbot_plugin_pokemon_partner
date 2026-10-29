"""Bundled OFL Song/Ming serif fonts for the monster-card print design.

The static subsets retain plugin text and standard simplified Chinese. Fonts are
loaded locally on every platform; fontTools is only used during asset preparation.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_FILES = {"body": "PartnerCardSerif-Medium.ttf", "title": "PartnerCardSerif-ExtraBold.ttf"}


@lru_cache(maxsize=48)
def load_card_font(size: int, role: str = "body") -> ImageFont.FreeTypeFont:
    """Return the portable medium reading face or heavy engraved title face."""
    if role not in _FILES:
        raise ValueError("未知的游戏王卡片字体角色")
    return ImageFont.truetype(str(_FONT_DIR / _FILES[role]), int(size))
