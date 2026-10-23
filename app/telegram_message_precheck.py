"""Conservative, zero-LLM Telegram target gates.

This rejects only obviously low-context, administrative or unsolicited
solicitation messages. Subject-matter relevance cannot be established with
these rules; uncertain messages still require the normal quality checks.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_LINKS = re.compile(r"(?i)(?:https?://|t\.me/|@)[^\s]+")
_WORDS = re.compile(r"[^\W_]+", re.UNICODE)
_ADMIN = re.compile(
    r"(?iu)^\s*(?:правила|закрепл[её]нное сообщение|реквизиты|"
    r"как вступить|правила участия)\b"
)
_BARE_CTA = re.compile(
    r"(?iu)\b(?:заполн(?:ите|ить)\s+анкету|"
    r"переходите\s+по\s+ссылке|подписывайтесь|"
    r"жми(?:те)?\s+сюда)\b"
)
_AD_MARKERS = (
    "канал и личка в шапке профиля",
    "пишите в личку",
    "писать в лс",
    "по вопросам рекламы",
)


@dataclass(frozen=True, slots=True)
class TelegramTargetPrecheck:
    eligible: bool
    reason: str


def precheck_telegram_target(text: str) -> TelegramTargetPrecheck:
    """Reject easy non-conversations before any LLM call.

    No keyword blacklist for the promoted product or adult communities: a
    short question or an actual conversation may genuinely be useful. Avoid
    deciding nuanced topicality with brittle substring matching.
    """
    stripped = _LINKS.sub(" ", str(text or "")).strip()
    words = _WORDS.findall(stripped)
    if not words:
        return TelegramTargetPrecheck(False, "EMPTY_OR_LINK_ONLY")
    # Four-word click prompts and six-word invitations are not a discussion.
    if len(words) <= 6 and "?" not in stripped:
        return TelegramTargetPrecheck(False, "NO_SUBSTANTIVE_CONVERSATION")
    if _BARE_CTA.search(stripped) and len(words) <= 12 and "?" not in stripped:
        return TelegramTargetPrecheck(False, "BARE_CALL_TO_ACTION")
    if _ADMIN.search(stripped) and "?" not in stripped:
        return TelegramTargetPrecheck(False, "ADMINISTRATIVE_ANNOUNCEMENT")
    lowered = stripped.casefold()
    if any(marker in lowered for marker in _AD_MARKERS) and "?" not in stripped:
        return TelegramTargetPrecheck(False, "UNSOLICITED_PROMOTIONAL_MESSAGE")
    return TelegramTargetPrecheck(True, "ELIGIBLE_FOR_CONTEXTUAL_REVIEW")
