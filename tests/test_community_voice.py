"""Voice experiments are validated separately from platform policy and fact checks."""
import pytest
from pydantic import ValidationError

from app.community_voice import (
    COMMUNITY_CREATIVE_GUIDANCE,
    VOICE_PROFILE_VERSION,
    build_voice_context,
)
from app.growth_autoresearch_schemas import GrowthVariantSpec


def test_voice_context_derives_from_user_product_fields_with_one_call():
    description = "Personalized conversational product. " * 30
    result = build_voice_context(
        {
            "name": "Example AI experience",
            "description": description,
            "product_type": "AI companion",
            "known_audience": ["A conversational community", "People who enjoy banter"],
            "input_brief": "Do not send the full intake",
        },
        community_title="Social chat",
        community_about="Jokes and social discussion",
    )
    assert result["voice"]["profile_version"] == VOICE_PROFILE_VERSION
    assert result["voice"]["variant_id"] == "adaptive"
    assert result["product_positioning"]["product_type"] == "AI companion"
    assert len(result["product_positioning"]["description"]) == 650
    assert "input_brief" not in result["product_positioning"]
    assert "Customer" not in result["voice"]["approach"]


def test_unapproved_voice_variants_are_rejected():
    with pytest.raises(ValueError, match="Unknown product voice variant"):
        build_voice_context({}, community_title="", community_about="",
                            variant_id="pretend_to_be_a_customer")


def test_creative_prompt_removes_unwanted_rules_without_removing_integrity():
    prompt = COMMUNITY_CREATIVE_GUIDANCE
    assert "interesting potential customers" not in prompt
    assert "attract relevant customers" in prompt
    assert "professional" not in prompt
    assert "AI-content disclosure" not in prompt
    assert "Do not claim a platform rule permits" not in prompt
    assert "verified information only" not in prompt
    assert "Never invent attendance" in prompt
    assert "commercial-disclosure" in prompt
    assert "CHANNEL_COMMENT" in prompt
    assert "GROUP_REPLY" in prompt
    assert "STANDALONE_POST" in prompt


def test_autoresearch_can_change_voice_without_changing_angle():
    base = GrowthVariantSpec(platform="TELEGRAM", tactic_id="comment",
                             message_angle="engage", voice_variant_id="adaptive")
    challenger = base.model_copy(update={"voice_variant_id": "playful"})
    assert base.message_angle == challenger.message_angle
    assert base.model_dump()["voice_variant_id"] == "adaptive"
    assert challenger.model_dump()["voice_variant_id"] == "playful"
    with pytest.raises(ValidationError):
        GrowthVariantSpec(platform="TELEGRAM", tactic_id="comment",
                          voice_variant_id="fabricated_identity")
