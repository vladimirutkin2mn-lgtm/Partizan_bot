"""Conservative evidence from a Telegram community's own public description.

The community about text is moderator-controlled, but ambiguous text is not
permission. A positive match is proof only of a visible, explicit allowance,
not a promise that a publisher account has write access.
"""
from __future__ import annotations

import re

from app.telegram_research import TelegramCommunitySnapshot

# Negation has precedence. Never infer promotion rights from audience fit,
# generic "chat", other users' messages or a writable discussion thread.
_DENY = re.compile(
    r"(?:без\s+реклам[ыа]|реклам[аы]\s+запрещен[аы]|"
    r"запрещен[аы]\s+реклам[ауы]|самореклама\s+запрещена|"
    r"no\s+(?:ads|advertis(?:ing|ements)|self[- ]promo)|"
    r"(?:ads|advertising|promotion)\s+(?:not\s+allowed|prohibited))",
    re.IGNORECASE,
)
_ALLOW = re.compile(
    r"(?:самореклама\s+разрешен[аы]|"
    r"реклам[ауы]\s+разрешен[аы]|"
    r"разрешен[аы]\s+(?:само)?реклам[аыу]|"
    r"можно\s+(?:публиковать\s+)?(?:само)?реклам[уаыу]|"
    r"(?:self[- ]promotion|advertising|ads|promotional posts?)\s+"
    r"(?:is\s+)?(?:allowed|permitted|welcome)|"
    r"(?:allow|permit)\s+(?:self[- ]promotion|advertising|ads))",
    re.IGNORECASE,
)


def public_community_promotion_rules(snapshot: TelegramCommunitySnapshot) -> dict:
    about = str(snapshot.about or "")[:4000]
    verified = bool(_ALLOW.search(about)) and not bool(_DENY.search(about))
    return {
        "status": "VERIFIED_ALLOWED" if verified else "UNVERIFIED",
        "allows_participation": verified,
        "allows_promotion": verified,
        "source_url": snapshot.url,
        "source_type": "TELEGRAM_NATIVE_COMMUNITY_ABOUT",
        "checked_at": snapshot.source_checked_at.isoformat(),
    }
