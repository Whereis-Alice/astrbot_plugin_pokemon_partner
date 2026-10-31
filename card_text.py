"""Text normalization at card layout boundaries; source data stays untouched."""
from __future__ import annotations


def single_line_text(value: object) -> str:
    """Replace imported line breaks/tabs without collapsing horizontal spacing."""
    return " ".join(str(value).replace("\t", " ").splitlines())


def paragraph_text(value: object) -> str:
    """Keep paragraph breaks while making CRLF/CR and tabs safe for Pillow."""
    return str(value).replace("\r\n", "\n").replace("\r", "\n").replace("\t", " ")
