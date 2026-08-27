from uuid import uuid4

import pytest

from app.growth_autoresearch import GrowthAutoResearchService
from app.growth_autoresearch_hypothesis import (
    GrowthAutoResearchHypothesisGenerator,
    GrowthAutoResearchHypothesisService,
    GrowthHypothesisContext,
)
from app.growth_autoresearch_schemas import (
    GrowthHypothesisDraft,
    GrowthHypothesisGenerationRequest,
    GrowthHypothesisMode,
    GrowthResearchBaselineRequest,
    GrowthResearchChallengerRequest,
    GrowthResearchEvaluationRequest,
    GrowthResearchEvidence,
    GrowthResearchPolicyRequest,
    GrowthVariantSpec,
)
from app.llm import LLMMessage, LLMProvider
from app.models import ProductProfileStatus
from app.runtime_store import MemoryRuntimeStateStore
from app.schemas import ProductProfileView


class QueueProvider(LLMProvider):
    def __init__(self, drafts: list[GrowthHypothesisDraft]) -> None:
        self.drafts = list(drafts)
        self.messages: list[list[LLMMessage]] = []

    async def parse(self, messages, response_model):
        self.messages.append(messages)
        assert response_model is GrowthHypothesisDraft
        if not self.drafts:
            raise AssertionError("No queued hypothesis draft")
        return self.drafts.pop(0)


def _product(product_id) -> ProductProfileView:
    return ProductProfileView(
        id=product_id,
        input_brief="Product: Partizan\nDescription: AI growth operator for founders.",
        name="Partizan",
        description="AI growth operator for founders.",
        problem_or_desire="Founders need customers without manually managing every growth channel.",
        value_proposition="Continuously find and test customer acquisition opportunities.",
        usp="Autonomous research, execution controls, analytics and learning in one loop.",
        use_cases=["customer acquisition"],
        market="US SaaS founders",
        language="English",
        price=49,
        pricing_model="one-off plan plus growth balance",
        goal="Acquire paying customers",
        budget=1000,
        max_cac=25,
        allowed_channels=["META", "TIKTOK"],
        constraints=[],
        known_audience=["solo SaaS founders", "bootstrapped founders"],
        known_competitors=[],
        reference_links=[],
        assumptions=[],
        contradictions=[],
        status=ProductProfileStatus.CONFIRMED,
    )


def _variant(**updates) -> GrowthVariantSpec:
    values = {
        "platform": "META",
        "tactic_id": "paid-social-founders",
        "audience": "solo SaaS founders",
        "message_angle": "Save time on customer acquisition",
        "offer": "Free acquisition assessment",
        "creative_ref": "video-a",
        "cta": "Get started",
        "destination_url": "https://example.com/start",
        "targeting": "US SaaS founders",
        "timing": "always-on",
        "test_budget": 10,
    }
    values.update(updates)
    return GrowthVariantSpec(**values)


def _evidence(*, spend=200, paid=20, visits=1000, revenue=800) -> GrowthResearchEvidence:
    return GrowthResearchEvidence(
        spend=spend,
        impressions=10000,
        clicks=500,
        visits=visits,
        signups=100,
        activated_users=50,
        paid_users=paid,
        revenue=revenue,
        duration_hours=24,
    )


def _service(product_id):
    store = MemoryRuntimeStateStore()
    service = GrowthAutoResearchService(store=store)
    policy = service.configure_policy(
        product_id,
        GrowthResearchPolicyRequest(
            allowed_platforms=["META", "TIKTOK"],
            max_changed_dimensions=2,
            max_shadow_trial_budget=20,
            shadow_research_budget=100,
            max_trial_budget_share=0.25,
            confidence_level=0.90,
        ),
    )
    champion = service.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(variant=_variant(), evidence=_evidence()),
    )
    return service, store, policy, champion


def _context(
    service,
    product_id,
    policy,
    *,
    learning=(),
    remaining=100,
):
    champion = service.current_champion(product_id)
    assert champion is not None
    return GrowthHypothesisContext(
        product=_product(product_id),
        policy=policy,
        champion=champion,
        history=service.history(product_id),
        learning_summaries=tuple(learning),
        ready_plays=(),
        remaining_research_budget=remaining,
    )


@pytest.mark.asyncio
async def test_mock_fallback_generates_and_persists_explained_challenger(monkeypatch) -> None:
    product_id = uuid4()
    service, store, policy, _ = _service(product_id)
    context = _context(service, product_id, policy)
    coordinator = GrowthAutoResearchHypothesisService(
        service,
        GrowthAutoResearchHypothesisGenerator(None),
    )
    monkeypatch.setattr(coordinator, "_context", lambda _: context)

    result = await coordinator.generate(
        product_id,
        GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.AUTO),
    )

    assert result.mode == GrowthHypothesisMode.EXPLOIT
    assert result.source == "fallback"
    assert result.changed_dimensions == ["message_angle"]
    assert result.trial.hypothesis
    assert result.trial.hypothesis_rationale
    assert result.trial.hypothesis_source == "fallback"

    restarted = GrowthAutoResearchService(store=store)
    persisted = restarted.get_trial(result.trial.id)
    assert persisted.hypothesis == result.hypothesis
    assert persisted.hypothesis_mode == GrowthHypothesisMode.EXPLOIT


@pytest.mark.asyncio
async def test_policy_rejection_regenerates_llm_draft(monkeypatch) -> None:
    product_id = uuid4()
    service, _, policy, _ = _service(product_id)
    context = _context(service, product_id, policy)
    provider = QueueProvider(
        [
            GrowthHypothesisDraft(
                mode=GrowthHypothesisMode.EXPLORE,
                hypothesis="Test an unapproved channel before policy validation catches it.",
                rationale=["Explore another channel."],
                variant=_variant(platform="REDDIT", message_angle="Reddit angle"),
            ),
            GrowthHypothesisDraft(
                mode=GrowthHypothesisMode.EXPLOIT,
                hypothesis="Test a sharper outcome message while keeping acquisition mechanics fixed.",
                rationale=["Use the current winner and vary one message dimension."],
                variant=_variant(message_angle="Replace manual growth work with an AI operator"),
            ),
        ]
    )
    coordinator = GrowthAutoResearchHypothesisService(
        service,
        GrowthAutoResearchHypothesisGenerator(provider),
    )
    monkeypatch.setattr(coordinator, "_context", lambda _: context)

    result = await coordinator.generate(
        product_id,
        GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.EXPLOIT),
    )

    assert result.source == "llm"
    assert result.trial.challenger.platform == "META"
    assert len(provider.messages) == 2
    assert "Rejected by policy validator" in provider.messages[1][1].content


@pytest.mark.asyncio
async def test_failed_near_duplicate_is_suppressed_and_falls_back(monkeypatch) -> None:
    product_id = uuid4()
    service, _, policy, baseline = _service(product_id)
    losing = service.create_challenger(
        product_id,
        GrowthResearchChallengerRequest(
            variant=_variant(message_angle="Generic AI growth productivity angle")
        ),
    )
    evaluation = service.evaluate_trial(
        losing.id,
        GrowthResearchEvaluationRequest(
            evidence=_evidence(spend=400, paid=20, revenue=600),
        ),
    )
    assert evaluation.outcome.value == "FAILED"
    assert service.current_champion(product_id).id == baseline.id

    context = _context(service, product_id, policy)
    duplicate = GrowthHypothesisDraft(
        mode=GrowthHypothesisMode.EXPLOIT,
        hypothesis="Retry a cosmetic rewrite of a prior losing message.",
        rationale=["Try the old idea again."],
        variant=_variant(message_angle="Generic AI growth productivity angle!"),
    )
    provider = QueueProvider([duplicate, duplicate])
    coordinator = GrowthAutoResearchHypothesisService(
        service,
        GrowthAutoResearchHypothesisGenerator(provider),
    )
    monkeypatch.setattr(coordinator, "_context", lambda _: context)

    result = await coordinator.generate(
        product_id,
        GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.EXPLOIT),
    )

    assert result.source == "fallback"
    assert result.trial.challenger.message_angle != duplicate.variant.message_angle
    assert len(provider.messages) == 2


@pytest.mark.asyncio
async def test_learning_memory_is_present_in_llm_context(monkeypatch) -> None:
    product_id = uuid4()
    service, _, policy, _ = _service(product_id)
    context = _context(
        service,
        product_id,
        policy,
        learning=("META/paid-social-founders: winner CAC=18 below target 25.",),
    )
    provider = QueueProvider(
        [
            GrowthHypothesisDraft(
                mode=GrowthHypothesisMode.EXPLOIT,
                hypothesis="Extend the winning Meta pattern with a more specific founder outcome message.",
                rationale=["Prior learning says this platform+tactic produced good CAC."],
                variant=_variant(message_angle="Get first customers without manually running growth ops"),
            )
        ]
    )
    coordinator = GrowthAutoResearchHypothesisService(
        service,
        GrowthAutoResearchHypothesisGenerator(provider),
    )
    monkeypatch.setattr(coordinator, "_context", lambda _: context)

    await coordinator.generate(
        product_id,
        GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.EXPLOIT),
    )

    assert "winner CAC=18 below target 25" in provider.messages[0][1].content


@pytest.mark.asyncio
async def test_explicit_explore_changes_allowed_platform_in_fallback(monkeypatch) -> None:
    product_id = uuid4()
    service, _, policy, _ = _service(product_id)
    context = _context(service, product_id, policy)
    coordinator = GrowthAutoResearchHypothesisService(
        service,
        GrowthAutoResearchHypothesisGenerator(None),
    )
    monkeypatch.setattr(coordinator, "_context", lambda _: context)

    result = await coordinator.generate(
        product_id,
        GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.EXPLORE),
    )

    assert result.mode == GrowthHypothesisMode.EXPLORE
    assert result.trial.challenger.platform == "TIKTOK"
    assert result.changed_dimensions == ["platform"]


@pytest.mark.asyncio
async def test_remaining_research_budget_caps_generated_test_budget(monkeypatch) -> None:
    product_id = uuid4()
    service, _, policy, _ = _service(product_id)
    context = _context(service, product_id, policy, remaining=5)
    coordinator = GrowthAutoResearchHypothesisService(
        service,
        GrowthAutoResearchHypothesisGenerator(None),
    )
    monkeypatch.setattr(coordinator, "_context", lambda _: context)

    result = await coordinator.generate(
        product_id,
        GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.EXPLOIT),
    )

    assert result.trial.challenger.test_budget == 5
    assert set(result.changed_dimensions) == {"message_angle", "test_budget"}
