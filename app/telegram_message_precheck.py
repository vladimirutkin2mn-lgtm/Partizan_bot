"""Cheap readability check for a selected Telegram message.

Audience fit and permission to reply were resolved upstream. The content
generator should not disqualify a whole opportunity merely because the
latest post is short, promotional or an announcement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LINKS = re.compile(r"(?i)(?:https?://|t\.me/|@)[^\s]+")
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class TelegramTargetPrecheck:
    eligible: bool
    reason: str


def precheck_telegram_target(text: str) -> TelegramTargetPrecheck:
    """Reject missing/unreadable content, never infer marketing fit from length."""
    readable = _LINKS.sub(" ", str(text or "")).strip()
    if not _WORDS.search(readable):
        return TelegramTargetPrecheck(False, "EMPTY_OR_LINK_ONLY")
    return TelegramTargetPrecheck(True, "READABLE_TARGET_FOR_REVIEW")
