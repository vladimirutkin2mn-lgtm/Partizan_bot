"""Product Intake must keep category-level audience ideas as HYPOTHESES."""
from uuid import uuid4

import pytest
from pydantic import BaseModel

from app.llm import LLMProvider
from app.models import ProductProfileStatus
from app.product_agent import ProductAnalysis, ProductIntakeAgent
from app.product_intake import product_intake_service


class AudienceHypothesisProvider(LLMProvider):
    def __init__(self):
        self.calls = []

    async def parse(self, messages, response_model):
        self.calls.append(messages)
        assert response_model is ProductAnalysis
        return ProductAnalysis(
            name="FemDom",
            product_type="Telegram channel (exact public/private format unverified)",
            description="Telegram channel for people interested in FemDom",
            problem_or_desire="Interest in FemDom-related content",
            value_proposition=None,
            known_audience=["People who like FemDom"],
            customer_hypotheses=[
                "Potential segment: adult observers of female-dominant aesthetics; motivation: visual interest; discovery clues: public topic discussions; unverified: channel offers visuals.",
                "Potential segment: adult submissive-identified enthusiasts; motivation: interest in role dynamics; discovery clues: adult topic communities; unverified: channel caters to this role.",
                "Potential segment: adult newcomers to kink culture; motivation: exploration; discovery clues: introductory discussions; unverified: introductory content exists.",
                "Potential segment: adult experienced community participants; motivation: culturally authentic discussions; discovery clues: public niche groups; unverified: discussion is enabled.",
            ],
            missing_information=[
                "Actual channel posts and formats not accessible",
                "Language and audience geography unknown",
            ],
            assumptions=["The category may involve different roles and forms of engagement"],
            confidence=0.52,
        )


@pytest.mark.asyncio
async def test_prompt_requests_multiple_conditional_personas_without_claiming_product_facts():
    provider = AudienceHypothesisProvider()
    agent = ProductIntakeAgent(provider)
    analysis = await agent.analyze(
        brief="Это телеграмм канал для тех, кому нравится femdom",
        reference_links=[],
    )
    assert len(provider.calls) == 1
    prompt = provider.calls[0][0].content
    assert "3-6 MEANINGFULLY DIFFERENT hypothetical adult audience segments" in prompt
    assert "not confirmed customers" in prompt
    assert "Never treat campaign budget" in prompt
    assert "18+" in prompt
    assert "non-consensual inference" in prompt
    assert analysis.value_proposition is None
    assert analysis.known_audience == ["People who like FemDom"]
    assert len(analysis.customer_hypotheses) == 4
    assert analysis.missing_information[0].startswith("Actual channel")


@pytest.mark.asyncio
async def test_hypotheses_survive_profile_mapping_separately_from_confirmed_audience():
    provider = AudienceHypothesisProvider()
    agent = ProductIntakeAgent(provider)
    analysis = await agent.analyze(
        brief="Это телеграмм канал для тех, кому нравится femdom",
        reference_links=[],
    )
    profile = product_intake_service._build_profile(
        product_id=uuid4(),
        brief="Это телеграмм канал для тех, кому нравится femdom",
        reference_links=[],
        analysis=analysis,
        status=ProductProfileStatus.DRAFT,
    )
    assert profile.known_audience == ["People who like FemDom"]
    assert len(profile.customer_hypotheses) == 4
    assert profile.value_proposition is None
    assert profile.use_cases == []
    assert profile.missing_information == analysis.missing_information
