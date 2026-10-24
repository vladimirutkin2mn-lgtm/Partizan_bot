"""Contract tests for a playful but factual Telegram community reply."""

from app.community_message_prompt import (
    COMMUNITY_MESSAGE_GUIDANCE,
    COMMUNITY_MESSAGE_PROMPT_VERSION,
)


def test_one_grounded_review_only_reply_for_group_or_channel():
    text = COMMUNITY_MESSAGE_GUIDANCE
    assert COMMUNITY_MESSAGE_PROMPT_VERSION == "community_message_v10_coquettish_role_voice"
    assert "Write ONE comment" in text
    assert "CHANNEL_COMMENT" in text
    assert "GROUP_REPLY" in text
    assert "language of the" in text
    assert "Never publish" in text


def test_prompt_matches_playful_subculture_without_forcing_all_groups():
    text = COMMUNITY_MESSAGE_GUIDANCE
    assert "a little flirtation" in text
    assert "шибари" in text
    assert "нижние" in text
    assert "unrelated groups" in text
    assert "Find ONE deliciously specific angle" in text
    assert "surprising contrast" in text
    assert "a little provocative" in text


def test_factuality_and_commercial_transparency_remain():
    text = COMMUNITY_MESSAGE_GUIDANCE
    assert "Never invent" in text
    assert "Do not invent personal experience" in text
    assert "Do not disguise" in text
    assert "material commercial connection disclosed" in text


def test_style_examples_are_mischievous_not_generic_admin_questions():
    text = COMMUNITY_MESSAGE_GUIDANCE
    assert "FLAT:" in text
    assert "GOOD VIBE:" in text
    assert "главная проверка для самых" in text
    assert "do not copy any sample" in text
    assert "Подскажите, пожалуйста" in text
    assert "HYPOTHETICAL" in text


def test_prompt_remains_compact_for_single_call():
    assert len(COMMUNITY_MESSAGE_GUIDANCE) < 3700
