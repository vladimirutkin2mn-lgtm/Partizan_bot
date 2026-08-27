from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.audience_intelligence_service import AUDIENCE_OPPORTUNITY_NAMESPACE
from app.distribution_analytics_service import (
    DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
    DISTRIBUTION_SPEND_NAMESPACE,
)
from app.distribution_execution_schemas import (
    DistributionExperimentStatus,
    DistributionExperimentView,
)
from app.distribution_execution_service import DISTRIBUTION_EXPERIMENT_NAMESPACE
from app.distribution_play_schemas import (
    DistributionPlayGenerationResponse,
    DistributionPlayStatus,
    DistributionPlayView,
)
from app.distribution_play_service import DISTRIBUTION_PLAY_NAMESPACE
from app.distribution_schemas import DistributionOpportunityView
from app.product_intake import PRODUCT_INTAKE_NAMESPACE
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.schemas import ProductProfileView
from app.self_research_benchmark_schemas import (
    SelfResearchBenchmarkCase,
    SelfResearchBenchmarkDataset,
    SelfResearchCandidatePlay,
    SelfResearchObservedEconomics,
    SelfResearchProductFacts,
    SelfResearchSplit,
)

SELF_RESEARCH_BENCHMARK_NAMESPACE = "self_research_benchmark"
SELF_RESEARCH_BENCHMARK_SCHEMA_VERSION = 1

_MIN_PAID_USERS = 2
_MIN_ACTIVATED_USERS = 5
_MIN_SIGNUPS = 10
_MIN_VISITS = 50


class SelfResearchBenchmarkBuilder:
    """Build a privacy-minimized benchmark from verified runtime facts.

    The builder intentionally uses an allowlist of aggregate fields. Raw briefs,
    reference URLs, analytics properties, provider metadata, credentials and account
    identifiers never enter the benchmark payload.
    """

    def __init__(self, store: RuntimeStateStore | None = None) -> None:
        self._store = store or get_runtime_store()

    @property
    def store(self) -> RuntimeStateStore:
        return self._store

    def build(self) -> SelfResearchBenchmarkDataset:
        opportunities = self._opportunities()
        experiments = self._experiments_by_product()
        event_rows = self._store.list_namespace(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE)
        spend_rows = self._store.list_namespace(DISTRIBUTION_SPEND_NAMESPACE)
        cases: list[SelfResearchBenchmarkCase] = []

        for play_payload in self._store.list_namespace(DISTRIBUTION_PLAY_NAMESPACE):
            generation = DistributionPlayGenerationResponse.model_validate(play_payload)
            product_payload = self._store.get(PRODUCT_INTAKE_NAMESPACE, str(generation.product_id))
            if product_payload is None:
                continue
            product = ProductProfileView.model_validate(product_payload["product"])
            case = self._build_case(
                product=product,
                plays=generation.plays,
                opportunities=opportunities,
                experiments=experiments.get(product.id, []),
                event_rows=event_rows,
                spend_rows=spend_rows,
            )
            if case is not None:
                cases.append(case)

        cases.sort(key=lambda item: item.case_id)
        version_payload = [
            case.model_dump(mode="json")
            for case in cases
        ]
        dataset_version = self._stable_hash(
            {
                "schema_version": SELF_RESEARCH_BENCHMARK_SCHEMA_VERSION,
                "cases": version_payload,
            }
        )
        dataset = SelfResearchBenchmarkDataset(
            dataset_version=dataset_version,
            case_count=len(cases),
            train_count=sum(item.split == SelfResearchSplit.TRAIN for item in cases),
            dev_count=sum(item.split == SelfResearchSplit.DEV for item in cases),
            test_count=sum(item.split == SelfResearchSplit.TEST for item in cases),
            decision_grade_count=sum(item.decision_grade for item in cases),
            cases=cases,
            created_at=datetime.now(UTC),
        )
        self._store.put_if_absent(
            SELF_RESEARCH_BENCHMARK_NAMESPACE,
            dataset.dataset_version,
            dataset.model_dump(mode="json"),
        )
        return self.get(dataset.dataset_version)

    def get(self, dataset_version: str) -> SelfResearchBenchmarkDataset:
        payload = self._store.get(SELF_RESEARCH_BENCHMARK_NAMESPACE, dataset_version)
        if payload is None:
            raise KeyError(dataset_version)
        return SelfResearchBenchmarkDataset.model_validate(payload)

    def list_versions(self) -> list[SelfResearchBenchmarkDataset]:
        datasets = [
            SelfResearchBenchmarkDataset.model_validate(payload)
            for payload in self._store.list_namespace(SELF_RESEARCH_BENCHMARK_NAMESPACE)
        ]
        return sorted(datasets, key=lambda item: item.dataset_version)

    def _build_case(
        self,
        *,
        product: ProductProfileView,
        plays: list[DistributionPlayView],
        opportunities: dict[UUID, DistributionOpportunityView],
        experiments: list[DistributionExperimentView],
        event_rows: list[dict],
        spend_rows: list[dict],
    ) -> SelfResearchBenchmarkCase | None:
        if not plays:
            return None
        experiments_by_play: dict[UUID, list[DistributionExperimentView]] = defaultdict(list)
        for experiment in experiments:
            if experiment.status in {
                DistributionExperimentStatus.RUNNING,
                DistributionExperimentStatus.FINISHED,
            }:
                experiments_by_play[experiment.distribution_play_id].append(experiment)

        candidates: list[SelfResearchCandidatePlay] = []
        for play in plays:
            opportunity = opportunities.get(play.opportunity_id)
            observed = self._observed_economics(
                experiments_by_play.get(play.id, []),
                event_rows=event_rows,
                spend_rows=spend_rows,
            )
            evidence_count = len(opportunity.evidence) if opportunity is not None else 0
            candidates.append(
                SelfResearchCandidatePlay(
                    candidate_key=self._candidate_key(play, opportunity),
                    platform=play.platform.value,
                    tactic_id=play.tactic_id,
                    tactic_class=play.tactic_class.value,
                    action_type=play.action_type.value,
                    opportunity_kind=play.opportunity_kind.value,
                    priority_score=play.priority_score,
                    executable=play.status == DistributionPlayStatus.READY,
                    evidence_count=evidence_count,
                    provenance_present=evidence_count > 0,
                    observed=observed,
                )
            )
        candidates.sort(key=lambda item: item.candidate_key)
        winner_key, objective, metric_value = self._winner(candidates)
        measured_count = sum(item.observed.experiment_count > 0 for item in candidates)
        case_id = self._stable_hash(
            {
                "case_schema": SELF_RESEARCH_BENCHMARK_SCHEMA_VERSION,
                "product_id": str(product.id),
            }
        )[:32]
        return SelfResearchBenchmarkCase(
            case_id=case_id,
            split=self._split(case_id),
            product=SelfResearchProductFacts(
                market=self._clean_text(product.market, 200),
                language=self._clean_text(product.language, 80),
                goal=self._clean_text(product.goal, 300),
                budget=product.budget,
                max_cac=product.max_cac,
            ),
            candidates=candidates,
            winner_candidate_key=winner_key,
            winner_objective=objective,
            winner_metric_value=metric_value,
            measured_candidate_count=measured_count,
            decision_grade=winner_key is not None,
        )

    def _observed_economics(
        self,
        experiments: list[DistributionExperimentView],
        *,
        event_rows: list[dict],
        spend_rows: list[dict],
    ) -> SelfResearchObservedEconomics:
        experiment_ids = {str(item.id) for item in experiments}
        if not experiment_ids:
            return SelfResearchObservedEconomics()
        events = [row for row in event_rows if str(row.get("experiment_id")) in experiment_ids]
        spend = [row for row in spend_rows if str(row.get("experiment_id")) in experiment_ids]
        spend_amount = round(sum(float(row.get("amount", 0)) for row in spend), 2)
        visits = sum(str(row.get("event_type")) == "VISIT" for row in events)
        signups = self._unique_events(events, "SIGNUP")
        activated = self._unique_events(events, "ACTIVATED")
        paid_users = self._unique_events(events, "PAID")
        revenue = round(
            sum(
                float(row.get("revenue", 0))
                for row in events
                if str(row.get("event_type")) == "PAID"
            ),
            2,
        )
        return SelfResearchObservedEconomics(
            experiment_count=len(experiments),
            spend=spend_amount,
            visits=visits,
            signups=signups,
            activated_users=activated,
            paid_users=paid_users,
            revenue=revenue,
            cac=round(spend_amount / paid_users, 2) if paid_users else None,
            roas=round(revenue / spend_amount, 3) if spend_amount else None,
        )

    @staticmethod
    def _unique_events(rows: list[dict], event_type: str) -> int:
        identities = {
            f"actor:{row.get('actor_id')}"
            if row.get("actor_id")
            else f"event:{row.get('event_id')}"
            for row in rows
            if str(row.get("event_type")) == event_type
        }
        return len(identities)

    def _winner(
        self,
        candidates: list[SelfResearchCandidatePlay],
    ) -> tuple[str | None, str | None, float | None]:
        measured = [item for item in candidates if item.observed.experiment_count > 0]
        paid = [item for item in measured if item.observed.paid_users >= _MIN_PAID_USERS]
        cac_candidates = [item for item in paid if item.observed.cac is not None]
        if len(cac_candidates) >= 2:
            winner = min(
                cac_candidates,
                key=lambda item: (
                    item.observed.cac,
                    -(item.observed.roas or 0),
                    -item.observed.revenue,
                    item.candidate_key,
                ),
            )
            return winner.candidate_key, "CAC", winner.observed.cac
        if len(paid) >= 2:
            winner = max(
                paid,
                key=lambda item: (
                    item.observed.paid_users,
                    item.observed.revenue,
                    -item.observed.spend,
                    item.candidate_key,
                ),
            )
            return winner.candidate_key, "PAID_USERS", float(winner.observed.paid_users)

        activation = [
            item
            for item in measured
            if item.observed.activated_users >= _MIN_ACTIVATED_USERS
            and item.observed.signups >= _MIN_SIGNUPS
        ]
        if len(activation) >= 2:
            winner = max(
                activation,
                key=lambda item: (
                    item.observed.activated_users / item.observed.signups,
                    item.observed.activated_users,
                    item.candidate_key,
                ),
            )
            value = winner.observed.activated_users / winner.observed.signups
            return winner.candidate_key, "ACTIVATION_RATE", round(value, 6)

        signup = [
            item
            for item in measured
            if item.observed.signups >= _MIN_SIGNUPS and item.observed.visits >= _MIN_VISITS
        ]
        if len(signup) >= 2:
            winner = max(
                signup,
                key=lambda item: (
                    item.observed.signups / item.observed.visits,
                    item.observed.signups,
                    item.candidate_key,
                ),
            )
            value = winner.observed.signups / winner.observed.visits
            return winner.candidate_key, "SIGNUP_RATE", round(value, 6)
        return None, None, None

    def _candidate_key(
        self,
        play: DistributionPlayView,
        opportunity: DistributionOpportunityView | None,
    ) -> str:
        opportunity_key = (
            opportunity.canonical_key if opportunity is not None else str(play.opportunity_id)
        )
        return self._stable_hash(
            {
                "platform": play.platform.value,
                "tactic_id": play.tactic_id,
                "opportunity_kind": play.opportunity_kind.value,
                "opportunity_key": opportunity_key,
            }
        )[:24]

    def _opportunities(self) -> dict[UUID, DistributionOpportunityView]:
        result: dict[UUID, DistributionOpportunityView] = {}
        for payload in self._store.list_namespace(AUDIENCE_OPPORTUNITY_NAMESPACE):
            opportunity = DistributionOpportunityView.model_validate(payload)
            result[opportunity.id] = opportunity
        return result

    def _experiments_by_product(self) -> dict[UUID, list[DistributionExperimentView]]:
        result: dict[UUID, list[DistributionExperimentView]] = defaultdict(list)
        for payload in self._store.list_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE):
            experiment = DistributionExperimentView.model_validate(payload)
            result[experiment.product_id].append(experiment)
        return result

    @staticmethod
    def _split(case_id: str) -> SelfResearchSplit:
        bucket = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        if bucket < 70:
            return SelfResearchSplit.TRAIN
        if bucket < 85:
            return SelfResearchSplit.DEV
        return SelfResearchSplit.TEST

    @staticmethod
    def _stable_hash(payload: Any) -> str:
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _clean_text(value: Any, max_length: int) -> str | None:
        if value is None:
            return None
        text = " ".join(str(value).split()).strip()
        return text[:max_length] or None


self_research_benchmark_builder = SelfResearchBenchmarkBuilder()
