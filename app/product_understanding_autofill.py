"""Conservative, no-model-call product facts from a founder's short description.

A Telegram topic + channel format is enough to infer minimal category,
audience and subscription value. This does NOT infer post formats, paid
services, personas or promises from an inaccessible Telegram invite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.product_source_quality import is_campaign_asset_brief

_GENERIC_NAMES = {
    "product", "new project", "existing community", "community",
    "telegram", "telegram channel", "channel", "group", "my product",
    "project", "promoted asset", "social account",
}
_TELEGRAM_WORDS = re.compile(
    r"(?i)(?:telegram|телеграм(?:м)?|тг|channel|канал|групп|сообщество)"
)
_CYRILLIC = re.compile(r"[А-Яа-яЁё]")
_WRAPPER = re.compile(r"(?i)^\s*(?:promoted asset|asset type)\s*:\s*")


@dataclass(frozen=True)
class ProductAutofill:
    name: str
    description: str
    value_proposition: str
    known_audience: str
    product_type: str
    inference_basis: str
    inference_notice: str


def _topic(project_name: str, saved_name: str) -> str:
    for candidate in (project_name, saved_name):
        cleaned = _WRAPPER.sub("", candidate or "").strip(" .:;–-")
        if not cleaned or cleaned.casefold() in _GENERIC_NAMES:
            continue
        if is_campaign_asset_brief(cleaned) or len(cleaned) > 80:
            continue
        # A brand or a theme named by the founder is a topical cue; not a
        # verified promise of a paid service or a conversation with an AI.
        return cleaned[:80]
    return ""


def infer_telegram_channel(
    *, project_name: str, saved_name: str, description: str,
    project_type: str, source_url: str,
) -> ProductAutofill | None:
    brief = " ".join((description or "").split()).strip()
    is_telegram = (
        project_type == "TELEGRAM_COMMUNITY"
        or (source_url or "").lower().startswith(("https://t.me/", "http://t.me/"))
        or bool(_TELEGRAM_WORDS.search(brief))
    )
    topic = _topic(project_name, saved_name)
    if not is_telegram or not topic:
        return None
    # A long campaign wrapper is not the founder's actual product description.
    if is_campaign_asset_brief(brief):
        brief = ""
    russian = bool(_CYRILLIC.search(brief))
    # Derive only what a subscriber receives by subscribing to a themed
    # channel. Never assert access to chats, specific post kinds or AI services.
    inferred_description = (
        f"Телеграм-канал на тему {topic}."
        if russian else f"Telegram channel about {topic}."
    )
    return ProductAutofill(
        name=(project_name or saved_name or topic).strip()[:120],
        description=(brief or inferred_description)[:2000],
        value_proposition=(
            f"Подписчики могут получать публикации канала на тему {topic}."
            if russian else f"Subscribers can follow channel posts about {topic}."
        ),
        known_audience=(
            f"Люди, которым интересна тема {topic}."
            if russian else f"People interested in {topic}-themed content."
        ),
        product_type="Telegram channel",
        inference_basis="PROJECT_NAME_AND_DESCRIPTION",
        inference_notice=(
            "Topic and channel format inferred from your project, not from "
            "private Telegram posts. Exact content and additional features remain unverified."
        ),
    )
