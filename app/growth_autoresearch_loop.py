from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.audience_intelligence_service import (
    InMemoryAudienceIntelligenceService,
    audience_intelligence_service,
)
from app.growth_autoresearch import (
    GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
    GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
    GrowthAutoResearchService,
    growth_autoresearch_service,
)
from app.growth_autoresearch_hypothesis import (
    GrowthAutoResearchHypothesisService,
    growth_autoresearch_hypothesis_service,
)
from app.growth_autoresearch_schemas import (
    GrowthAutoResearchLoopStatus,
    GrowthAutoResearchOverviewView,
    GrowthAutoResearchRunView,
    GrowthAutoResearchSweepView,
    GrowthHypothesisGenerationRequest,
    GrowthHypothesisMode,
    GrowthResearchHistoryView,
    GrowthResearchPolicyRequest,
    GrowthResearchPolicyView,
    GrowthResearchProvenanceView,
    GrowthResearchTrialStatus,
    GrowthResearchTrialView,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

GROWTH_AUTORESEARCH_SWEEP_NAMESPACE = "growth_autoresearch_sweep"
GROWTH_AUTORESEARCH_LATEST_SWEEP_NAMESPACE = "growth_autoresearch_latest_sweep"
GROWTH_AUTORESEARCH_RUN_NAMESPACE = "growth_autoresearch_run"


class GrowthAutoResearchLoopService:
    """Research-only continuous loop for bounded growth hypotheses.

    The loop is intentionally incapable of generating business evidence. Public research
    can explain why a challenger is worth testing, but only the Phase 2 evaluator can
    turn measured/replay evidence into a champion decision.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        autoresearch: GrowthAutoResearchService | None = None,
        hypotheses: GrowthAutoResearchHypothesisService | None = None,
        audience_service: InMemoryAudienceIntelligenceService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._autoresearch = autoresearch or growth_autoresearch_service
        self._hypotheses = hypotheses or growth_autoresearch_hypothesis_service
        self._audience_service = audience_service or audience_intelligence_service

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    async def run_once(self, *, product_id: UUID | None = None) -> GrowthAutoResearchRunView:
        product_ids = [product_id] if product_id is not None else self._configured_product_ids()
        sweeps = [await self.sweep_product(item) for item in product_ids]
        result = GrowthAutoResearchRunView(
            id=uuid4(),
            product_count=len(sweeps),
            generated_count=sum(
                item.status == GrowthAutoResearchLoopStatus.GENERATED for item in sweeps
            ),
            waiting_count=sum(
                item.status == GrowthAutoResearchLoopStatus.WAITING_EVIDENCE
                for item in sweeps
            ),
            sweeps=sweeps,
            created_at=datetime.now(UTC),
        )
        self._store.put(
            GROWTH_AUTORESEARCH_RUN_NAMESPACE,
            str(result.id),
            result.model_dump(mode="json"),
        )
        return result

    async def sweep_product(self, product_id: UUID) -> GrowthAutoResearchSweepView:
        try:
            policy = self._autoresearch.get_policy(product_id)
        except KeyError:
            return self._record_sweep(
                product_id,
                GrowthAutoResearchLoopStatus.NOT_CONFIGURED,
                "Growth AutoResearch policy is not configured for this product.",
            )

        history = self._autoresearch.history(product_id)
        remaining = self._remaining_budget(policy, history)
        if policy.paused:
            return self._record_sweep(
                product_id,
                GrowthAutoResearchLoopStatus.PAUSED,
                "Growth AutoResearch is paused for this product.",
                remaining_research_budget=remaining,
            )
        if history.champion is None:
            return self._record_sweep(
                product_id,
                GrowthAutoResearchLoopStatus.NO_BASELINE,
                "Establish a measured/replay baseline before continuous research can start.",
                remaining_research_budget=remaining,
            )

        ready_trials = [
            trial
            for trial in history.trials
            if trial.status == GrowthResearchTrialStatus.READY
        ]
        if ready_trials:
            active = ready_trials[-1]
            return self._record_sweep(
                product_id,
                GrowthAutoResearchLoopStatus.WAITING_EVIDENCE,
                self._waiting_message(len(ready_trials)),
                trial_id=active.id,
                provenance_count=len(active.research_provenance),
                remaining_research_budget=remaining,
            )

        if remaining is not None and remaining <= 0:
            return self._record_sweep(
                product_id,
                GrowthAutoResearchLoopStatus.BUDGET_EXHAUSTED,
                "The configured shadow research budget is exhausted.",
                remaining_research_budget=0,
            )

        generated = await self._hypotheses.generate(
            product_id,
            GrowthHypothesisGenerationRequest(mode=GrowthHypothesisMode.AUTO),
        )
        provenance = self._provenance(product_id, generated.trial)
        annotated = generated.trial.model_copy(update={"research_provenance": provenance})
        self._store.put(
            GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
            str(annotated.id),
            annotated.model_dump(mode="json"),
        )
        refreshed = self._autoresearch.history(product_id)
        return self._record_sweep(
            product_id,
            GrowthAutoResearchLoopStatus.GENERATED,
            "Generated one bounded research-only challenger and stopped for evidence.",
            trial_id=annotated.id,
            provenance_count=len(provenance),
            remaining_research_budget=self._remaining_budget(policy, refreshed),
        )

    def overview(self, product_id: UUID) -> GrowthAutoResearchOverviewView:
        history = self._autoresearch.history(product_id)
        policy = history.policy
        last_sweep = self._latest_sweep(product_id)
        if policy is None:
            return GrowthAutoResearchOverviewView(
                product_id=product_id,
                configured=False,
                paused=False,
                status=GrowthAutoResearchLoopStatus.NOT_CONFIGURED,
                champion=history.champion,
                recent_trials=list(reversed(history.trials[-8:])),
                recent_evaluations=list(reversed(history.evaluations[-8:])),
                last_sweep=last_sweep,
            )

        ready_trials = [
            trial
            for trial in history.trials
            if trial.status == GrowthResearchTrialStatus.READY
        ]
        active = ready_trials[-1] if ready_trials else None
        remaining = self._remaining_budget(policy, history)
        status = self._current_status(policy, history, active, remaining)
        provenance = (
            active.research_provenance
            if active is not None and active.research_provenance
            else self._provenance(product_id, active)
        )
        return GrowthAutoResearchOverviewView(
            product_id=product_id,
            configured=True,
            paused=policy.paused,
            status=status,
            remaining_research_budget=remaining,
            champion=history.champion,
            active_trial=active,
            recent_trials=list(reversed(history.trials[-8:])),
            recent_evaluations=list(reversed(history.evaluations[-8:])),
            provenance=provenance,
            last_sweep=last_sweep,
        )

    def set_paused(self, product_id: UUID, *, paused: bool) -> GrowthAutoResearchOverviewView:
        policy = self._autoresearch.get_policy(product_id)
        self._autoresearch.configure_policy(
            product_id,
            GrowthResearchPolicyRequest(
                allowed_platforms=policy.allowed_platforms,
                max_changed_dimensions=policy.max_changed_dimensions,
                max_shadow_trial_budget=policy.max_shadow_trial_budget,
                shadow_research_budget=policy.shadow_research_budget,
                max_trial_budget_share=policy.max_trial_budget_share,
                max_trial_duration_hours=policy.max_trial_duration_hours,
                min_paid_users_for_decision=policy.min_paid_users_for_decision,
                min_activated_users_for_decision=policy.min_activated_users_for_decision,
                min_signups_for_decision=policy.min_signups_for_decision,
                min_visits_for_proxy_decision=policy.min_visits_for_proxy_decision,
                min_relative_cac_improvement=policy.min_relative_cac_improvement,
                min_relative_proxy_improvement=policy.min_relative_proxy_improvement,
                max_relative_roas_regression=policy.max_relative_roas_regression,
                confidence_level=policy.confidence_level,
                paused=paused,
            ),
        )
        return self.overview(product_id)

    def recent_runs(self, limit: int = 20) -> list[GrowthAutoResearchRunView]:
        rows = [
            GrowthAutoResearchRunView.model_validate(payload)
            for payload in self._store.list_namespace(GROWTH_AUTORESEARCH_RUN_NAMESPACE)
        ]
        rows.sort(key=lambda item: (item.created_at, str(item.id)), reverse=True)
        return rows[: max(0, limit)]

    def reset(self) -> None:
        if not self._store.ephemeral:
            return
        self._store.clear_namespace(GROWTH_AUTORESEARCH_SWEEP_NAMESPACE)
        self._store.clear_namespace(GROWTH_AUTORESEARCH_LATEST_SWEEP_NAMESPACE)
        self._store.clear_namespace(GROWTH_AUTORESEARCH_RUN_NAMESPACE)

    def _configured_product_ids(self) -> list[UUID]:
        values = {
            UUID(str(payload["product_id"]))
            for payload in self._store.list_namespace(GROWTH_AUTORESEARCH_POLICY_NAMESPACE)
            if payload.get("product_id")
        }
        return sorted(values, key=str)

    def _record_sweep(
        self,
        product_id: UUID,
        status: GrowthAutoResearchLoopStatus,
        message: str,
        *,
        trial_id: UUID | None = None,
        provenance_count: int = 0,
        remaining_research_budget: float | None = None,
    ) -> GrowthAutoResearchSweepView:
        sweep = GrowthAutoResearchSweepView(
            id=uuid4(),
            product_id=product_id,
            status=status,
            message=message,
            trial_id=trial_id,
            provenance_count=provenance_count,
            remaining_research_budget=remaining_research_budget,
            created_at=datetime.now(UTC),
        )
        payload = sweep.model_dump(mode="json")
        self._store.put(GROWTH_AUTORESEARCH_SWEEP_NAMESPACE, str(sweep.id), payload)
        self._store.put(
            GROWTH_AUTORESEARCH_LATEST_SWEEP_NAMESPACE,
            str(product_id),
            payload,
        )
        return sweep

    def _latest_sweep(self, product_id: UUID) -> GrowthAutoResearchSweepView | None:
        payload = self._store.get(
            GROWTH_AUTORESEARCH_LATEST_SWEEP_NAMESPACE,
            str(product_id),
        )
        return GrowthAutoResearchSweepView.model_validate(payload) if payload else None

    def _provenance(
        self,
        product_id: UUID,
        trial: GrowthResearchTrialView | None,
    ) -> list[GrowthResearchProvenanceView]:
        try:
            distribution = self._audience_service.get(product_id)
        except KeyError:
            return []
        platform = trial.challenger.platform if trial is not None else None
        candidates = [
            item
            for item in distribution.opportunities
            if platform is None or item.platform.value == platform
        ]
        candidates.sort(
            key=lambda item: (-(item.relevance_score or 0), item.canonical_key)
        )
        result: list[GrowthResearchProvenanceView] = []
        for opportunity in candidates[:5]:
            source_urls: list[str] = []
            signal_tags: list[str] = []
            for evidence in opportunity.evidence:
                source_url = str(evidence.get("url") or "").strip()
                if source_url and source_url not in source_urls:
                    source_urls.append(source_url)
                for tag in evidence.get("signal_tags") or []:
                    tag_text = str(tag).strip()
                    if tag_text and tag_text not in signal_tags:
                        signal_tags.append(tag_text)
            result.append(
                GrowthResearchProvenanceView(
                    platform=opportunity.platform.value,
                    title=opportunity.title,
                    url=str(opportunity.url) if opportunity.url else None,
                    rationale=opportunity.rationale,
                    relevance_score=opportunity.relevance_score,
                    source_urls=source_urls[:20],
                    signal_tags=signal_tags[:40],
                )
            )
        return result

    @staticmethod
    def _waiting_message(ready_count: int) -> str:
        if ready_count == 1:
            return "One challenger is ready; waiting for measured/replay business evidence."
        return (
            f"{ready_count} unevaluated READY trials exist. The continuous loop will not "
            "generate another challenger until they are resolved."
        )

    @staticmethod
    def _remaining_budget(
        policy: GrowthResearchPolicyView,
        history: GrowthResearchHistoryView,
    ) -> float | None:
        if policy.shadow_research_budget is None:
            return None
        committed = sum(trial.challenger.test_budget for trial in history.trials)
        return round(max(0.0, policy.shadow_research_budget - committed), 2)

    @staticmethod
    def _current_status(
        policy: GrowthResearchPolicyView,
        history: GrowthResearchHistoryView,
        active: GrowthResearchTrialView | None,
        remaining: float | None,
    ) -> GrowthAutoResearchLoopStatus:
        if policy.paused:
            return GrowthAutoResearchLoopStatus.PAUSED
        if history.champion is None:
            return GrowthAutoResearchLoopStatus.NO_BASELINE
        if active is not None:
            return GrowthAutoResearchLoopStatus.WAITING_EVIDENCE
        if remaining is not None and remaining <= 0:
            return GrowthAutoResearchLoopStatus.BUDGET_EXHAUSTED
        return GrowthAutoResearchLoopStatus.IDLE


growth_autoresearch_loop_service = GrowthAutoResearchLoopService()
