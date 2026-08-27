from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.growth_autoresearch import GrowthAutoResearchService
from app.growth_autoresearch_loop import GrowthAutoResearchLoopService
from app.growth_autoresearch_schemas import (
    GrowthAutoResearchLoopStatus,
    GrowthHypothesisGenerationView,
    GrowthHypothesisMode,
    GrowthResearchBaselineRequest,
    GrowthResearchChallengerRequest,
    GrowthResearchEvaluationRequest,
    GrowthResearchEvidence,
    GrowthResearchPolicyRequest,
    GrowthVariantSpec,
)
from app.runtime_store import MemoryRuntimeStateStore


def _variant(*, angle: str = "Champion message", budget: float = 5) -> GrowthVariantSpec:
    return GrowthVariantSpec(
        platform="META",
        tactic_id="paid-social-founders",
        audience="SaaS founders",
        message_angle=angle,
        offer="Free acquisition assessment",
        cta="Get started",
        destination_url="https://example.com/start",
        targeting="US SaaS founders",
        timing="always-on",
        test_budget=budget,
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


class FakeHypothesisService:
    def __init__(self, autoresearch: GrowthAutoResearchService) -> None:
        self.autoresearch = autoresearch
        self.calls = 0

    async def generate(self, product_id, request):
        self.calls += 1
        assert request.mode == GrowthHypothesisMode.AUTO
        hypothesis = f"Test bounded message challenger number {self.calls}."
        trial = self.autoresearch.create_challenger(
            product_id,
            GrowthResearchChallengerRequest(
                variant=_variant(angle=f"Challenger message {self.calls}"),
                hypothesis=hypothesis,
                hypothesis_rationale=["A bounded one-dimension shadow comparison."],
                hypothesis_mode=GrowthHypothesisMode.EXPLOIT,
                hypothesis_source="test",
            ),
        )
        annotated = trial.model_copy(
            update={
                "hypothesis": hypothesis,
                "hypothesis_rationale": ["A bounded one-dimension shadow comparison."],
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
            rationale=["A bounded one-dimension shadow comparison."],
            changed_dimensions=annotated.changed_dimensions,
            source="test",
            remaining_research_budget=20,
            trial=annotated,
        )


class FakeAudienceService:
    def get(self, product_id):
        del product_id
        opportunity = SimpleNamespace(
            platform=SimpleNamespace(value="META"),
            canonical_key="meta:founder-demand",
            title="Founder acquisition discussion",
            url="https://example.com/research",
            rationale="Multiple public sources show founder demand for acquisition automation.",
            relevance_score=88.0,
            evidence=[
                {
                    "url": "https://example.com/source-1",
                    "signal_tags": ["demand_intent", "pain"],
                },
                {
                    "url": "https://example.com/source-2",
                    "signal_tags": ["demand_intent"],
                },
            ],
        )
        return SimpleNamespace(opportunities=[opportunity])


def _configured_service(*, research_budget: float = 20):
    product_id = uuid4()
    store = MemoryRuntimeStateStore()
    autoresearch = GrowthAutoResearchService(store=store)
    autoresearch.configure_policy(
        product_id,
        GrowthResearchPolicyRequest(
            allowed_platforms=["META"],
            max_changed_dimensions=2,
            max_shadow_trial_budget=5,
            shadow_research_budget=research_budget,
            max_trial_budget_share=1,
        ),
    )
    baseline = autoresearch.establish_baseline(
        product_id,
        GrowthResearchBaselineRequest(variant=_variant(budget=0), evidence=_evidence()),
    )
    hypotheses = FakeHypothesisService(autoresearch)
    loop = GrowthAutoResearchLoopService(
        store=store,
        autoresearch=autoresearch,
        hypotheses=hypotheses,
        audience_service=FakeAudienceService(),
    )
    return product_id, store, autoresearch, hypotheses, loop, baseline


@pytest.mark.asyncio
async def test_loop_generates_one_trial_then_waits_and_survives_restart() -> None:
    product_id, store, autoresearch, hypotheses, loop, baseline = _configured_service()

    first = await loop.sweep_product(product_id)
    assert first.status == GrowthAutoResearchLoopStatus.GENERATED
    assert first.provenance_count == 1
    assert hypotheses.calls == 1

    trial = autoresearch.get_trial(first.trial_id)
    assert len(trial.research_provenance) == 1
    assert trial.research_provenance[0].source_urls == [
        "https://example.com/source-1",
        "https://example.com/source-2",
    ]
    assert autoresearch.current_champion(product_id).id == baseline.id

    second = await loop.sweep_product(product_id)
    assert second.status == GrowthAutoResearchLoopStatus.WAITING_EVIDENCE
    assert second.trial_id == trial.id
    assert hypotheses.calls == 1
    ready = [item for item in autoresearch.history(product_id).trials if item.status == "READY"]
    assert len(ready) == 1

    restarted = GrowthAutoResearchLoopService(
        store=store,
        autoresearch=GrowthAutoResearchService(store=store),
        hypotheses=hypotheses,
        audience_service=FakeAudienceService(),
    )
    overview = restarted.overview(product_id)
    assert overview.status == GrowthAutoResearchLoopStatus.WAITING_EVIDENCE
    assert overview.active_trial.id == trial.id
    assert overview.last_sweep.id == second.id


@pytest.mark.asyncio
async def test_evaluated_trial_allows_next_hypothesis_without_fabricated_evidence() -> None:
    product_id, _, autoresearch, hypotheses, loop, baseline = _configured_service()
    first = await loop.sweep_product(product_id)
    trial = autoresearch.get_trial(first.trial_id)

    evaluation = autoresearch.evaluate_trial(
        trial.id,
        GrowthResearchEvaluationRequest(
            evidence=GrowthResearchEvidence(source="replay-unavailable"),
            blocked_reason="No measured/replay business evidence is available yet.",
        ),
    )
    assert evaluation.outcome == "BLOCKED"
    assert autoresearch.current_champion(product_id).id == baseline.id

    next_sweep = await loop.sweep_product(product_id)
    assert next_sweep.status == GrowthAutoResearchLoopStatus.GENERATED
    assert hypotheses.calls == 2
    assert next_sweep.trial_id != trial.id


@pytest.mark.asyncio
async def test_paused_no_baseline_and_exhausted_states_are_explicit() -> None:
    product_id, store, autoresearch, hypotheses, loop, _ = _configured_service(
        research_budget=5
    )
    policy = autoresearch.get_policy(product_id)
    autoresearch.configure_policy(
        product_id,
        GrowthResearchPolicyRequest(
            **policy.model_dump(
                exclude={"product_id", "shadow_only", "created_at", "updated_at", "paused"}
            ),
            paused=True,
        ),
    )
    paused = await loop.sweep_product(product_id)
    assert paused.status == GrowthAutoResearchLoopStatus.PAUSED
    assert hypotheses.calls == 0

    loop.set_paused(product_id, paused=False)
    generated = await loop.sweep_product(product_id)
    autoresearch.evaluate_trial(
        generated.trial_id,
        GrowthResearchEvaluationRequest(
            evidence=GrowthResearchEvidence(source="blocked-replay"),
            blocked_reason="No downstream replay evidence.",
        ),
    )
    exhausted = await loop.sweep_product(product_id)
    assert exhausted.status == GrowthAutoResearchLoopStatus.BUDGET_EXHAUSTED

    other_product = uuid4()
    other = GrowthAutoResearchService(store=store)
    other.configure_policy(
        other_product,
        GrowthResearchPolicyRequest(
            allowed_platforms=["META"],
            max_shadow_trial_budget=5,
            shadow_research_budget=10,
            max_trial_budget_share=1,
        ),
    )
    no_baseline_loop = GrowthAutoResearchLoopService(
        store=store,
        autoresearch=other,
        hypotheses=hypotheses,
        audience_service=FakeAudienceService(),
    )
    no_baseline = await no_baseline_loop.sweep_product(other_product)
    assert no_baseline.status == GrowthAutoResearchLoopStatus.NO_BASELINE
