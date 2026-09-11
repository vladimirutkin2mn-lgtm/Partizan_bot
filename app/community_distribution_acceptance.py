from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, Field

from app.audience_intelligence_service import (
    AUDIENCE_MAP_NAMESPACE,
    AUDIENCE_OPPORTUNITY_NAMESPACE,
)
from app.config import Settings, get_settings
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_analytics_service import (
    DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
    DISTRIBUTION_SPEND_NAMESPACE,
)
from app.distribution_control_plane_service import (
    COMMUNITY_POLICY_NAMESPACE,
    DISTRIBUTION_IDENTITY_NAMESPACE,
)
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    DISTRIBUTION_EXPERIMENT_NAMESPACE,
)
from app.distribution_growth_manager_service import DISTRIBUTION_DECISION_NAMESPACE
from app.distribution_schemas import CommunityPolicyView
from app.managed_distribution import (
    MANAGED_ASSIGNMENT_NAMESPACE,
    MANAGED_PUBLISHER_NAMESPACE,
)
from app.reddit_client_publishing import (
    CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
    CUSTOMER_REDDIT_OBSERVATION_NAMESPACE,
    CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE,
)
from app.reddit_research import (
    REDDIT_POLICY_MAX_AGE,
    action_target_is_fresh,
    policy_freshness_reason,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_governance import CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
)


class AcceptanceState(StrEnum):
    BLOCKED = "BLOCKED"
    READY_FOR_REAL_RUN = "READY_FOR_REAL_RUN"
    PARTIAL = "PARTIAL"
    VERIFIED = "VERIFIED"


class AcceptanceCheckKind(StrEnum):
    READINESS = "READINESS"
    EVIDENCE = "EVIDENCE"
    POLICY = "POLICY"


class AcceptanceCheckView(BaseModel):
    key: str
    kind: AcceptanceCheckKind
    satisfied: bool
    required_for_close: bool = True
    detail: str
    evidence_count: int = Field(default=0, ge=0)
    latest_at: datetime | None = None
    sample: dict[str, Any] = Field(default_factory=dict)


class CommunityDistributionPhaseAcceptanceView(BaseModel):
    issue_number: int
    phase: int
    name: str
    state: AcceptanceState
    current_ready: bool
    evidence_complete: bool
    production_verified: bool
    blockers: list[str] = Field(default_factory=list)
    checks: list[AcceptanceCheckView] = Field(default_factory=list)


class CommunityDistributionAcceptanceReport(BaseModel):
    generated_at: datetime
    release_sha: str
    app_env: str
    runtime_storage: str
    project_id: UUID | None = None
    product_id: UUID | None = None
    production_environment_eligible: bool
    environment_blockers: list[str] = Field(default_factory=list)
    phases: list[CommunityDistributionPhaseAcceptanceView]


class CommunityDistributionAcceptanceError(RuntimeError):
    pass


class CommunityDistributionAcceptanceService:
    """Read-only acceptance evidence collector for community distribution phases.

    The service never invokes a provider, publishes content, mutates readiness, reads provider
    secret values, or closes issues. It only summarizes settings and already persisted runtime
    evidence. Production verification additionally requires the durable production runtime.
    """

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings = settings or get_settings()

    def report(self, project_id: UUID | None = None) -> CommunityDistributionAcceptanceReport:
        _, product_id = self._scope(project_id)
        production_eligible, environment_blockers = self._production_environment()
        projects = self._projects()
        experiments = self._experiments(product_id)
        actions = self._actions(experiments)
        opportunities = self._opportunities(product_id, experiments)
        project_ids = self._project_ids_for_scope(project_id, product_id, projects)

        phases = [
            self._phase2(production_eligible, opportunities),
            self._phase3(production_eligible, project_ids, experiments, actions),
            self._phase4(production_eligible, opportunities),
            self._phase5(production_eligible, project_ids, experiments, actions),
            self._phase6(production_eligible, product_id, experiments, actions),
            self._phase7(production_eligible, experiments, actions),
        ]
        return CommunityDistributionAcceptanceReport(
            generated_at=datetime.now(UTC),
            release_sha=self._settings.partizan_release_sha,
            app_env=self._settings.app_env,
            runtime_storage=self._settings.runtime_storage,
            project_id=project_id,
            product_id=product_id,
            production_environment_eligible=production_eligible,
            environment_blockers=environment_blockers,
            phases=phases,
        )

    def _phase2(
        self,
        production_eligible: bool,
        opportunities: list[dict],
    ) -> CommunityDistributionPhaseAcceptanceView:
        ready = self._telegram_research_ready()
        verified = [
            row
            for row in opportunities
            if str(row.get("platform") or "").upper() == "TELEGRAM"
            and isinstance(row.get("metadata"), dict)
            and str(row["metadata"].get("native_research_status") or "").upper()
            == "VERIFIED"
            and row["metadata"].get("telegram_entity_id") is not None
            and row["metadata"].get("source_checked_at")
            and self._public_host(row.get("url")) in {"t.me", "telegram.me"}
        ]
        sample = self._telegram_research_sample(verified[0]) if verified else {}
        checks = [
            self._check(
                "telegram_research_current_ready",
                AcceptanceCheckKind.READINESS,
                ready,
                "Telegram native research provider is currently production-configured."
                if ready
                else "Telegram native research is not currently fully configured/readied.",
                required=False,
            ),
            self._check(
                "real_native_telegram_research",
                AcceptanceCheckKind.EVIDENCE,
                bool(verified),
                "Persisted native Telegram community research evidence exists."
                if verified
                else "No persisted verified native Telegram community research evidence found.",
                count=len(verified),
                latest=self._latest(verified, ("metadata", "source_checked_at")),
                sample=sample,
            ),
        ]
        return self._phase(
            250,
            2,
            "Telegram research connector",
            production_eligible,
            checks,
        )

    def _phase3(
        self,
        production_eligible: bool,
        project_ids: set[str],
        experiments: dict[str, dict],
        actions: dict[str, dict],
    ) -> CommunityDistributionPhaseAcceptanceView:
        ready = self._telegram_publish_ready()
        connections = self._active_connections(
            CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
            project_ids,
            required_scopes=None,
        )
        receipts = self._executed_receipts(
            CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
            actions,
        )
        observations = {
            str(row.get("action_id"))
            for row in self._store.list_namespace(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE)
            if row.get("action_id") and self._telegram_observation_has_result(row)
        }
        complete = [row for row in receipts if str(row.get("action_id")) in observations]
        sample = self._publish_sample(complete[0]) if complete else {}
        checks = [
            self._check(
                "telegram_client_publish_current_ready",
                AcceptanceCheckKind.READINESS,
                ready,
                "Telegram client-owned publishing is currently production-configured."
                if ready
                else "Telegram client-owned publishing is not currently fully configured/readied.",
                required=False,
            ),
            self._check(
                "active_telegram_connection",
                AcceptanceCheckKind.READINESS,
                bool(connections),
                "An active scoped Telegram customer connection exists."
                if connections
                else "No active scoped Telegram customer connection is persisted.",
                count=len(connections),
                required=False,
            ),
            self._check(
                "real_telegram_publish_and_observation",
                AcceptanceCheckKind.EVIDENCE,
                bool(complete),
                "A confirmed Telegram publish has a persisted post-publish observation."
                if complete
                else "No same-action confirmed Telegram publish + observation chain found.",
                count=len(complete),
                latest=self._latest(complete, ("published_at",)),
                sample=sample,
            ),
        ]
        return self._phase(
            251,
            3,
            "Telegram client-owned publish",
            production_eligible,
            checks,
        )

    def _phase4(
        self,
        production_eligible: bool,
        opportunities: list[dict],
    ) -> CommunityDistributionPhaseAcceptanceView:
        reddit = [
            row
            for row in opportunities
            if str(row.get("platform") or "").upper() == "REDDIT"
            and self._public_host(row.get("url")) in {"reddit.com", "www.reddit.com"}
        ]
        opportunity_ids = {str(row.get("id")) for row in reddit if row.get("id")}
        policies = [
            row
            for row in self._store.list_namespace(COMMUNITY_POLICY_NAMESPACE)
            if str(row.get("opportunity_id")) in opportunity_ids
            and self._current_indexed_reddit_policy(row)
        ]
        researched_ids = {str(row.get("opportunity_id")) for row in policies}
        researched = [row for row in reddit if str(row.get("id")) in researched_ids]
        partial_policies = [
            row
            for row in policies
            if str(row.get("research_status") or "").upper() == "PARTIAL"
        ]
        ambiguous_fail_closed = all(
            self._reddit_partial_policy_remains_fail_closed(row) for row in partial_policies
        )
        fresh_targets = self._fresh_reddit_targets(researched)
        real_search_ready = self._real_search_ready()
        sample = self._reddit_research_sample(researched[0], policies) if researched else {}
        checks = [
            self._check(
                "real_search_provider_current_ready",
                AcceptanceCheckKind.READINESS,
                real_search_ready,
                "A non-mock search provider is currently configured."
                if real_search_ready
                else "Production search is not currently configured with a non-mock provider.",
                required=False,
            ),
            self._check(
                "real_reddit_indexed_policy_research",
                AcceptanceCheckKind.EVIDENCE,
                bool(researched),
                "A concrete Reddit opportunity has fresh evidence-backed indexed policy research."
                if researched
                else (
                    "No scoped Reddit opportunity with fresh evidence-backed indexed policy "
                    "research found."
                ),
                count=len(researched),
                latest=self._latest(policies, ("last_checked_at",)),
                sample=sample,
            ),
            self._check(
                "reddit_ambiguous_policy_fail_closed",
                AcceptanceCheckKind.POLICY,
                bool(researched) and ambiguous_fail_closed,
                (
                    "Ambiguous indexed Reddit policies remain blocked by the execution freshness "
                    "gate."
                )
                if researched and ambiguous_fail_closed
                else "At least one ambiguous indexed Reddit policy is not proven fail-closed.",
                count=len(partial_policies),
            ),
            self._check(
                "fresh_reddit_thread_target",
                AcceptanceCheckKind.EVIDENCE,
                bool(fresh_targets),
                "Fresh Reddit thread target provenance is available."
                if fresh_targets
                else "No currently fresh Reddit thread target is persisted (optional where unavailable).",
                count=len(fresh_targets),
                latest=self._latest(fresh_targets, ("checked_at",)),
                sample=self._target_sample(fresh_targets[0]) if fresh_targets else {},
                required=False,
            ),
        ]
        return self._phase(
            252,
            4,
            "Reddit research and CommunityPolicy",
            production_eligible,
            checks,
        )

    def _phase5(
        self,
        production_eligible: bool,
        project_ids: set[str],
        experiments: dict[str, dict],
        actions: dict[str, dict],
    ) -> CommunityDistributionPhaseAcceptanceView:
        ready = self._reddit_publish_ready()
        connections = self._active_connections(
            CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
            project_ids,
            required_scopes={"identity", "read", "submit"},
        )
        receipts = self._executed_receipts(
            CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE,
            actions,
        )
        observations = {
            str(row.get("action_id")): row
            for row in self._store.list_namespace(CUSTOMER_REDDIT_OBSERVATION_NAMESPACE)
            if row.get("action_id") and self._reddit_observation_has_result(row)
        }
        events = self._events_for_experiments(set(experiments))
        funnel_types = {"VISIT", "SIGNUP", "ACTIVATED", "PAID"}
        complete: list[dict] = []
        attributed: list[dict] = []
        for receipt in receipts:
            action = actions.get(str(receipt.get("action_id")))
            experiment_id = str(action.get("experiment_id")) if action else ""
            if any(
                str(event.get("experiment_id")) == experiment_id
                and str(event.get("event_type") or "").upper() in funnel_types
                for event in events
            ):
                attributed.append(receipt)
            if str(receipt.get("action_id")) in observations:
                complete.append(receipt)
        end_to_end = [row for row in complete if row in attributed]
        checks = [
            self._check(
                "reddit_commercial_api_access_verified",
                AcceptanceCheckKind.POLICY,
                self._settings.reddit_commercial_access_verified,
                "Reddit commercial/API access is explicitly verified."
                if self._settings.reddit_commercial_access_verified
                else "Reddit commercial/API access is not explicitly verified.",
            ),
            self._check(
                "reddit_client_publish_current_ready",
                AcceptanceCheckKind.READINESS,
                ready,
                "Reddit client-owned publishing is currently fully configured/readied."
                if ready
                else "Reddit client-owned publishing is not currently fully configured/readied.",
                required=False,
            ),
            self._check(
                "active_reddit_oauth_connection",
                AcceptanceCheckKind.EVIDENCE,
                bool(connections),
                "An active scoped Reddit OAuth connection with required scopes exists."
                if connections
                else "No active scoped Reddit OAuth connection with all required scopes exists.",
                count=len(connections),
            ),
            self._check(
                "real_reddit_publish_and_observation",
                AcceptanceCheckKind.EVIDENCE,
                bool(complete),
                "A confirmed Reddit publish has a persisted same-action outcome observation."
                if complete
                else "No same-action confirmed Reddit publish + outcome observation chain found.",
                count=len(complete),
                latest=self._latest(complete, ("published_at",)),
                sample=self._publish_sample(complete[0]) if complete else {},
            ),
            self._check(
                "downstream_attribution_observed",
                AcceptanceCheckKind.EVIDENCE,
                bool(attributed),
                "A downstream funnel event is attributed to a confirmed Reddit publish experiment."
                if attributed
                else (
                    "No downstream VISIT/SIGNUP/ACTIVATED/PAID event is tied to a confirmed "
                    "Reddit publish experiment."
                ),
                count=len(attributed),
            ),
            self._check(
                "reddit_end_to_end_evidence_chain",
                AcceptanceCheckKind.EVIDENCE,
                bool(end_to_end),
                "The same Reddit action has publish, observation and downstream attribution evidence."
                if end_to_end
                else (
                    "No single Reddit action currently has the full publish + observation + "
                    "attribution chain."
                ),
                count=len(end_to_end),
            ),
        ]
        return self._phase(
            253,
            5,
            "Reddit client-owned publish",
            production_eligible,
            checks,
        )

    def _phase6(
        self,
        production_eligible: bool,
        product_id: UUID | None,
        experiments: dict[str, dict],
        actions: dict[str, dict],
    ) -> CommunityDistributionPhaseAcceptanceView:
        ready = self._managed_distribution_ready()
        assignments = [
            row
            for row in self._store.list_namespace(MANAGED_ASSIGNMENT_NAMESPACE)
            if str(row.get("status") or "").upper() == "FULFILLED"
            and (product_id is None or str(row.get("product_id")) == str(product_id))
            and str(row.get("action_id") or "") in actions
        ]
        events = self._events_for_experiments(set(experiments))
        reddit_obs = self._observation_action_ids(CUSTOMER_REDDIT_OBSERVATION_NAMESPACE)
        telegram_obs = self._observation_action_ids(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE)
        with_outcome: list[dict] = []
        for row in assignments:
            action_id = str(row.get("action_id") or "")
            action = actions.get(action_id)
            experiment_id = str(action.get("experiment_id")) if action else ""
            event_found = any(str(event.get("experiment_id")) == experiment_id for event in events)
            if event_found or action_id in reddit_obs or action_id in telegram_obs:
                with_outcome.append(row)
        checks = [
            self._check(
                "managed_distribution_current_ready",
                AcceptanceCheckKind.READINESS,
                ready,
                "Managed Distribution is currently public-ready with eligible inventory."
                if ready
                else "Managed Distribution is not currently public-ready with eligible inventory.",
                required=False,
            ),
            self._check(
                "real_managed_fulfillment",
                AcceptanceCheckKind.EVIDENCE,
                bool(assignments),
                "A fulfilled scoped managed assignment is persisted."
                if assignments
                else "No fulfilled scoped managed assignment is persisted.",
                count=len(assignments),
                latest=self._latest(assignments, ("fulfilled_at",)),
                sample=self._managed_sample(assignments[0]) if assignments else {},
            ),
            self._check(
                "managed_measurable_outcome",
                AcceptanceCheckKind.EVIDENCE,
                bool(with_outcome),
                "A fulfilled managed assignment has persisted measurable outcome evidence."
                if with_outcome
                else "No fulfilled managed assignment has a persisted analytics/platform outcome.",
                count=len(with_outcome),
            ),
        ]
        return self._phase(
            254,
            6,
            "Partizan Managed Distribution",
            production_eligible,
            checks,
        )

    def _phase7(
        self,
        production_eligible: bool,
        experiments: dict[str, dict],
        actions: dict[str, dict],
    ) -> CommunityDistributionPhaseAcceptanceView:
        events = self._events_for_experiments(set(experiments))
        spends = [
            row
            for row in self._store.list_namespace(DISTRIBUTION_SPEND_NAMESPACE)
            if str(row.get("experiment_id")) in experiments
            and str(row.get("evidence_kind") or "").upper() == "OBSERVED"
            and self._positive_number(row.get("amount"))
        ]
        managed = [
            row
            for row in self._store.list_namespace(MANAGED_ASSIGNMENT_NAMESPACE)
            if str(row.get("status") or "").upper() == "FULFILLED"
            and str(row.get("action_id") or "") in actions
            and self._managed_has_cost(row)
        ]
        decisions = [
            row
            for row in self._store.list_namespace(DISTRIBUTION_DECISION_NAMESPACE)
            if str(row.get("experiment_id")) in experiments
            and str(row.get("action") or "").upper() in {"STOP", "CONTINUE", "MODIFY", "SCALE"}
        ]
        reddit_obs = self._observation_action_ids(CUSTOMER_REDDIT_OBSERVATION_NAMESPACE)
        telegram_obs = self._observation_action_ids(CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE)
        spend_experiments = {str(row.get("experiment_id")) for row in spends}
        for row in managed:
            action = actions.get(str(row.get("action_id") or ""))
            if action and action.get("experiment_id"):
                spend_experiments.add(str(action["experiment_id"]))
        outcome_experiments = {str(row.get("experiment_id")) for row in events}
        for action_id in reddit_obs | telegram_obs:
            action = actions.get(action_id)
            if action and action.get("experiment_id"):
                outcome_experiments.add(str(action["experiment_id"]))
        decision_experiments = {str(row.get("experiment_id")) for row in decisions}
        executed_experiments = {
            str(action.get("experiment_id"))
            for action in actions.values()
            if str(action.get("status") or "").upper() == "EXECUTED"
            and action.get("experiment_id")
        }
        complete_ids = (
            set(experiments)
            & spend_experiments
            & outcome_experiments
            & decision_experiments
            & executed_experiments
        )
        complete_decisions = [
            row for row in decisions if str(row.get("experiment_id")) in complete_ids
        ]
        checks = [
            self._check(
                "observed_real_cost",
                AcceptanceCheckKind.EVIDENCE,
                bool(spend_experiments),
                "Observed cost evidence exists; estimates/synthetic costs do not count."
                if spend_experiments
                else "No positive OBSERVED cost or fulfilled managed cost exists for scoped experiments.",
                count=len(spend_experiments),
            ),
            self._check(
                "observed_outcome",
                AcceptanceCheckKind.EVIDENCE,
                bool(outcome_experiments),
                "Persisted funnel/community outcome evidence exists."
                if outcome_experiments
                else "No persisted analytics/platform outcome exists for scoped experiments.",
                count=len(outcome_experiments),
            ),
            self._check(
                "measured_next_decision",
                AcceptanceCheckKind.EVIDENCE,
                bool(decisions),
                "A persisted STOP/CONTINUE/MODIFY/SCALE decision exists."
                if decisions
                else "No persisted Growth Manager decision exists for scoped experiments.",
                count=len(decisions),
                latest=self._latest(decisions, ("created_at",)),
            ),
            self._check(
                "same_experiment_economics_outcome_decision_chain",
                AcceptanceCheckKind.EVIDENCE,
                bool(complete_ids),
                "The same executed experiment has observed cost, outcome and measured next decision."
                if complete_ids
                else (
                    "No single executed experiment currently has observed cost + outcome + "
                    "decision evidence."
                ),
                count=len(complete_ids),
                sample=self._decision_sample(complete_decisions[0]) if complete_decisions else {},
            ),
        ]
        return self._phase(
            255,
            7,
            "Economics and learning loop",
            production_eligible,
            checks,
        )

    def _phase(
        self,
        issue_number: int,
        phase: int,
        name: str,
        production_eligible: bool,
        checks: list[AcceptanceCheckView],
    ) -> CommunityDistributionPhaseAcceptanceView:
        required = [check for check in checks if check.required_for_close]
        evidence_complete = bool(required) and all(check.satisfied for check in required)
        readiness = [check for check in checks if check.kind == AcceptanceCheckKind.READINESS]
        current_ready = production_eligible and all(check.satisfied for check in readiness)
        production_verified = production_eligible and evidence_complete
        if production_verified:
            state = AcceptanceState.VERIFIED
        elif not production_eligible:
            state = AcceptanceState.BLOCKED
        elif any(
            check.satisfied
            for check in required
            if check.kind in {AcceptanceCheckKind.EVIDENCE, AcceptanceCheckKind.POLICY}
        ):
            state = AcceptanceState.PARTIAL
        elif current_ready:
            state = AcceptanceState.READY_FOR_REAL_RUN
        else:
            state = AcceptanceState.BLOCKED
        blockers = [check.detail for check in checks if check.required_for_close and not check.satisfied]
        if not production_eligible:
            blockers.insert(0, "Report is not running in an eligible durable production runtime.")
        return CommunityDistributionPhaseAcceptanceView(
            issue_number=issue_number,
            phase=phase,
            name=name,
            state=state,
            current_ready=current_ready,
            evidence_complete=evidence_complete,
            production_verified=production_verified,
            blockers=blockers,
            checks=checks,
        )

    def _production_environment(self) -> tuple[bool, list[str]]:
        blockers: list[str] = []
        if self._settings.app_env.strip().lower() != "production":
            blockers.append("APP_ENV is not production")
        if self._settings.runtime_storage.strip().lower() != "database":
            blockers.append("RUNTIME_STORAGE is not database")
        if self._store.ephemeral:
            blockers.append("RuntimeStateStore is ephemeral")
        release = self._settings.partizan_release_sha.strip().lower()
        if not release or release == "unknown":
            blockers.append("PARTIZAN_RELEASE_SHA is not a concrete release")
        return not blockers, blockers

    def _scope(self, project_id: UUID | None) -> tuple[dict | None, UUID | None]:
        if project_id is None:
            return None, None
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CommunityDistributionAcceptanceError("Customer project not found")
        raw_product_id = project.get("product_id")
        if not raw_product_id:
            raise CommunityDistributionAcceptanceError("Customer project has no confirmed product")
        try:
            return project, UUID(str(raw_product_id))
        except ValueError as exc:
            raise CommunityDistributionAcceptanceError("Customer project product id is invalid") from exc

    def _projects(self) -> list[dict]:
        return self._store.list_namespace(CUSTOMER_PROJECT_NAMESPACE)

    def _project_ids_for_scope(
        self,
        project_id: UUID | None,
        product_id: UUID | None,
        projects: list[dict],
    ) -> set[str]:
        if project_id is not None:
            return {str(project_id)}
        if product_id is not None:
            return {
                str(row.get("id"))
                for row in projects
                if str(row.get("product_id")) == str(product_id) and row.get("id")
            }
        return {str(row.get("id")) for row in projects if row.get("id")}

    def _experiments(self, product_id: UUID | None) -> dict[str, dict]:
        return {
            str(row.get("id")): row
            for row in self._store.list_namespace(DISTRIBUTION_EXPERIMENT_NAMESPACE)
            if row.get("id") and (product_id is None or str(row.get("product_id")) == str(product_id))
        }

    def _actions(self, experiments: dict[str, dict]) -> dict[str, dict]:
        experiment_ids = set(experiments)
        return {
            str(row.get("id")): row
            for row in self._store.list_namespace(DISTRIBUTION_ACTION_NAMESPACE)
            if row.get("id") and str(row.get("experiment_id")) in experiment_ids
        }

    def _opportunities(
        self,
        product_id: UUID | None,
        experiments: dict[str, dict],
    ) -> list[dict]:
        if product_id is None:
            return self._store.list_namespace(AUDIENCE_OPPORTUNITY_NAMESPACE)
        opportunity_ids = {
            str(row.get("opportunity_id"))
            for row in experiments.values()
            if row.get("opportunity_id")
        }
        map_payload = self._store.get(AUDIENCE_MAP_NAMESPACE, str(product_id))
        if map_payload and isinstance(map_payload.get("opportunities"), list):
            for item in map_payload["opportunities"]:
                if isinstance(item, dict) and item.get("id"):
                    opportunity_ids.add(str(item["id"]))
        rows = self._store.list_namespace(AUDIENCE_OPPORTUNITY_NAMESPACE)
        return [row for row in rows if str(row.get("id")) in opportunity_ids]

    def _events_for_experiments(self, experiment_ids: set[str]) -> list[dict]:
        return [
            row
            for row in self._store.list_namespace(DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE)
            if str(row.get("experiment_id")) in experiment_ids
        ]

    def _active_connections(
        self,
        namespace: str,
        project_ids: set[str],
        *,
        required_scopes: set[str] | None,
    ) -> list[dict]:
        rows: list[dict] = []
        for row in self._store.list_namespace(namespace):
            if str(row.get("status") or "").upper() != "ACTIVE":
                continue
            if project_ids and str(row.get("project_id")) not in project_ids:
                continue
            if required_scopes is not None:
                raw_scopes = row.get("scopes")
                if not isinstance(raw_scopes, (list, tuple, set)):
                    continue
                scopes = {str(item) for item in raw_scopes if item is not None}
                if not required_scopes.issubset(scopes):
                    continue
            rows.append(row)
        return rows

    def _executed_receipts(self, namespace: str, actions: dict[str, dict]) -> list[dict]:
        return [
            row
            for row in self._store.list_namespace(namespace)
            if str(row.get("outcome") or "").upper() == "EXECUTED"
            and str(row.get("action_id")) in actions
        ]

    def _telegram_research_ready(self) -> bool:
        return bool(
            self._settings.telegram_research_provider == "telethon"
            and self._settings.telegram_research_public_ready
            and self._settings.telegram_research_api_id is not None
            and self._settings.telegram_research_api_hash is not None
            and self._settings.telegram_research_session is not None
        )

    def _telegram_publish_ready(self) -> bool:
        return bool(
            self._settings.telegram_client_publish_provider == "telethon"
            and self._settings.telegram_client_publish_public_ready
            and self._settings.telegram_client_publish_api_id is not None
            and self._settings.telegram_client_publish_api_hash is not None
            and self._settings.provider_secret_encryption_key is not None
        )

    def _reddit_publish_ready(self) -> bool:
        return bool(
            self._settings.reddit_client_publish_provider == "oauth"
            and self._settings.reddit_client_publish_public_ready
            and self._settings.reddit_commercial_access_verified
            and self._settings.reddit_client_publish_client_id
            and self._settings.reddit_client_publish_client_secret is not None
            and self._settings.reddit_client_publish_user_agent
            and self._settings.partizan_public_base_url
            and self._settings.provider_secret_encryption_key is not None
        )

    def _real_search_ready(self) -> bool:
        return bool(
            self._settings.search_provider == "openai"
            and self._settings.openai_api_key
        )

    def _managed_distribution_ready(self) -> bool:
        if not self._settings.managed_distribution_public_ready:
            return False
        active_identity_ids = {
            str(row.get("id"))
            for row in self._store.list_namespace(DISTRIBUTION_IDENTITY_NAMESPACE)
            if str(row.get("status") or "").upper() == "ACTIVE"
        }
        return any(
            str(row.get("health") or "").upper() == "ELIGIBLE"
            and str(row.get("distribution_identity_id")) in active_identity_ids
            for row in self._store.list_namespace(MANAGED_PUBLISHER_NAMESPACE)
        )

    def _fresh_reddit_targets(self, opportunities: list[dict]) -> list[dict]:
        targets: list[dict] = []
        for row in opportunities:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            enrichment = (
                metadata.get("enrichment")
                if isinstance(metadata.get("enrichment"), dict)
                else {}
            )
            raw_targets = enrichment.get("action_targets")
            if not isinstance(raw_targets, list):
                continue
            for target in raw_targets:
                if isinstance(target, dict) and action_target_is_fresh(target):
                    targets.append(target)
        return targets

    def _current_indexed_reddit_policy(self, row: dict) -> bool:
        if str(row.get("source") or "") != "indexed_public_research":
            return False
        if str(row.get("research_status") or "").upper() not in {"PARTIAL", "VERIFIED"}:
            return False
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            return False
        checked_at = self._datetime(row.get("last_checked_at"))
        if checked_at is None:
            return False
        now = datetime.now(UTC)
        if checked_at > now + timedelta(minutes=5):
            return False
        if now - checked_at > REDDIT_POLICY_MAX_AGE:
            return False
        fresh_until = self._datetime(row.get("fresh_until"))
        if fresh_until is not None and now > fresh_until:
            return False
        return True

    @staticmethod
    def _reddit_partial_policy_remains_fail_closed(row: dict) -> bool:
        if str(row.get("research_status") or "").upper() != "PARTIAL":
            return True
        try:
            policy = CommunityPolicyView.model_validate(row)
        except ValueError:
            return False
        return policy_freshness_reason(policy) is not None

    def _observation_action_ids(self, namespace: str) -> set[str]:
        result: set[str] = set()
        for row in self._store.list_namespace(namespace):
            action_id = str(row.get("action_id") or "")
            if not action_id:
                continue
            if namespace == CUSTOMER_REDDIT_OBSERVATION_NAMESPACE:
                if self._reddit_observation_has_result(row):
                    result.add(action_id)
            elif self._telegram_observation_has_result(row):
                result.add(action_id)
        return result

    @staticmethod
    def _reddit_observation_has_result(row: dict) -> bool:
        history = row.get("history")
        return bool(
            isinstance(history, list)
            and any(
                isinstance(item, dict)
                and str(item.get("state") or "").upper()
                in {"PRESENT", "REMOVED", "INACCESSIBLE", "UNKNOWN"}
                and item.get("checked_at")
                for item in history
            )
        )

    @staticmethod
    def _telegram_observation_has_result(row: dict) -> bool:
        latest = row.get("latest")
        if isinstance(latest, dict):
            return bool(latest.get("checked_at") and latest.get("state"))
        history = row.get("history")
        return bool(
            isinstance(history, list)
            and any(
                isinstance(item, dict) and item.get("checked_at") and item.get("state")
                for item in history
            )
        )

    @staticmethod
    def _managed_has_cost(row: dict) -> bool:
        cost = row.get("cost")
        if not isinstance(cost, dict):
            return False
        return any(
            CommunityDistributionAcceptanceService._positive_number(cost.get(key))
            for key in (
                "distribution_spend_usd",
                "operational_cost_usd",
                "management_fee_usd",
            )
        )

    @staticmethod
    def _positive_number(value: object) -> bool:
        try:
            return float(value) > 0
        except (TypeError, ValueError):
            return False

    @staticmethod
    def _public_host(value: object) -> str:
        try:
            parts = urlsplit(str(value or ""))
        except ValueError:
            return ""
        return parts.netloc.lower().removeprefix("www.")

    @staticmethod
    def _check(
        key: str,
        kind: AcceptanceCheckKind,
        satisfied: bool,
        detail: str,
        *,
        count: int = 0,
        latest: datetime | None = None,
        sample: dict[str, Any] | None = None,
        required: bool = True,
    ) -> AcceptanceCheckView:
        return AcceptanceCheckView(
            key=key,
            kind=kind,
            satisfied=satisfied,
            required_for_close=required,
            detail=detail,
            evidence_count=count,
            latest_at=latest,
            sample=sample or {},
        )

    def _latest(self, rows: list[dict], path: tuple[str, ...]) -> datetime | None:
        values: list[datetime] = []
        for row in rows:
            value: object = row
            for key in path:
                if not isinstance(value, dict):
                    value = None
                    break
                value = value.get(key)
            parsed = self._datetime(value)
            if parsed is not None:
                values.append(parsed)
        return max(values) if values else None

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        if isinstance(value, datetime):
            parsed = value
        elif value:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    @staticmethod
    def _telegram_research_sample(row: dict) -> dict[str, Any]:
        metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return {
            "opportunity_id": str(row.get("id") or ""),
            "url": str(row.get("url") or ""),
            "telegram_entity_id": metadata.get("telegram_entity_id"),
            "source_checked_at": metadata.get("source_checked_at"),
        }

    @staticmethod
    def _reddit_research_sample(row: dict, policies: list[dict]) -> dict[str, Any]:
        opportunity_id = str(row.get("id") or "")
        policy = next(
            (item for item in policies if str(item.get("opportunity_id")) == opportunity_id),
            {},
        )
        return {
            "opportunity_id": opportunity_id,
            "url": str(row.get("url") or ""),
            "policy_source": policy.get("source"),
            "policy_status": policy.get("research_status"),
            "policy_checked_at": policy.get("last_checked_at"),
        }

    @staticmethod
    def _target_sample(row: dict) -> dict[str, Any]:
        return {
            "url": str(row.get("url") or ""),
            "freshness_status": row.get("freshness_status"),
            "checked_at": row.get("checked_at"),
        }

    @staticmethod
    def _publish_sample(row: dict) -> dict[str, Any]:
        return {
            "action_id": str(row.get("action_id") or ""),
            "outcome": row.get("outcome"),
            "executed_url": str(row.get("executed_url") or ""),
            "published_at": row.get("published_at"),
        }

    @staticmethod
    def _managed_sample(row: dict) -> dict[str, Any]:
        cost = row.get("cost") if isinstance(row.get("cost"), dict) else {}
        return {
            "assignment_id": str(row.get("id") or ""),
            "action_id": str(row.get("action_id") or ""),
            "platform": row.get("platform"),
            "action_type": row.get("action_type"),
            "executed_url": str(row.get("executed_url") or ""),
            "fulfilled_at": row.get("fulfilled_at"),
            "customer_cost_recorded": any(
                CommunityDistributionAcceptanceService._positive_number(cost.get(key))
                for key in ("distribution_spend_usd", "management_fee_usd")
            ),
            "operating_cost_recorded": CommunityDistributionAcceptanceService._positive_number(
                cost.get("operational_cost_usd")
            ),
        }

    @staticmethod
    def _decision_sample(row: dict) -> dict[str, Any]:
        return {
            "experiment_id": str(row.get("experiment_id") or ""),
            "action": row.get("action"),
            "publisher_mode": row.get("publisher_mode"),
            "action_type": row.get("action_type"),
            "created_at": row.get("created_at"),
        }


community_distribution_acceptance_service = CommunityDistributionAcceptanceService()
