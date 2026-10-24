"""Discover public Telegram post links in the ONE selected message, no network calls.

A linked post is a research lead, NOT a verified target or permission to send.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_LINK = re.compile(
    r"(?i)(?<![\w.-])(?:https?://)?(?:t\.me|telegram\.me)"
    r"/[A-Za-z][A-Za-z0-9_]{4,31}/[0-9]+(?:\?[^\s)\]>]*)?"
)
_HANDLE = re.compile(r"[a-z][a-z0-9_]{4,31}")
_ID = re.compile(r"[0-9]{1,15}")


def _canonical_post(raw: object, *, exclude_handle: str) -> str | None:
    text = str(raw or "").strip().rstrip(".,!;:)]}>")
    if not text:
        return None
    if not text.lower().startswith(("https://", "http://")):
        text = "https://" + text
    try:
        parsed = urlsplit(text)
    except ValueError:
        return None
    if parsed.scheme != "https" or parsed.hostname not in {
        "t.me", "telegram.me",
    } or parsed.username or parsed.password or parsed.port:
        return None
    parts = [x for x in parsed.path.split("/") if x]
    if (
        len(parts) != 2
        or not _HANDLE.fullmatch(parts[0].casefold())
        or not _ID.fullmatch(parts[1])
        or int(parts[1]) <= 0
        or parts[0].casefold() == exclude_handle.casefold()
    ):
        return None
    return f"https://t.me/{parts[0].casefold()}/{int(parts[1])}"


def referenced_public_posts(
    message: object, *, own_handle: str, max_refs: int = 6,
) -> tuple[str, ...]:
    """Read native body, hyperlink entities and URL buttons, not older posts."""
    candidates: list[str] = []
    body = str(getattr(message, "message", "") or "")
    candidates.extend(match.group(0) for match in _LINK.finditer(body))
    for entity in getattr(message, "entities", None) or []:
        if getattr(entity, "url", None):
            candidates.append(str(entity.url))
    for row in getattr(message, "buttons", None) or []:
        for button in row if isinstance(row, (list, tuple)) else [row]:
            if getattr(button, "url", None):
                candidates.append(str(button.url))
    found: list[str] = []
    for raw in candidates:
        normalized = _canonical_post(raw, exclude_handle=own_handle)
        if normalized and normalized not in found:
            found.append(normalized)
            if len(found) >= max_refs:
                break
    return tuple(found)
