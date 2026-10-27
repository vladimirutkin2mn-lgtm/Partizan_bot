"""Compact product-derived voice guidance for one-call organic drafts.

No LLM call is made here. The drafting model infers the suitable author voice
from current product, audience and target in the same call as the comment.
Variants are constrained, measurable AutoResearch inputs, not personas a
customer must fill out by hand.
"""

from __future__ import annotations

from typing import Literal

VoiceVariantId = Literal["adaptive", "assertive", "playful", "warm", "direct", "dry_wit"]

VOICE_VARIANT_IDS = frozenset(
    ("adaptive", "assertive", "playful", "warm", "direct", "dry_wit")
)
VOICE_PROFILE_VERSION = "product_derived_voice_v1"
COMMUNITY_CREATIVE_PROMPT_VERSION = "community_message_v16_1_connected_account_voice"

COMMUNITY_CREATIVE_GUIDANCE = """Write ONE message in the target community language.

Your constant business objective is to attract relevant customers. Earn reader
interest in the author through a fitting point of view, personality, wit or
reaction. The message need not be advice, a compliment or a question. Do not
add promotional links, profile invitations, generic praise, lectures or a
second sentence that contributes nothing.

Infer a distinct communication voice from the supplied product positioning,
the audience, the actual_publisher_profile (when available), and the community
register. Use the Telegram profile's real display name, username and bio to
keep the message consistent with the author the reader will see. If the
profile is absent, do not assume a personal identity or invent a profile
bio. Keep the author's voice recognizable while adapting vocabulary and
level of informality to this specific discussion. Do NOT use one category
stereotype for every message or roleplay personal experiences. The selected
voice experiment is a stylistic nudge, not permission to invent identity,
endorsements or product claims.

CHANNEL_COMMENT replies to the exact selected channel post.
GROUP_REPLY replies to the exact selected group message.
STANDALONE_POST opens a topic appropriate to the verified group context,
without pretending to reply to an older message. For comments and replies,
ground your reaction in the selected newest native message, not older posts.

A natural reaction, humour, curiosity or mild disagreement can earn attention
without being 'helpful' in a corporate way. One short complete thought is
usually enough. Never make the entire message a covert advertisement.

Never invent attendance, customer experience, relationships, credentials,
product results or other factual claims. Follow applicable content boundaries,
commercial-disclosure requirements and sending permissions; those rules are
enforced independently of this creative style prompt. Return only the
requested structured draft and never claim to have sent it.
"""


_VOICE_NUDGES = {
    "adaptive": "Determine the best voice from product, intended audience and community.",
    "assertive": "Test a confident, self-assured perspective when it fits the context.",
    "playful": "Test light playfulness or teasing only where the community welcomes it.",
    "warm": "Test warm, inviting language without customer-service clichés.",
    "direct": "Test economical, direct statements rather than an explanation.",
    "dry_wit": "Test understated, dry humour without forced jokes.",
}


def build_voice_context(
    product_data: dict,
    *,
    community_title: str,
    community_about: str,
    variant_id: VoiceVariantId = "adaptive",
) -> dict:
    """Compose bounded product/audience facts and an adaptive style variable."""
    if variant_id not in VOICE_VARIANT_IDS:
        raise ValueError("Unknown product voice variant")
    def field(name: str, limit: int) -> str:
        return str(product_data.get(name) or "").strip()[:limit]

    def items(name: str, count: int, max_chars: int) -> list[str]:
        raw = product_data.get(name)
        return [
            str(x).strip()[:max_chars]
            for x in (raw if isinstance(raw, list) else [])[:count]
            if str(x).strip()
        ]

    return {
        "product_positioning": {
            "name": field("name", 90),
            "description": field("description", 650),
            "value_proposition": field("value_proposition", 320),
            "usp": field("usp", 250),
            "product_type": field("product_type", 100),
            "problem_or_desire": field("problem_or_desire", 220),
            "known_audience": items("known_audience", 3, 130),
            "use_cases": items("use_cases", 2, 120),
            "language": field("language", 60),
        },
        "voice": {
            "profile_version": VOICE_PROFILE_VERSION,
            "variant_id": variant_id,
            "approach": "derive_from_product_audience_and_community_in_same_generation",
            "stylistic_test": _VOICE_NUDGES[variant_id],
            "community_title": community_title[:150],
            "community_register_hint": community_about[:260],
        },
    }
