"""Versioned creative instructions for the optional persisted-voice writer.

The immutable VoiceProfileVersion gives a concrete character. Platform policy
and conversion permissions are not delegated to this untrusted style object.
"""
from __future__ import annotations

import re

from app.voice_profiles import VOICE_WRITER_STYLE_VERSION, VoiceProfileVersion

PERSISTED_ACTION_SYSTEM_PROMPT = """You write exactly ONE organic community message.
You already have a concrete author communication style; DO NOT infer a new
personality or replace it with a product-category stereotype. The message
should be relevant and interesting on its own, not a sales pitch, generic
compliment, paraphrase, invented life experience or a forced punchline.
Marketing attribution and the profile CTA are handled separately.

Write in the language of the selected community message. Never invent an
author biography, customer stories, attendance, credentials, product features,
promotional destination, Telegram channel or personal experience.
Observe the supplied permitted conversion mechanism. For PROFILE_CLICK,
do NOT mention or link the product or invite people to open the profile.
No sending or publishing is authorized by this writing task.
Return only the requested structured response.
"""

PERSISTED_COMMUNITY_PROMPT = """CHANNEL_COMMENT: respond only to the selected native channel post.
GROUP_REPLY: respond only to the selected native group message.
STANDALONE_POST: open an on-topic group conversation without pretending
to answer a message. Do not read or invent a historical thread.

Use the supplied voice_profile to guide stance, vocabulary and rhythm.
Adapt register to the specific discussion without changing the established
character. A genuine reaction can be brief or humorous, but neither jokes,
questions, opinions, advice nor product references are mandatory.
If the comment would fit almost any post unchanged, make it more specific.
Never advertise an unverified resource or invent a promotional channel.
"""

PERSISTED_COMMUNITY_PROMPT_VERSION = "community_message_v17_persisted_voice"


def writer_voice_payload(version: VoiceProfileVersion) -> dict:
    """Only relevant style fields. No fabricated identity or profile secrets."""
    p = version.profile
    return {
        "voice_profile_version_id": str(version.id),
        "voice_profile_content_hash": version.content_hash,
        "schema_version": version.schema_version,
        "writer_style_version": VOICE_WRITER_STYLE_VERSION,
        "character_summary": p.character_summary,
        "default_tone": p.default_tone,
        "speech_traits": p.speech_traits,
        "adaptation_rules": p.adaptation_rules,
        "style_instruction": p.style_instruction,
    }


# This deterministic check complements, but does not replace, policy review.
# It applies only when the selected tactic/variant uses profile curiosity
# without an in-message product link or promotion. Reject and retain the
# paid draft's failure state; NEVER silently strip/edit or regenerate it.
_PROMOTIONAL_URL = re.compile(
    r"(?i)(?:https?://|www\\.|t\\.me/|telegram\\.me/)"
)
_EXPLICIT_PROFILE_CTA = re.compile(
    r"(?iu)(?:"
    r"\\b(?:check|visit|open|follow|see|view|look at)\\b.{0,35}"
    r"\\b(?:my|our)\\s+(?:profile|bio)\\b"
    r"|\\blink\\s+in\\s+(?:my|our|the)\\s+bio\\b"
    r"|\\b(?:загляни|загляните|перейди|перейдите|переходи|"
    r"смотри|смотрите|открой|откройте)\\b.{0,40}"
    r"\\b(?:профил\\w*|био)\\b"
    r"|\\bссылк\\w*\\s+в\\s+профил\\w*\\b"
    r")"
)


def validate_profile_click_draft(content_text: str, *, product_name: str) -> None:
    """Fail closed on obvious in-message promotion for organic profile discovery.

    This deliberately avoids semantic inference, author-identity assumptions,
    and automatic rewrites of a potentially already billed model response.
    """
    if _PROMOTIONAL_URL.search(content_text):
        raise ValueError("VOICE_PROFILE_CLICK_LINK_NOT_ALLOWED")
    if _EXPLICIT_PROFILE_CTA.search(content_text):
        raise ValueError("VOICE_PROFILE_CLICK_CTA_NOT_ALLOWED")
    normalized_name = product_name.strip()
    if len(normalized_name) >= 8 and re.search(
        rf"(?i)(?<!\\w){re.escape(normalized_name)}(?!\\w)", content_text
    ):
        raise ValueError("VOICE_PROFILE_CLICK_PRODUCT_MENTION_NOT_ALLOWED")
