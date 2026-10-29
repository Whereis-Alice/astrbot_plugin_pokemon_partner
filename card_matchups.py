"""Immutable, cached main-series type matchups for the two draw card styles.

These are species type multipliers, independent of abilities, moves, weather,
and the TCG's damage modifiers. Neutral attacking types are intentionally omitted.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from typing import Any

try:
    from .pokedex_db import PokedexDB
except ImportError:
    from pokedex_db import PokedexDB


TYPE_ORDER = (
    "一般", "火", "水", "电", "草", "冰", "格斗", "毒", "地面", "飞行",
    "超能力", "虫", "岩石", "幽灵", "龙", "恶", "钢", "妖精",
)
_TYPE_INDEX = {name: index for index, name in enumerate(TYPE_ORDER)}
_GROUP_KEYS = ("weak", "resist", "immune")
Matchup = tuple[str, float]


def _normalized_types(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if isinstance(value, str):
        values = re.split(r"[/／、,，]", value)
    elif isinstance(value, Sequence):
        values = value
    else:
        return ()
    if any(not isinstance(name, str) for name in values):
        return ()
    names = tuple(dict.fromkeys(name.strip() for name in values if name.strip()))
    if not names or len(names) > 2 or any(name not in _TYPE_INDEX for name in names):
        return ()
    return tuple(sorted(names, key=_TYPE_INDEX.__getitem__))


@lru_cache(maxsize=1)
def _database() -> PokedexDB:
    return PokedexDB()


@lru_cache(maxsize=192)
def _groups(types: tuple[str, ...]) -> tuple[tuple[Matchup, ...], ...]:
    if not types:
        return ((), (), ())
    result = _database().matchups(list(types))
    if result is None:
        return ((), (), ())
    groups = []
    for key in _GROUP_KEYS:
        values = []
        for entry in result[key]:
            if key == "immune":
                name, multiplier = entry, 0.0
            else:
                name, raw_multiplier = entry.rsplit("×", 1)
                multiplier = float(raw_multiplier)
            values.append((name, multiplier))
        groups.append(tuple(sorted(values, key=lambda item: _TYPE_INDEX[item[0]])))
    return tuple(groups)


def matchup_groups(
    row: Mapping[str, Any], types: str | Sequence[str] | None = None,
) -> dict[str, tuple[Matchup, ...]]:
    """Return base defensive weaknesses, resistances and immunities.

    ``types`` overrides ``row['types']`` when supplied. Both slash-separated
    Chinese text and a type sequence are accepted. Invalid or absent types yield
    empty groups; stale row ``weak_to`` fields never invent a type matchup.
    The returned mapping is fresh, and cached values are immutable.
    """
    names = _normalized_types(row.get("types") if types is None else types)
    return dict(zip(_GROUP_KEYS, _groups(names)))
