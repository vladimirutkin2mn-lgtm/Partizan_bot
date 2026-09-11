from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.audience_intelligence_service import (
    AUDIENCE_MAP_NAMESPACE,
    AUDIENCE_OPPORTUNITY_NAMESPACE,
)
from app.community_distribution_acceptance import CommunityDistributionAcceptanceService
from app.config import Settings
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_analytics_service import DISTRIBUTION_SPEND_NAMESPACE
from app.distribution_control_plane_service import COMMUNITY_POLICY_NAMESPACE
from app.distribution_execution_service import DISTRIBUTION_EXPERIMENT_NAMESPACE
from app.reddit_client_publishing import CUSTOMER_REDDIT_CONNECTION_NAMESPACE
from app.runtime_store import MemoryRuntimeStateStore


def _settings() -> Settings:
    return Settings(
        app_env="local",
        runtime_storage="memory",
        partizan_release_sha="test-release",
    )


def _phase(report, issue_number: int):
    return next(item for item in report.phases if item.issue_number == issue_number)


def _check(phase, key: str):
    return next(item for item in phase.checks if item.key == key)


def _project(store: MemoryRuntimeStateStore):
    project_id = uuid4()
    product_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
        },
    )
    return project_id, product_id


def test_malformed_reddit_scopes_are_ignored_without_crashing_report() -> None:
    store = MemoryRuntimeStateStore()
    project_id, _ = _project(store)
    service = CommunityDistributionAcceptanceService(store=store, settings=_settings())

    for malformed in (None, "identity read submit", {"identity": True}):
        store.put(
            CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
            str(project_id),
            {
                "project_id": str(project_id),
                "status": "ACTIVE",
                "scopes": malformed,
            },
        )
        phase = _phase(service.report(project_id), 253)
        assert _check(phase, "active_reddit_oauth_connection").satisfied is False

    store.put(
        CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "status": "ACTIVE",
            "scopes": ["identity", "read", "submit"],
        },
    )
    phase = _phase(service.report(project_id), 253)
    assert _check(phase, "active_reddit_oauth_connection").satisfied is True


def test_malformed_reddit_action_targets_are_ignored_without_crashing_report() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _project(store)
    opportunity_id = uuid4()
    checked_at = datetime.now(UTC).isoformat()
    service = CommunityDistributionAcceptanceService(store=store, settings=_settings())

    store.put(
        COMMUNITY_POLICY_NAMESPACE,
        str(opportunity_id),
        {
            "opportunity_id": str(opportunity_id),
            "source": "indexed_public_research",
            "research_status": "VERIFIED",
            "last_checked_at": checked_at,
            "evidence": [{"url": "https://www.reddit.com/r/example/about/rules"}],
        },
    )

    for malformed in (None, "not-a-list", {"url": "https://www.reddit.com/r/example/"}):
        opportunity = {
            "id": str(opportunity_id),
            "platform": "REDDIT",
            "url": "https://www.reddit.com/r/example/",
            "metadata": {"enrichment": {"action_targets": malformed}},
        }
        store.put(AUDIENCE_OPPORTUNITY_NAMESPACE, str(opportunity_id), opportunity)
        store.put(
            AUDIENCE_MAP_NAMESPACE,
            str(product_id),
            {
                "product_id": str(product_id),
                "opportunities": [opportunity],
            },
        )

        phase = _phase(service.report(project_id), 252)
        assert _check(phase, "real_reddit_indexed_policy_research").satisfied is True
        assert _check(phase, "fresh_reddit_thread_target").satisfied is False


def test_stale_partial_reddit_policy_does_not_satisfy_phase4_acceptance() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _project(store)
    opportunity_id = uuid4()
    policy_id = uuid4()
    opportunity = {
        "id": str(opportunity_id),
        "platform": "REDDIT",
        "url": "https://www.reddit.com/r/example/",
        "metadata": {"enrichment": {"action_targets": []}},
    }
    store.put(AUDIENCE_OPPORTUNITY_NAMESPACE, str(opportunity_id), opportunity)
    store.put(
        AUDIENCE_MAP_NAMESPACE,
        str(product_id),
        {"product_id": str(product_id), "opportunities": [opportunity]},
    )
    store.put(
        COMMUNITY_POLICY_NAMESPACE,
        str(opportunity_id),
        {
            "id": str(policy_id),
            "opportunity_id": str(opportunity_id),
            "source": "indexed_public_research",
            "research_status": "PARTIAL",
            "last_checked_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            "evidence": [{"url": "https://www.reddit.com/r/example/about/rules"}],
        },
    )

    phase = _phase(
        CommunityDistributionAcceptanceService(store=store, settings=_settings()).report(project_id),
        252,
    )

    assert _check(phase, "real_reddit_indexed_policy_research").satisfied is False
    assert _check(phase, "reddit_ambiguous_policy_fail_closed").satisfied is False
    assert phase.evidence_complete is False


def test_phase7_requires_explicit_observed_spend_evidence_kind() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _project(store)
    experiment_id = uuid4()
    spend_id = uuid4()
    service = CommunityDistributionAcceptanceService(store=store, settings=_settings())

    store.put(
        DISTRIBUTION_EXPERIMENT_NAMESPACE,
        str(experiment_id),
        {
            "id": str(experiment_id),
            "product_id": str(product_id),
        },
    )
    store.put(
        DISTRIBUTION_SPEND_NAMESPACE,
        str(spend_id),
        {
            "spend_id": str(spend_id),
            "experiment_id": str(experiment_id),
            "amount": 10,
        },
    )

    phase = _phase(service.report(project_id), 255)
    assert _check(phase, "observed_real_cost").satisfied is False

    store.put(
        DISTRIBUTION_SPEND_NAMESPACE,
        str(spend_id),
        {
            "spend_id": str(spend_id),
            "experiment_id": str(experiment_id),
            "amount": 10,
            "evidence_kind": "OBSERVED",
        },
    )
    phase = _phase(service.report(project_id), 255)
    assert _check(phase, "observed_real_cost").satisfied is True
