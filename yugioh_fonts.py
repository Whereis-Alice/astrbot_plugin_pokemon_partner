"""Bundled OFL Song/Ming serif fonts for the monster-card print design.

The static subsets retain plugin text and standard simplified Chinese. Fonts are
loaded locally on every platform; fontTools is only used during asset preparation.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import ImageFont


_FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
_FILES = {"body": "PartnerCardSerif-Medium.ttf", "title": "PartnerCardSerif-ExtraBold.ttf",
          "name": "PartnerDuelInscription-Regular.ttf"}


@lru_cache(maxsize=48)
def _load_face(size: int, role: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(_FONT_DIR / _FILES[role]), int(size))


@lru_cache(maxsize=1)
def _name_coverage() -> frozenset[str]:
    return frozenset((_FONT_DIR / "name-coverage.txt").read_text(encoding="utf-8"))


def load_card_font(size: int, role: str = "body", *, text: str = "") -> ImageFont.FreeTypeFont:
    """Load portable print faces; custom names fall back as a complete line.

    The small inscription face covers all shipped species and form names. Pass
    the complete name when measuring and drawing it so a custom name outside
    that subset consistently uses the bundled general Chinese reading face.
    """
    if role not in _FILES:
        raise ValueError("未知的游戏王卡片字体角色")
    if role == "name" and text and not set(text).issubset(_name_coverage()):
        role = "body"
    return _load_face(int(size), role)
