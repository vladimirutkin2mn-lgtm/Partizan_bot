import json
from datetime import UTC, datetime
from hashlib import sha1
from uuid import UUID, uuid4

from app.analytics_service import analytics_service
from app.execution_service import execution_service, find_growth_play
from app.growth_manager import POLICY_VERSION, GrowthPolicy
from app.growth_manager_schemas import (
    DecisionHistoryView,
    GrowthDecisionView,
    LearningMemoryEntryView,
    LearningMemoryView,
    ProductDecisionHistoryView,
)
from app.product_intake import product_intake_service


class InMemoryGrowthManagerService:
    def __init__(self) -> None:
        self._decisions: dict[UUID, GrowthDecisionView] = {}
        self._by_experiment: dict[UUID, list[UUID]] = {}
        self._by_product: dict[UUID, list[UUID]] = {}
        self._memory: dict[UUID, list[LearningMemoryEntryView]] = {}
        self._latest_fingerprint: dict[UUID, str] = {}
        self._latest_decision: dict[UUID, UUID] = {}

    def evaluate(self, experiment_id: UUID) -> GrowthDecisionView:
        experiment = execution_service.get_experiment(experiment_id)
        if experiment.status not in {"RUNNING", "FINISHED"}:
            raise ValueError("Growth Manager requires a RUNNING or FINISHED experiment")

        product = product_intake_service.get_product(experiment.product_id)
        play = find_growth_play(experiment.product_id, experiment.growth_play_id)
        analytics = analytics_service.experiment_analytics(experiment_id)
        product_analytics = analytics_service.product_analytics(experiment.product_id)
        fingerprint = self._fingerprint(
            analytics.model_dump(mode="json"),
            product_analytics.model_dump(mode="json"),
            product.budget,
            product.max_cac,
        )
        if self._latest_fingerprint.get(experiment_id) == fingerprint:
            decision_id = self._latest_decision[experiment_id]
            return self._decisions[decision_id].model_copy(update={"duplicate": True})

        policy = GrowthPolicy().evaluate(
            product=product,
            play=play,
            analytics=analytics,
            product_analytics=product_analytics,
        )
        now = datetime.now(UTC)
        decision = GrowthDecisionView(
            id=uuid4(),
            product_id=experiment.product_id,
            experiment_id=experiment_id,
            action=policy.action,
            rationale=policy.rationale,
            policy_version=POLICY_VERSION,
            metrics=analytics.metrics,
            budget_remaining=policy.budget_remaining,
            recommended_budget_increment=policy.recommended_budget_increment,
            next_hypothesis=policy.next_hypothesis,
            created_at=now,
        )
        self._decisions[decision.id] = decision
        self._by_experiment.setdefault(experiment_id, []).append(decision.id)
        self._by_product.setdefault(experiment.product_id, []).append(decision.id)
        self._latest_fingerprint[experiment_id] = fingerprint
        self._latest_decision[experiment_id] = decision.id
        self._memory.setdefault(experiment.product_id, []).append(
            LearningMemoryEntryView(
                id=uuid4(),
                product_id=experiment.product_id,
                experiment_id=experiment_id,
                source_type=play.source_type,
                template_id=play.template_id,
                action=decision.action,
                observed_cac=analytics.metrics.cac,
                paid_users=analytics.metrics.paid_users,
                revenue=analytics.metrics.revenue,
                summary=(
                    f"{play.template_id} on {play.source_type}: action={decision.action}; "
                    f"spend={analytics.metrics.spend:.2f}; paid={analytics.metrics.paid_users}; "
                    f"CAC={analytics.metrics.cac}; revenue={analytics.metrics.revenue:.2f}."
                ),
                created_at=now,
            )
        )
        return decision

    def experiment_history(self, experiment_id: UUID) -> DecisionHistoryView:
        execution_service.get_experiment(experiment_id)
        ids = self._by_experiment.get(experiment_id, [])
        return DecisionHistoryView(
            experiment_id=experiment_id,
            decisions=[self._decisions[decision_id] for decision_id in ids],
        )

    def product_history(self, product_id: UUID) -> ProductDecisionHistoryView:
        product_intake_service.get_product(product_id)
        ids = self._by_product.get(product_id, [])
        return ProductDecisionHistoryView(
            product_id=product_id,
            decisions=[self._decisions[decision_id] for decision_id in ids],
        )

    def learning_memory(self, product_id: UUID) -> LearningMemoryView:
        product_intake_service.get_product(product_id)
        return LearningMemoryView(
            product_id=product_id,
            entries=list(self._memory.get(product_id, [])),
        )

    def _fingerprint(self, *parts) -> str:
        payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
        return sha1(payload.encode()).hexdigest()

    def reset(self) -> None:
        self._decisions.clear()
        self._by_experiment.clear()
        self._by_product.clear()
        self._memory.clear()
        self._latest_fingerprint.clear()
        self._latest_decision.clear()


growth_manager_service = InMemoryGrowthManagerService()
