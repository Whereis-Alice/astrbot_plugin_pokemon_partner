"""宝可梦挑战指令的纯解析：名字/图鉴编号、真实 @、回合上限互不混淆。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

try:
    from .gacha import GachaError
except ImportError:
    from gacha import GachaError


CHALLENGE_COMMANDS = ("宝可梦挑战", "收藏挑战", "挑战宝可梦")
ACCEPT_COMMANDS = ("接收挑战", "接受对战", "接受挑战")
_FLAGS = {"卡片", "图片转发", "卡片转发", "转发", "合并转发", "文本", "详细", "完整"}
_NUMERIC_OPTION = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


@dataclass(frozen=True)
class SelectionRequest:
    selection: str
    shiny: bool = False


@dataclass(frozen=True)
class ChallengeRequest(SelectionRequest):
    target_id: str = ""
    target_name: str = ""
    max_turns: int = 50


def _tokens(raw: str, commands: Sequence[str]) -> tuple[str, list[str]]:
    pattern = "|".join(re.escape(command) for command in commands)
    match = re.search(rf"({pattern})(?=\s|$)", str(raw or ""))
    if not match:
        raise GachaError("没有识别到挑战指令。")
    body = str(raw)[match.end():].strip()
    # 双引号可包住英文名称；普通名字中的撇号（Farfetch'd）不会被当引号。
    return match.group(1), [a or b or c for a, b, c in re.findall(r'"([^"]*)"|“([^”]*)”|(\S+)', body)]


def round_limit(value: int | str) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value).strip()):
        raise GachaError("回合上限应为 1–100 的整数，例如 20 或 20回合。")
    limit = int(value)
    if not 1 <= limit <= 100:
        raise GachaError("回合上限必须在 1–100 之间。")
    return limit


def _options(tokens: list[str]) -> tuple[list[str], bool]:
    shiny = "闪光" in tokens
    return [token for token in tokens if token not in _FLAGS | {"闪光"}], shiny


def parse_challenge(raw: str, mentions: Sequence[tuple[str, str]] = (), *, bot_id: str = "") -> ChallengeRequest:
    """解析 `宝可梦挑战 <图鉴编号/名字> @群友 [回合]`，也兼容 @ 在前的格式。"""
    command, tokens = _tokens(raw, CHALLENGE_COMMANDS)
    tokens, shiny = _options(tokens)
    by_id = {}
    for user, name in mentions:
        user_id = str(user)
        if user_id and user_id not in {str(bot_id), "all"}:
            by_id[user_id] = by_id.get(user_id) or str(name or "")
    actual = list(by_id.items())
    if len(actual) > 1:
        raise GachaError("一次只能向一位群友发起挑战。")
    target_id, target_name = actual[0] if actual else ("", "")

    text_mentions = []
    remaining = []
    for token in tokens:
        encoded = re.fullmatch(r"(?:\[CQ:at,qq=|\[At:)([^\]]+)\]", token)
        at_value = ""
        if "@" in token:
            prefix, at_value = token.rsplit("@", 1)
            if prefix:
                remaining.append(prefix)
        elif encoded:
            at_value = encoded.group(1)
        if at_value:
            if at_value not in {str(bot_id), "all"}:
                text_mentions.append(at_value)
        else:
            remaining.append(token)
    if len(set(text_mentions)) > 1:
        raise GachaError("一次只能向一位群友发起挑战。")
    if target_id and any(value.isdigit() and value != target_id for value in text_mentions):
        raise GachaError("一次只能向一位群友发起挑战。")
    if not target_id:
        if text_mentions:
            if not text_mentions[0].isdigit():
                raise GachaError("请真正 @ 一位群友，或使用 @用户ID，不能只写昵称。")
            target_id = text_mentions[0]
        elif command != "宝可梦挑战" and len(remaining) >= 2 and remaining[0].isdigit():
            # 用户 ID 在前的旧指令格式仅在没有真实 @ 时使用。
            target_id = remaining.pop(0)
        else:
            raise GachaError("请选择自己在本群已拥有的宝可梦，并真正 @ 一位群友，例如“宝可梦挑战 25 @群友 20”；25 可换成自己的图鉴编号或名字，20 是回合上限，可以省略。")

    limits = []
    selected = []
    cursor = 0
    while cursor < len(remaining):
        token = remaining[cursor]
        if token in {"回合", "回合=", "回合：", "回合:"}:
            cursor += 1
            if cursor >= len(remaining):
                raise GachaError("请在「回合」后填写 1–100 的整数。")
            limits.append(round_limit(remaining[cursor]))
        elif token.startswith("回合"):
            limits.append(round_limit(token[2:].lstrip("=：:")))
        elif token.endswith("回合"):
            limits.append(round_limit(token[:-2]))
        else:
            selected.append(token)
        cursor += 1
    # 一个数字是图鉴编号；名字/编号后面的数字只能是回合，非法数值也应明确报错。
    while len(selected) > 1 and (selected[-1].isdigit() or _NUMERIC_OPTION.fullmatch(selected[-1])):
        limits.append(round_limit(selected.pop()))
    if len(limits) > 1:
        raise GachaError("一次挑战只能指定一个回合上限。")
    selection = " ".join(selected).strip()
    if not selection:
        raise GachaError("请填写自己已拥有的宝可梦图鉴编号或名字，例如：宝可梦挑战 25 @群友。")
    return ChallengeRequest(selection, shiny, target_id, target_name, limits[0] if limits else 50)


def parse_accept(raw: str) -> SelectionRequest:
    """接受时只选择自己的宝可梦，不更改已经约定的回合上限。"""
    _, tokens = _tokens(raw, ACCEPT_COMMANDS)
    tokens, shiny = _options(tokens)
    if any(token.startswith("回合") or token.endswith("回合") for token in tokens):
        raise GachaError("回合上限由发起方约定，接收挑战时只需填写自己的图鉴编号或名字。")
    if len(tokens) > 1 and (tokens[-1].isdigit() or _NUMERIC_OPTION.fullmatch(tokens[-1])):
        raise GachaError("回合上限由发起方约定，接收挑战时只需填写自己的图鉴编号或名字。")
    selection = " ".join(tokens).strip()
    if not selection:
        raise GachaError("请在“接收挑战”后加上你在本群已拥有的宝可梦图鉴编号或名字，例如“接收挑战 25”或“接收挑战 皮卡丘”；使用闪光个体时在末尾加“闪光”。")
    return SelectionRequest(selection, shiny)
