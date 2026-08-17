import json
from datetime import UTC, datetime
from hashlib import sha1
from uuid import UUID, uuid4

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_analytics_schemas import (
    DistributionGrowthDecisionView,
    DistributionLearningEntryView,
    DistributionLearningMemoryView,
    DistributionPortfolioItemView,
    DistributionPortfolioView,
)
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_execution_schemas import DistributionExperimentStatus
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_schemas import DistributionPlayStatus, DistributionPlayView
from app.distribution_play_service import distribution_play_service
from app.growth_planning import growth_planning_engine
from app.product_intake import product_intake_service
from app.runtime_store import RuntimeStateStore, get_runtime_store

MIN_SCALE_PAID_USERS = 3
DISTRIBUTION_DECISION_NAMESPACE = "distribution_growth_decision"
DISTRIBUTION_DECISION_STATE_NAMESPACE = "distribution_growth_decision_state"
DISTRIBUTION_LEARNING_NAMESPACE = "distribution_learning_entry"


class InMemoryDistributionGrowthManagerService:
    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()
        self._decisions: dict[UUID, DistributionGrowthDecisionView] = {}
        self._latest_fingerprint: dict[UUID, str] = {}
        self._latest_decision: dict[UUID, UUID] = {}
        self._memory: dict[UUID, list[DistributionLearningEntryView]] = {}

    def evaluate(self, experiment_id: UUID) -> DistributionGrowthDecisionView:
        experiment = distribution_execution_service.get_experiment(experiment_id)
        if experiment.status not in {
            DistributionExperimentStatus.RUNNING,
            DistributionExperimentStatus.FINISHED,
        }:
            raise ValueError("Growth Manager requires a RUNNING or FINISHED experiment")

        product = product_intake_service.get_product(experiment.product_id)
        analytics = distribution_analytics_service.experiment_analytics(experiment_id)
        product_analytics = distribution_analytics_service.product_analytics(product.id)
        play = analytics.play
        fingerprint = self._fingerprint(
            analytics.model_dump(mode="json"),
            product_analytics.model_dump(mode="json"),
            product.budget,
            product.max_cac,
        )
        self._hydrate_decision_state(experiment_id)
        if self._latest_fingerprint.get(experiment_id) == fingerprint:
            decision_id = self._latest_decision[experiment_id]
            decision = self._get_decision(decision_id)
            return decision.model_copy(update={"duplicate": True})

        action, rationale = self._decide(product.max_cac, play, analytics.metrics)
        budget_remaining = self._budget_remaining(
            product.budget,
            product_analytics.total_spend,
        )
        if budget_remaining is not None and budget_remaining <= 0 and analytics.metrics.spend > 0:
            action = "STOP"
            rationale = [
                "Product marketing budget is exhausted; no additional spend is allowed.",
                self._metric_summary(analytics.metrics, product.max_cac),
            ]
        increment = self._recommended_increment(
            action,
            play,
            analytics.metrics.spend,
            budget_remaining,
        )
        now = datetime.now(UTC)
        decision = DistributionGrowthDecisionView(
            id=uuid4(),
            product_id=product.id,
            experiment_id=experiment.id,
            action=action,
            rationale=rationale,
            metrics=analytics.metrics,
            platform=play.platform,
            tactic_id=play.tactic_id,
            opportunity_id=play.opportunity_id,
            distribution_identity_id=analytics.action.distribution_identity_id,
            budget_remaining=budget_remaining,
            recommended_budget_increment=increment,
            created_at=now,
        )
        self._decisions[decision.id] = decision
        self._latest_fingerprint[experiment_id] = fingerprint
        self._latest_decision[experiment_id] = decision.id
        learning_entry = DistributionLearningEntryView(
            id=uuid4(),
            product_id=product.id,
            experiment_id=experiment.id,
            platform=play.platform,
            tactic_id=play.tactic_id,
            opportunity_id=play.opportunity_id,
            distribution_identity_id=analytics.action.distribution_identity_id,
            action=action,
            observed_cac=analytics.metrics.cac,
            paid_users=analytics.metrics.paid_users,
            revenue=analytics.metrics.revenue,
            summary=(
                f"{play.platform.value}/{play.tactic_id}: action={action}; "
                f"spend={analytics.metrics.spend:.2f}; "
                f"paid={analytics.metrics.paid_users}; "
                f"CAC={analytics.metrics.cac}; "
                f"revenue={analytics.metrics.revenue:.2f}."
            ),
            created_at=now,
        )
        self._memory.setdefault(product.id, []).append(learning_entry)
        self._persist_decision(decision, fingerprint)
        self._store.put(
            DISTRIBUTION_LEARNING_NAMESPACE,
            str(learning_entry.id),
            learning_entry.model_dump(mode="json"),
        )
        return decision

    def learning_memory(self, product_id: UUID) -> DistributionLearningMemoryView:
        product_intake_service.get_product(product_id)
        self._hydrate_learning()
        return DistributionLearningMemoryView(
            product_id=product_id,
            entries=list(self._memory.get(product_id, [])),
        )

    def portfolio(
        self,
        product_id: UUID,
        *,
        max_items: int = 4,
    ) -> DistributionPortfolioView:
        product = product_intake_service.get_product(product_id)
        play_result = distribution_play_service.get(product_id)
        product_analytics = distribution_analytics_service.product_analytics(product_id)
        budget_remaining = self._budget_remaining(
            product.budget,
            product_analytics.total_spend,
        )
        ready = [
            play
            for play in play_result.plays
            if play.status == DistributionPlayStatus.READY
        ]
        running_play_ids = {
            experiment.distribution_play_id
            for experiment in distribution_execution_service.list_experiments(product_id)
            if experiment.status == DistributionExperimentStatus.RUNNING
        }
        ready = [play for play in ready if play.id not in running_play_ids]

        scored: list[tuple[float, DistributionPlayView, list[str]]] = []
        for play in ready:
            learning_adjustment, learning_reason = self._learning_adjustment(
                play,
                product.max_cac,
                product_analytics.experiments,
            )
            planning = growth_planning_engine.assess(
                play,
                budget_remaining=budget_remaining,
                research_signals=self._research_signals(play),
            )
            if not planning.feasible:
                continue
            score = max(
                0.0,
                min(
                    100.0,
                    play.priority_score + learning_adjustment + planning.adjustment,
                ),
            )
            rationale = [
                f"Base play priority={play.priority_score:.1f}/100.",
                learning_reason,
                *planning.rationale,
            ]
            scored.append((score, play, rationale))
        scored.sort(
            key=lambda item: (
                -item[0],
                item[1].time_to_signal_days,
                item[1].effort_hours,
                item[1].tactic_id,
                str(item[1].id),
            )
        )

        items: list[DistributionPortfolioItemView] = []
        allocated = 0.0
        used_platforms: set[str] = set()
        per_item_cap = (
            round(budget_remaining / max_items, 2)
            if budget_remaining is not None
            else None
        )
        for score, play, rationale in scored:
            if len(items) >= max_items:
                break
            adjusted_score = score
            if play.platform.value not in used_platforms:
                adjusted_score = min(100.0, adjusted_score + 3.0)
                rationale = rationale + [
                    "Small diversification bonus for a new platform."
                ]
            cap = self._portfolio_budget_cap(
                play,
                budget_remaining,
                allocated,
                per_item_cap,
            )
            if budget_remaining is not None and cap <= 0:
                continue
            items.append(
                DistributionPortfolioItemView(
                    play=play,
                    portfolio_score=adjusted_score,
                    recommended_budget_cap=cap,
                    rationale=rationale,
                )
            )
            allocated += cap
            used_platforms.add(play.platform.value)

        return DistributionPortfolioView(
            product_id=product_id,
            max_items=max_items,
            budget_remaining=budget_remaining,
            items=items,
        )

    def _research_signals(self, play: DistributionPlayView) -> dict:
        try:
            opportunity = audience_intelligence_service.find_opportunity(
                play.opportunity_id
            )
        except KeyError:
            return {}
        signals = opportunity.metadata.get("research_signals", {})
        return signals if isinstance(signals, dict) else {}

    def _hydrate_decision_state(self, experiment_id: UUID) -> None:
        if experiment_id in self._latest_fingerprint:
            return
        payload = self._store.get(
            DISTRIBUTION_DECISION_STATE_NAMESPACE,
            str(experiment_id),
        )
        if payload is None:
            return
        self._latest_fingerprint[experiment_id] = str(payload["fingerprint"])
        self._latest_decision[experiment_id] = UUID(str(payload["decision_id"]))

    def _get_decision(self, decision_id: UUID) -> DistributionGrowthDecisionView:
        cached = self._decisions.get(decision_id)
        if cached is not None:
            return cached
        payload = self._store.get(DISTRIBUTION_DECISION_NAMESPACE, str(decision_id))
        if payload is None:
            raise KeyError(decision_id)
        decision = DistributionGrowthDecisionView.model_validate(payload)
        self._decisions[decision_id] = decision
        return decision

    def _persist_decision(
        self,
        decision: DistributionGrowthDecisionView,
        fingerprint: str,
    ) -> None:
        self._store.put(
            DISTRIBUTION_DECISION_NAMESPACE,
            str(decision.id),
            decision.model_dump(mode="json"),
        )
        self._store.put(
            DISTRIBUTION_DECISION_STATE_NAMESPACE,
            str(decision.experiment_id),
            {
                "fingerprint": fingerprint,
                "decision_id": str(decision.id),
            },
        )

    def _hydrate_learning(self) -> None:
        existing_ids = {
            entry.id
            for entries in self._memory.values()
            for entry in entries
        }
        for payload in self._store.list_namespace(DISTRIBUTION_LEARNING_NAMESPACE):
            entry = DistributionLearningEntryView.model_validate(payload)
            if entry.id in existing_ids:
                continue
            self._memory.setdefault(entry.product_id, []).append(entry)
            existing_ids.add(entry.id)
        for entries in self._memory.values():
            entries.sort(key=lambda entry: (entry.created_at, str(entry.id)))

    def _learning_adjustment(
        self,
        play: DistributionPlayView,
        target_cac: float | None,
        experiments,
    ) -> tuple[float, str]:
        peers = [
            item
            for item in experiments
            if item.play.platform == play.platform
            and item.play.tactic_id == play.tactic_id
        ]
        if not peers:
            return 0.0, "No prior observed economics for this platform+tactic."
        spend = sum(item.metrics.spend for item in peers)
        paid = sum(item.metrics.paid_users for item in peers)
        cac = spend / paid if paid else None
        if target_cac is not None and cac is not None:
            ratio = cac / target_cac if target_cac else float("inf")
            if paid >= MIN_SCALE_PAID_USERS and ratio <= 0.8:
                return 15.0, (
                    f"Winner bonus: observed peer CAC={cac:.2f} below target."
                )
            if ratio <= 1.0:
                return 8.0, (
                    f"Positive bonus: observed peer CAC={cac:.2f} within target."
                )
            if ratio > 1.5 and paid >= 1:
                return -20.0, (
                    f"Loss penalty: observed peer CAC={cac:.2f} far above target."
                )
        no_paid_threshold = self._no_paid_guardrail(play, target_cac)
        if paid == 0 and spend >= no_paid_threshold:
            return -25.0, (
                f"Loss penalty: {spend:.2f} peer spend with no paid users."
            )
        if paid > 0:
            return 4.0, f"Evidence bonus: peer tactic produced {paid} paid users."
        return -5.0, (
            "Weak evidence penalty: peer tactic has spend but no conversion signal."
        )

    def _decide(self, target_cac, play, metrics) -> tuple[str, list[str]]:
        if target_cac is not None:
            if metrics.paid_users >= MIN_SCALE_PAID_USERS and metrics.cac is not None:
                ratio = metrics.cac / target_cac if target_cac else float("inf")
                if ratio <= 0.8:
                    return "SCALE", [
                        (
                            f"CAC {metrics.cac:.2f} is at least 20% below target "
                            f"{target_cac:.2f}."
                        ),
                        f"{metrics.paid_users} paid users meet the scale threshold.",
                    ]
                if ratio <= 1.1:
                    return "CONTINUE", [
                        f"CAC {metrics.cac:.2f} is close to target {target_cac:.2f}."
                    ]
                if ratio <= 1.5:
                    return "MODIFY", [
                        f"CAC {metrics.cac:.2f} is above target but the tactic converts."
                    ]
                return "STOP", [
                    (
                        f"CAC {metrics.cac:.2f} is more than 50% above target "
                        f"{target_cac:.2f}."
                    )
                ]
            if metrics.paid_users > 0 and metrics.cac is not None:
                if metrics.cac <= target_cac:
                    return "CONTINUE", [
                        "Early CAC is within target; collect more paid-user signal."
                    ]
                if (
                    metrics.cac > target_cac * 1.5
                    and metrics.spend >= max(25, target_cac * 2)
                ):
                    return "STOP", [
                        "Early CAC materially exceeds the loss guardrail."
                    ]
                return "MODIFY", ["The tactic converts but CAC is above target."]
            stop_threshold = self._no_paid_guardrail(play, target_cac)
            if metrics.spend >= stop_threshold:
                return "STOP", [
                    (
                        f"No paid users after {metrics.spend:.2f} spend; "
                        f"guardrail {stop_threshold:.2f} reached."
                    )
                ]
            if metrics.visits >= 20 and metrics.signups == 0:
                return "MODIFY", [
                    "Traffic arrives but no users sign up; change hook/CTA/landing."
                ]
            if metrics.signups >= 5 and metrics.paid_users == 0:
                return "MODIFY", [
                    "Signups arrive but no paid users; change activation/offer/paywall."
                ]
            return "CONTINUE", [
                "Insufficient conversion signal to scale or stop yet."
            ]

        if metrics.paid_users >= MIN_SCALE_PAID_USERS and (metrics.roas or 0) >= 1:
            return "SCALE", ["Positive ROAS with enough paid-user signal."]
        if metrics.paid_users > 0:
            return "CONTINUE", [
                "Paid users observed; collect more signal without a CAC target."
            ]
        if metrics.spend >= max(play.estimated_cost_max, 100):
            return "STOP", ["Fallback spend guardrail reached with no paid users."]
        if metrics.visits >= 20:
            return "MODIFY", [
                "Visits exist but no paid conversion; change one bottleneck."
            ]
        return "CONTINUE", ["Insufficient signal and no CAC target configured."]

    def _no_paid_guardrail(
        self,
        play: DistributionPlayView,
        target_cac: float | None,
    ) -> float:
        if target_cac is None:
            return max(play.estimated_cost_max, 100)
        target_guardrail = max(target_cac * 3, 25)
        return min(play.estimated_cost_max, target_guardrail)

    def _recommended_increment(
        self,
        action: str,
        play: DistributionPlayView,
        current_spend: float,
        budget_remaining: float | None,
    ) -> float:
        if action not in {"SCALE", "CONTINUE"}:
            return 0.0
        if action == "SCALE":
            desired = max(current_spend, play.estimated_cost_max, 25)
        else:
            desired = max(0.0, play.estimated_cost_max - current_spend)
        if budget_remaining is not None:
            desired = min(desired, budget_remaining)
        return round(max(0.0, desired), 2)

    def _portfolio_budget_cap(
        self,
        play: DistributionPlayView,
        budget_remaining: float | None,
        already_allocated: float,
        per_item_cap: float | None,
    ) -> float:
        desired = play.estimated_cost_max
        if per_item_cap is not None:
            desired = min(desired, per_item_cap)
        if budget_remaining is None:
            return round(desired, 2)
        available = max(0.0, budget_remaining - already_allocated)
        desired = min(desired, available)
        if desired < play.estimated_cost_min:
            return 0.0
        return round(desired, 2)

    def _budget_remaining(
        self,
        budget: float | None,
        total_spend: float,
    ) -> float | None:
        if budget is None:
            return None
        return round(max(0.0, budget - total_spend), 2)

    def _metric_summary(self, metrics, target_cac: float | None) -> str:
        target = (
            f", target CAC={target_cac:.2f}"
            if target_cac is not None
            else ""
        )
        return (
            f"Observed: spend={metrics.spend:.2f}, visits={metrics.visits}, "
            f"signups={metrics.signups}, paid={metrics.paid_users}, "
            f"CAC={metrics.cac}{target}."
        )

    def _fingerprint(self, *parts) -> str:
        payload = json.dumps(
            parts,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return sha1(payload.encode()).hexdigest()

    def reset(self) -> None:
        self._decisions.clear()
        self._latest_fingerprint.clear()
        self._latest_decision.clear()
        self._memory.clear()
        if self._store.ephemeral:
            self._store.clear_namespace(DISTRIBUTION_DECISION_NAMESPACE)
            self._store.clear_namespace(DISTRIBUTION_DECISION_STATE_NAMESPACE)
            self._store.clear_namespace(DISTRIBUTION_LEARNING_NAMESPACE)


distribution_growth_manager_service = InMemoryDistributionGrowthManagerService()
