from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.growth_autoresearch_evaluator import (
    GrowthAutoResearchEvaluator,
    growth_autoresearch_evaluator,
)
from app.growth_autoresearch_schemas import (
    GrowthChampionView,
    GrowthResearchBaselineRequest,
    GrowthResearchChallengerRequest,
    GrowthResearchEvaluationRequest,
    GrowthResearchEvaluationView,
    GrowthResearchHistoryView,
    GrowthResearchOutcome,
    GrowthResearchPolicyRequest,
    GrowthResearchPolicyView,
    GrowthResearchTrialStatus,
    GrowthResearchTrialView,
    GrowthVariantSpec,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

GROWTH_AUTORESEARCH_POLICY_NAMESPACE = "growth_autoresearch_policy"
GROWTH_AUTORESEARCH_CURRENT_CHAMPION_NAMESPACE = "growth_autoresearch_current_champion"
GROWTH_AUTORESEARCH_CHAMPION_NAMESPACE = "growth_autoresearch_champion"
GROWTH_AUTORESEARCH_TRIAL_NAMESPACE = "growth_autoresearch_trial"
GROWTH_AUTORESEARCH_EVALUATION_NAMESPACE = "growth_autoresearch_evaluation"


class GrowthAutoResearchService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        evaluator: GrowthAutoResearchEvaluator | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._evaluator = evaluator or growth_autoresearch_evaluator

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    def configure_policy(
        self,
        product_id: UUID,
        payload: GrowthResearchPolicyRequest,
    ) -> GrowthResearchPolicyView:
        now = datetime.now(UTC)
        existing_payload = self._store.get(
            GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
            str(product_id),
        )
        created_at = (
            GrowthResearchPolicyView.model_validate(existing_payload).created_at
            if existing_payload is not None
            else now
        )
        allowed_platforms = sorted(
            {
                platform.strip().upper()
                for platform in payload.allowed_platforms
                if platform.strip()
            }
        )
        policy = GrowthResearchPolicyView(
            product_id=product_id,
            allowed_platforms=allowed_platforms,
            max_changed_dimensions=payload.max_changed_dimensions,
            max_shadow_trial_budget=payload.max_shadow_trial_budget,
            shadow_research_budget=payload.shadow_research_budget,
            max_trial_budget_share=payload.max_trial_budget_share,
            max_trial_duration_hours=payload.max_trial_duration_hours,
            min_paid_users_for_decision=payload.min_paid_users_for_decision,
            min_activated_users_for_decision=payload.min_activated_users_for_decision,
            min_signups_for_decision=payload.min_signups_for_decision,
            min_visits_for_proxy_decision=payload.min_visits_for_proxy_decision,
            min_relative_cac_improvement=payload.min_relative_cac_improvement,
            min_relative_proxy_improvement=payload.min_relative_proxy_improvement,
            max_relative_roas_regression=payload.max_relative_roas_regression,
            confidence_level=payload.confidence_level,
            paused=payload.paused,
            shadow_only=True,
            created_at=created_at,
            updated_at=now,
        )
        self._store.put(
            GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
            str(product_id),
            policy.model_dump(mode="json"),
        )
        return policy

    def get_policy(self, product_id: UUID) -> GrowthResearchPolicyView:
        payload = self._store.get(GROWTH_AUTORESEARCH_POLICY_NAMESPACE, str(product_id))
        if payload is None:
            raise KeyError(product_id)
        return GrowthResearchPolicyView.model_validate(payload)

    def establish_baseline(
        self,
        product_id: UUID,
        payload: GrowthResearchBaselineRequest,
    ) -> GrowthChampionView:
        policy = self.get_policy(product_id)
        if self.current_champion(product_id) is not None:
            raise ValueError("Growth AutoResearch baseline already exists for this product")
        variant = self._validated_variant(policy, payload.variant)
        champion = GrowthChampionView(
            id=uuid4(),
            product_id=product_id,
            variant=variant,
            evidence=payload.evidence,
            source_trial_id=None,
            promoted_at=datetime.now(UTC),
        )
        self._persist_champion(champion)
        return champion

    def current_champion(self, product_id: UUID) -> GrowthChampionView | None:
        payload = self._store.get(
            GROWTH_AUTORESEARCH_CURRENT_CHAMPION_NAMESPACE,
            str(product_id),
        )
        if payload is None:
            return None
        return GrowthChampionView.model_validate(payload)

    def create_challenger(
        self,
        product_id: UUID,
        payload: GrowthResearchChallengerRequest,
    ) -> GrowthResearchTrialView:
        policy = self.get_policy(product_id)
        if policy.paused:
            raise ValueError("Growth AutoResearch is paused for this product")
        champion = self.current_champion(product_id)
        if champion is None:
            raise ValueError("Establish a Growth AutoResearch baseline before creating a challenger")
        challenger = self._validated_variant(policy, payload.variant)
        changed_dimensions = self._changed_dimensions(champion.variant, challenger)
        if not changed_dimensions:
            raise ValueError("Challenger must change at least one growth dimension")
        if len(changed_dimensions) > policy.max_changed_dimensions:
            raise ValueError(
                "Challenger changes too many growth dimensions: "
                f"{len(changed_dimensions)} changed, {policy.max_changed_dimensions} allowed"
            )
        trial = GrowthResearchTrialView(
            id=uuid4(),
            product_id=product_id,
            champion_id=champion.id,
            challenger=challenger,
            changed_dimensions=changed_dimensions,
            status=GrowthResearchTrialStatus.READY,
            created_at=datetime.now(UTC),
        )
        if not self._store.put_if_absent(
            GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
            str(trial.id),
            trial.model_dump(mode="json"),
        ):
            raise RuntimeError("Growth AutoResearch trial id collision")
        return trial

    def evaluate_trial(
        self,
        trial_id: UUID,
        payload: GrowthResearchEvaluationRequest,
    ) -> GrowthResearchEvaluationView:
        trial = self.get_trial(trial_id)
        if trial.status == GrowthResearchTrialStatus.EVALUATED:
            raise ValueError("Growth AutoResearch trial has already been evaluated")
        policy = self.get_policy(trial.product_id)
        original_champion = self._champion_by_id(trial.champion_id)
        current = self.current_champion(trial.product_id)
        stale_reason = None
        if current is None or current.id != original_champion.id:
            stale_reason = (
                "The champion changed after this challenger was created; stale trials cannot "
                "replace a newer champion."
            )
        result = self._evaluator.evaluate(
            policy=policy,
            champion=original_champion.evidence,
            challenger=payload.evidence,
            planned_budget=trial.challenger.test_budget,
            blocked_reason=stale_reason or payload.blocked_reason,
            failed_reason=payload.failed_reason,
        )
        now = datetime.now(UTC)
        evaluation = GrowthResearchEvaluationView(
            id=uuid4(),
            product_id=trial.product_id,
            trial_id=trial.id,
            champion_id=original_champion.id,
            outcome=result.outcome,
            objective=result.objective,
            rationale=result.rationale,
            champion_evidence=original_champion.evidence,
            challenger_evidence=payload.evidence,
            champion_cac=result.champion_cac,
            challenger_cac=result.challenger_cac,
            champion_roas=result.champion_roas,
            challenger_roas=result.challenger_roas,
            champion_metric_value=result.champion_metric_value,
            challenger_metric_value=result.challenger_metric_value,
            relative_improvement=result.relative_improvement,
            confidence=result.confidence,
            created_at=now,
        )
        if not self._store.put_if_absent(
            GROWTH_AUTORESEARCH_EVALUATION_NAMESPACE,
            str(evaluation.id),
            evaluation.model_dump(mode="json"),
        ):
            raise RuntimeError("Growth AutoResearch evaluation id collision")
        evaluated_trial = trial.model_copy(
            update={
                "status": GrowthResearchTrialStatus.EVALUATED,
                "evaluation_id": evaluation.id,
                "evaluated_at": now,
            }
        )
        self._store.put(
            GROWTH_AUTORESEARCH_TRIAL_NAMESPACE,
            str(trial.id),
            evaluated_trial.model_dump(mode="json"),
        )
        if result.outcome == GrowthResearchOutcome.KEEP:
            promoted = GrowthChampionView(
                id=uuid4(),
                product_id=trial.product_id,
                variant=trial.challenger,
                evidence=payload.evidence,
                source_trial_id=trial.id,
                promoted_at=now,
            )
            self._persist_champion(promoted)
        return evaluation

    def get_trial(self, trial_id: UUID) -> GrowthResearchTrialView:
        payload = self._store.get(GROWTH_AUTORESEARCH_TRIAL_NAMESPACE, str(trial_id))
        if payload is None:
            raise KeyError(trial_id)
        return GrowthResearchTrialView.model_validate(payload)

    def history(self, product_id: UUID) -> GrowthResearchHistoryView:
        policy_payload = self._store.get(
            GROWTH_AUTORESEARCH_POLICY_NAMESPACE,
            str(product_id),
        )
        policy = (
            GrowthResearchPolicyView.model_validate(policy_payload)
            if policy_payload is not None
            else None
        )
        trials = [
            GrowthResearchTrialView.model_validate(payload)
            for payload in self._store.list_namespace(GROWTH_AUTORESEARCH_TRIAL_NAMESPACE)
            if str(payload.get("product_id")) == str(product_id)
        ]
        evaluations = [
            GrowthResearchEvaluationView.model_validate(payload)
            for payload in self._store.list_namespace(GROWTH_AUTORESEARCH_EVALUATION_NAMESPACE)
            if str(payload.get("product_id")) == str(product_id)
        ]
        trials.sort(key=lambda item: (item.created_at, str(item.id)))
        evaluations.sort(key=lambda item: (item.created_at, str(item.id)))
        return GrowthResearchHistoryView(
            product_id=product_id,
            policy=policy,
            champion=self.current_champion(product_id),
            trials=trials,
            evaluations=evaluations,
        )

    def _validated_variant(
        self,
        policy: GrowthResearchPolicyView,
        variant: GrowthVariantSpec,
    ) -> GrowthVariantSpec:
        normalized = variant.model_copy(
            update={
                "platform": variant.platform.strip().upper(),
                "tactic_id": variant.tactic_id.strip(),
            }
        )
        if not normalized.platform or not normalized.tactic_id:
            raise ValueError("Growth variant platform and tactic_id are required")
        if policy.allowed_platforms and normalized.platform not in policy.allowed_platforms:
            raise ValueError(
                f"Platform {normalized.platform} is outside the Growth AutoResearch policy"
            )
        if normalized.test_budget > policy.max_shadow_trial_budget:
            raise ValueError(
                "Growth variant test budget exceeds the shadow research policy: "
                f"{normalized.test_budget:.2f} > {policy.max_shadow_trial_budget:.2f}"
            )
        if policy.shadow_research_budget is not None:
            share_cap = policy.shadow_research_budget * policy.max_trial_budget_share
            if normalized.test_budget > share_cap:
                raise ValueError(
                    "Growth variant test budget exceeds the configured research-budget share: "
                    f"{normalized.test_budget:.2f} > {share_cap:.2f}"
                )
        return normalized

    @staticmethod
    def _changed_dimensions(
        champion: GrowthVariantSpec,
        challenger: GrowthVariantSpec,
    ) -> list[str]:
        champion_payload = champion.model_dump(mode="json")
        challenger_payload = challenger.model_dump(mode="json")
        return sorted(
            key
            for key in champion_payload
            if champion_payload[key] != challenger_payload[key]
        )

    def _persist_champion(self, champion: GrowthChampionView) -> None:
        payload = champion.model_dump(mode="json")
        if not self._store.put_if_absent(
            GROWTH_AUTORESEARCH_CHAMPION_NAMESPACE,
            str(champion.id),
            payload,
        ):
            raise RuntimeError("Growth AutoResearch champion id collision")
        self._store.put(
            GROWTH_AUTORESEARCH_CURRENT_CHAMPION_NAMESPACE,
            str(champion.product_id),
            payload,
        )

    def _champion_by_id(self, champion_id: UUID) -> GrowthChampionView:
        payload = self._store.get(
            GROWTH_AUTORESEARCH_CHAMPION_NAMESPACE,
            str(champion_id),
        )
        if payload is None:
            raise KeyError(champion_id)
        return GrowthChampionView.model_validate(payload)


growth_autoresearch_service = GrowthAutoResearchService()
