"""Campaign asset metadata must not masquerade as product understanding."""
import pytest

from app.product_agent import ProductIntakeAgent
from app.product_source_quality import (
    is_campaign_asset_brief,
    is_distinct_product_value,
    needs_product_value_clarification,
)


CAMPAIGN_BRIEF = (
    "Promoted asset: FemDom\n"
    "Asset type: Telegram channel\n"
    "Description: This Telegram channel is for people who like FemDom.\n"
    "Market: RU\n"
    "Goal: Acquire users\n"
    "Test budget: $10"
)


@pytest.mark.asyncio
async def test_promoted_asset_never_becomes_customer_value_or_invented_audience():
    analysis = await ProductIntakeAgent(None).analyze(
        brief=CAMPAIGN_BRIEF, reference_links=[],
    )
    assert analysis.name == "FemDom"
    assert analysis.description == "This Telegram channel is for people who like FemDom."
    assert analysis.value_proposition is None
    assert analysis.known_audience == []
    fields = {q.field_name for q in analysis.clarifications}
    assert "value_proposition" in fields
    assert "known_audience" in fields
    assert len(analysis.clarifications) <= 3


@pytest.mark.asyncio
async def test_campaign_founder_clarifications_survive_fallback_without_assumptions():
    agent = ProductIntakeAgent(None)
    answers = [
        ("value_proposition", "What do users receive?", "Subscribers get original event guides."),
        ("known_audience", "Who benefits?", "Adults looking for themed event announcements."),
    ]
    result = await agent.analyze(
        brief=CAMPAIGN_BRIEF, reference_links=[],
        answers=answers, answered_fields={"value_proposition", "known_audience"},
    )
    assert result.value_proposition == "Subscribers get original event guides."
    assert result.known_audience == ["Adults looking for themed event announcements."]
    assert not any(
        q.field_name in {"value_proposition", "known_audience"}
        for q in result.clarifications
    )


@pytest.mark.asyncio
async def test_normal_product_description_does_not_gain_campaign_questions():
    description = "LedgerFox automates bookkeeping and tax prep for freelancers."
    analysis = await ProductIntakeAgent(None).analyze(
        brief=(
            "Product: LedgerFox\n"
            f"Description: {description}\n"
            "Value proposition: Organizes freelancer receipts automatically.\n"
            "Audience: Freelancers\n"
            "Market: US\n"
            "Goal: Get first customers"
        ),
        reference_links=[],
    )
    assert analysis.value_proposition == "Organizes freelancer receipts automatically."
    assert analysis.known_audience == ["Freelancers"]
    assert not any(
        q.field_name in {"value_proposition", "known_audience"}
        for q in analysis.clarifications
    )


def test_campaign_quality_classification_is_conservative():
    assert is_campaign_asset_brief(CAMPAIGN_BRIEF)
    assert not is_campaign_asset_brief("A mobile app for keeping personal expenses.")
    assert not is_distinct_product_value("Some description", "Some description")
    assert is_distinct_product_value(
        "A Telegram channel for hobbyists.",
        "Subscribers get verified guides to local hobby events.",
    )
    assert needs_product_value_clarification(
        description=CAMPAIGN_BRIEF,
        value_proposition=CAMPAIGN_BRIEF,
        problem_or_desire=None,
        source_brief=CAMPAIGN_BRIEF,
    )
