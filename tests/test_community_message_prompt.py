"""Contract tests for short, conversational Telegram drafting guidance."""

from app.community_message_prompt import (
    COMMUNITY_MESSAGE_GUIDANCE,
    COMMUNITY_MESSAGE_PROMPT_VERSION,
)


def test_social_voice_prompt_keeps_one_native_language_draft_and_no_send():
    text = COMMUNITY_MESSAGE_GUIDANCE
    assert COMMUNITY_MESSAGE_PROMPT_VERSION == "community_message_v7_social_voice"
    assert "Generate exactly ONE message" in text
    assert "LANGUAGE and register" in text
    assert "CHANNEL_COMMENT" in text
    assert "GROUP_REPLY" in text
    assert "never publish" in text


def test_prompt_encourages_social_voice_without_fabricated_experiences():
    text = COMMUNITY_MESSAGE_GUIDANCE
    assert "Light slang" in text
    assert "Vary rhythm naturally" in text
    assert "важно отметить" in text
    assert "в заключение" in text
    assert "длинных" not in text  # editorial rules are in English
    assert "No bullet/numbered lists" in text
    assert "long em dashes" in text
    assert "at most one" in text
    assert "NEVER fabricate personal" in text
    assert "commercial affiliation disclosed" in text


def test_examples_contrast_generic_praise_and_specific_reply():
    text = COMMUNITY_MESSAGE_GUIDANCE
    assert "ROBOTIC:" in text
    assert "NATURAL:" in text
    assert "NOT a reusable formula" in text
    assert "not an obligatory pitch" in text


def test_prompt_not_needlessly_long_for_one_output():
    # Savings matter for the product's normal one-call generation path.
    assert len(COMMUNITY_MESSAGE_GUIDANCE) < 3600
