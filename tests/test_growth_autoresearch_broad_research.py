from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.broad_research import (
    BroadResearchEvidenceView,
    BroadResearchMapView,
    BroadResearchOpportunityView,
    ResearchExecutionStatus,
    ResearchSurface,
)
from app.growth_autoresearch import GrowthAutoResearchService
from app.growth_autoresearch_hypothesis import (
    GrowthAutoResearchHypothesisGenerator,
    GrowthHypothesisContext,
)
from app.growth_autoresearch_loop import GrowthAutoResearchLoopService
from app.growth_autoresearch_schemas import (
    GrowthAutoResearchLoopStatus,
    GrowthHypothesisDraft,
    GrowthHypothesisGenerationRequest,
    GrowthHypothesisGenerationView,
    GrowthHypothesisMode,
    GrowthResearchBaselineRequest,
    GrowthResearchChallengerRequest,
    GrowthResearchEvaluationRequest,
    GrowthResearchEvidence,
    GrowthResearchOutcome,
    GrowthResearchPolicyRequest,
    GrowthVariantSpec,
)
from app.llm import LLMProvider
from app.models import ProductProfileStatus
from app.runtime_store import MemoryRuntimeStateStore
from app.schemas import ProductProfileView


def _product(product_id: UUID) -> ProductProfileView:
    return ProductProfileView(
        id=product_id,
        input_brief="AI bookkeeping growth product for SaaS founders.",
        name="Founder Ledger",
        description="AI bookkeeping assistant for SaaS founders.",
        problem_or_desire="Founders lose time on bookkeeping and customer acquisition.",
        value_proposition="Spend less time on admin and more time winning customers.",
        usp="One workflow for bookkeeping and founder operations.",
        use_cases=["bookkeeping"],
        market="US SaaS founders",
        language="English",
        price=49,
        pricing_model="monthly",
        goal="Acquire paying customers",
        budget=1000,
        max_cac=25,
        allowed_channels=["META"],
        constraints=[],
        known_audience=["SaaS founders"],
        known_competitors=[],
        reference_links=[],
        assumptions=[],
        contradictions=[],
        status=ProductProfileStatus.CONFIRMED,
    )


def _variant(*, platform: str = "META", angle: str = "Founder bookkeeping") -> GrowthVariantSpec:
    return GrowthVariantSpec(
        platform=platform,
        tactic_id="founder-acquisition",
        audience="SaaS founders",
        message_angle=angle,
        offer="Free founder operations assessment",
        cta="Get started",
        destination_url="https://example.com/start",
        targeting="US SaaS founders",
        timing="always-on",
        test_budget=5,
    )


def _evidence() -> GrowthResearchEvidence:
    return GrowthResearchEvidence(
        spend=100,
        impressions=10000,
        clicks=500,
        visits=300,
        signups=40,
        activated_users=20,
        paid_users=10,
        revenue=500,
        duration_hours=48,
        source="measured-replay",
    )


def _broad(product_id: UUID) -> BroadResearchOpportunityView:
    return BroadResearchOpportunityView(
        id=uuid4(),
        product_id=product_id,
        icp_id=uuid4(),
        surface=ResearchSurface.SEARCH,
        kind="QUERY_CLUSTER",
        title="SaaS founder bookkeeping automation search cluster",
        url=None,
        rationale=(
            "SaaS founders are actively researching bookkeeping automation and admin reduction."
        ),
        relevance_score=94,
        execution_status=ResearchExecutionStatus.RESEARCH_ONLY,
        execution_requirement=(
            "This is Search/SEO research only and does not imply an execution integration."
        ),
        provenance=[
            BroadResearchEvidenceView(
                query="saas founder bookkeeping automation",
                title="Founder bookkeeping workflows",
                url="https://example.com/founder-bookkeeping",
                snippet="SaaS founders compare bookkeeping automation and admin workflows.",
            )
        ],
    )


class RecordingProvider(LLMProvider):
    def __init__(self, draft: GrowthHypothesisDraft) -> None:
        self.draft = draft
        self.messages = []

    async def parse(self, messages, response_model):
        self.messages.append(messages)
        assert response_model is GrowthHypothesisDraft
        return self.draft


@pytest.mark.asyncio
async def test_hypothesis_context_includes_broad_research_evidence_without_platform_leakage(
) -> None:
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    autoresearch = GrowthAutoResearchService(store=store)
    policy = autoresearch.configure_policy(
        product_id,
        GrowthResearchPolicyRequest(
            allowed_platforms=["META"],
            max_shadow_trial_budget=10,
            shadow_research_budget=50,
            max_trial_budget_share=1,
        ),
    )
    champion = autoresearch.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(variant=_variant(), evidence=_evidence()),
    )
    provider = RecordingProvider(
        GrowthHypothesisDraft(
            mode=GrowthHypothesisMode.EXPLOIT,
            hypothesis="Test a bookkeeping automation message for the existing Meta audience.",
            rationale=["Broad search evidence shows founder interest in bookkeeping automation."],
            variant=_variant(angle="Automate founder bookkeeping admin"),
        )
    )
    context = GrowthHypothesisContext(
        product=_product(product_id),
        policy=policy,
        champion=champion,
        history=autoresearch.history(product_id),
        learning_summaries=(),
        ready_plays=(),
        remaining_research_budget=50,
        broad_research=(_broad(product_id),),
    )

    result = await GrowthAutoResearchHypothesisGenerator(provider).generate(
        context,
        GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.EXPLOIT),
    )

    assert result.draft.variant.platform == "META"
    payload = provider.messages[0][1].content
    assert '"surface": "SEARCH"' in payload
    assert '"execution_status": "RESEARCH_ONLY"' in payload
    assert "saas founder bookkeeping automation" in payload
    assert "never grants execution-platform access" in payload


def test_research_surface_cannot_become_variant_platform_even_if_policy_lists_it() -> None:
    product_id = uuid4()
    autoresearch = GrowthAutoResearchService(store=MemoryRuntimeStateStore())
    autoresearch.configure_policy(
        product_id,
        GrowthResearchPolicyRequest(
            allowed_platforms=["META", "CREATOR"],
            max_shadow_trial_budget=10,
        ),
    )
    autoresearch.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(variant=_variant(), evidence=_evidence()),
    )

    with pytest.raises(ValueError, match="Research surface CREATOR"):
        autoresearch.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(
                variant=_variant(platform="CREATOR", angle="Creator-led founder message")
            ),
        )


class FakeHypothesisService:
    def __init__(self, autoresearch: GrowthAutoResearchService) -> None:
        self.autoresearch = autoresearch

    async def generate(self, product_id, request):
        assert request.mode == GrowthHypothesisMode.AUTO
        hypothesis = "Test SaaS founder bookkeeping automation demand on the existing Meta path."
        trial = self.autoresearch.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(
                variant=_variant(angle="SaaS founder bookkeeping automation"),
                hypothesis=hypothesis,
                hypothesis_rationale=[
                    "Founder bookkeeping research supports testing this message angle."
                ],
                hypothesis_mode=GrowthHypothesisMode.EXPLOIT,
                hypothesis_source="test",
            ),
        )
        annotated = trial.model_copy(
            update={
                "hypothesis": hypothesis,
                "hypothesis_rationale": [
                    "Founder bookkeeping research supports testing this message angle."
                ],
                "hypothesis_mode": GrowthHypothesisMode.EXPLOIT,
                "hypothesis_source": "test",
            }
        )
        self.autoresearch.store.put(
            "growth_autoresearch_trial",
            str(annotated.id),
            annotated.model_dump(mode="json"),
        )
        return GrowthHypothesisGenerationView(
            product_id=product_id,
            mode=GrowthHypothesisMode.EXPLOIT,
            hypothesis=hypothesis,
            rationale=annotated.hypothesis_rationale,
            changed_dimensions=annotated.changed_dimensions,
            source="test",
            remaining_research_budget=50,
            trial=annotated,
        )


class EmptyAudienceService:
    def get(self, product_id):
        del product_id
        raise KeyError("no execution-domain research")


class FakeBroadResearchService:
    def __init__(self, product_id: UUID) -> None:
        opportunity = _broad(product_id)
        self.map = BroadResearchMapView(
            product_id=product_id,
            icp_count=1,
            opportunity_count=1,
            opportunities=[opportunity],
        )

    def get(self, product_id):
        assert product_id == self.map.product_id
        return self.map


@pytest.mark.asyncio
async def test_loop_persists_broad_provenance_without_fabricating_business_evidence() -> None:
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    autoresearch = GrowthAutoResearchService(store=store)
    autoresearch.configure_policy(
        product_id,
        GrowthResearchPolicyRequest(
            allowed_platforms=["META"],
            max_shadow_trial_budget=10,
            shadow_research_budget=50,
            max_trial_budget_share=1,
        ),
    )
    baseline = autoresearch.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(variant=_variant(), evidence=_evidence()),
    )
    broad = FakeBroadResearchService(product_id)
    loop = GrowthAutoResearchLoopService(
        store=store,
        autoresearch=autoresearch,
        hypotheses=FakeHypothesisService(autoresearch),
        audience_service=EmptyAudienceService(),
        broad_research=broad,
    )

    sweep = await loop.sweep_product(product_id)

    assert sweep.status == GrowthAutoResearchLoopStatus.GENERATED
    assert sweep.provenance_count == 1
    trial = autoresearch.get_trial(sweep.trial_id)
    assert trial.challenger.platform == "META"
    assert trial.research_provenance[0].source_domain == "BROAD_RESEARCH"
    assert trial.research_provenance[0].surface == "SEARCH"
    assert trial.research_provenance[0].platform is None
    assert trial.research_provenance[0].execution_status == "RESEARCH_ONLY"
    assert trial.research_provenance[0].evidence_queries == [
        "saas founder bookkeeping automation"
    ]
    assert autoresearch.history(product_id).evaluations == []
    assert autoresearch.current_champion(product_id).id == baseline.id

    restarted = GrowthAutoResearchService(store=store)
    persisted = restarted.get_trial(trial.id)
    assert persisted.research_provenance == trial.research_provenance

    evaluation = restarted.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=GrowthResearchEvidence(source="broad-research-context-only")
        ),
    )
    assert evaluation.outcome != GrowthResearchOutcome.KEEP
    assert restarted.current_champion(product_id).id == baseline.id
