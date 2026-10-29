"""Read image response bodies with a real bound, including chunked transfers."""

from typing import Any

MAX_IMAGE_BYTES = 8 * 1024 * 1024


async def read_image_body(response: Any) -> bytes | None:
    """Stop an oversized body before buffering the rest of the response."""
    try:
        declared = int(response.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        declared = 0
    if declared > MAX_IMAGE_BYTES:
        return None
    body = bytearray()
    async for chunk in response.content.iter_chunked(64 * 1024):
        if len(body) + len(chunk) > MAX_IMAGE_BYTES:
            return None
        body.extend(chunk)
    return bytes(body) if body else None
