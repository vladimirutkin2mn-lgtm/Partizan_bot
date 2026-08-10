import json
from datetime import UTC, datetime
from hashlib import sha1
from uuid import UUID, uuid4

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
from app.product_intake import product_intake_service

MIN_SCALE_PAID_USERS = 3


class InMemoryDistributionGrowthManagerService:
    def __init__(self) -> None:
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
        if self._latest_fingerprint.get(experiment_id) == fingerprint:
            decision_id = self._latest_decision[experiment_id]
            return self._decisions[decision_id].model_copy(update={"duplicate": True})

        action, rationale = self._decide(product.max_cac, play, analytics.metrics)
        budget_remaining = self._budget_remaining(product.budget, product_analytics.total_spend)
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
        self._memory.setdefault(product.id, []).append(
            DistributionLearningEntryView(
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
        )
        return decision

    def learning_memory(self, product_id: UUID) -> DistributionLearningMemoryView:
        product_intake_service.get_product(product_id)
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
        budget_remaining = self._budget_remaining(product.budget, product_analytics.total_spend)
        ready = [
            play for play in play_result.plays if play.status == DistributionPlayStatus.READY
        ]
        running_play_ids = {
            experiment.distribution_play_id
            for experiment in distribution_execution_service.list_experiments(product_id)
            if experiment.status == DistributionExperimentStatus.RUNNING
        }
        ready = [play for play in ready if play.id not in running_play_ids]

        scored: list[tuple[float, DistributionPlayView, list[str]]] = []
        for play in ready:
            adjustment, learning_reason = self._learning_adjustment(
                play,
                product.max_cac,
                product_analytics.experiments,
            )
            score = max(0.0, min(100.0, play.priority_score + adjustment))
            rationale = [
                f"Base play priority={play.priority_score:.1f}/100.",
                learning_reason,
            ]
            scored.append((score, play, rationale))
        scored.sort(key=lambda item: (-item[0], item[1].tactic_id, str(item[1].id)))

        items: list[DistributionPortfolioItemView] = []
        allocated = 0.0
        used_platforms: set[str] = set()
        for score, play, rationale in scored:
            if len(items) >= max_items:
                break
            adjusted_score = score
            if play.platform.value not in used_platforms:
                adjusted_score = min(100.0, adjusted_score + 3.0)
                rationale = rationale + ["Small diversification bonus for a new platform."]
            cap = self._portfolio_budget_cap(
                play,
                budget_remaining,
                allocated,
            )
            if budget_remaining is not None and cap <= 0:
                break
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

    def _learning_adjustment(
        self,
        play: DistributionPlayView,
        target_cac: float | None,
        experiments,
    ) -> tuple[float, str]:
        peers = [
            item
            for item in experiments
            if item.play.platform == play.platform and item.play.tactic_id == play.tactic_id
        ]
        if not peers:
            return 0.0, "No prior observed economics for this platform+tactic."
        spend = sum(item.metrics.spend for item in peers)
        paid = sum(item.metrics.paid_users for item in peers)
        cac = spend / paid if paid else None
        if target_cac is not None and cac is not None:
            ratio = cac / target_cac if target_cac else float("inf")
            if paid >= MIN_SCALE_PAID_USERS and ratio <= 0.8:
                return 15.0, f"Winner bonus: observed peer CAC={cac:.2f} below target."
            if ratio <= 1.0:
                return 8.0, f"Positive bonus: observed peer CAC={cac:.2f} within target."
            if ratio > 1.5 and paid >= 1:
                return -20.0, f"Loss penalty: observed peer CAC={cac:.2f} far above target."
        if paid == 0 and spend >= max(play.estimated_cost_max, 25):
            return -25.0, f"Loss penalty: {spend:.2f} peer spend with no paid users."
        if paid > 0:
            return 4.0, f"Evidence bonus: peer tactic produced {paid} paid users."
        return -5.0, "Weak evidence penalty: peer tactic has spend but no conversion signal."

    def _decide(self, target_cac, play, metrics) -> tuple[str, list[str]]:
        if target_cac is not None:
            if metrics.paid_users >= MIN_SCALE_PAID_USERS and metrics.cac is not None:
                ratio = metrics.cac / target_cac if target_cac else float("inf")
                if ratio <= 0.8:
                    return "SCALE", [
                        f"CAC {metrics.cac:.2f} is at least 20% below target {target_cac:.2f}.",
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
                    f"CAC {metrics.cac:.2f} is more than 50% above target {target_cac:.2f}."
                ]
            if metrics.paid_users > 0 and metrics.cac is not None:
                if metrics.cac <= target_cac:
                    return "CONTINUE", [
                        "Early CAC is within target; collect more paid-user signal."
                    ]
                if metrics.cac > target_cac * 1.5 and metrics.spend >= max(25, target_cac * 2):
                    return "STOP", ["Early CAC materially exceeds the loss guardrail."]
                return "MODIFY", ["The tactic converts but CAC is above target."]
            stop_threshold = max(play.estimated_cost_max, target_cac * 3, 25)
            if metrics.spend >= stop_threshold:
                return "STOP", [
                    f"No paid users after {metrics.spend:.2f} spend; guardrail reached."
                ]
            if metrics.visits >= 20 and metrics.signups == 0:
                return "MODIFY", ["Traffic arrives but no users sign up; change hook/CTA/landing."]
            if metrics.signups >= 5 and metrics.paid_users == 0:
                return "MODIFY", ["Signups arrive but no paid users; change activation/offer/paywall."]
            return "CONTINUE", ["Insufficient conversion signal to scale or stop yet."]

        if metrics.paid_users >= MIN_SCALE_PAID_USERS and (metrics.roas or 0) >= 1:
            return "SCALE", ["Positive ROAS with enough paid-user signal."]
        if metrics.paid_users > 0:
            return "CONTINUE", ["Paid users observed; collect more signal without a CAC target."]
        if metrics.spend >= max(play.estimated_cost_max, 100):
            return "STOP", ["Fallback spend guardrail reached with no paid users."]
        if metrics.visits >= 20:
            return "MODIFY", ["Visits exist but no paid conversion; change one bottleneck."]
        return "CONTINUE", ["Insufficient signal and no CAC target configured."]

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
    ) -> float:
        desired = play.estimated_cost_max
        if budget_remaining is None:
            return round(desired, 2)
        available = max(0.0, budget_remaining - already_allocated)
        return round(min(desired, available), 2)

    def _budget_remaining(self, budget: float | None, total_spend: float) -> float | None:
        if budget is None:
            return None
        return round(max(0.0, budget - total_spend), 2)

    def _metric_summary(self, metrics, target_cac: float | None) -> str:
        target = f", target CAC={target_cac:.2f}" if target_cac is not None else ""
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


distribution_growth_manager_service = InMemoryDistributionGrowthManagerService()
