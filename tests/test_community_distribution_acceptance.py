from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import SecretStr

from app.audience_intelligence_service import (
    AUDIENCE_MAP_NAMESPACE,
    AUDIENCE_OPPORTUNITY_NAMESPACE,
)
from app.community_distribution_acceptance import (
    AcceptanceState,
    CommunityDistributionAcceptanceService,
)
from app.config import Settings
from app.customer_funnel import CUSTOMER_PROJECT_NAMESPACE
from app.distribution_analytics_service import (
    DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
    DISTRIBUTION_SPEND_NAMESPACE,
)
from app.distribution_control_plane_service import COMMUNITY_POLICY_NAMESPACE
from app.distribution_execution_service import (
    DISTRIBUTION_ACTION_NAMESPACE,
    DISTRIBUTION_EXPERIMENT_NAMESPACE,
)
from app.distribution_growth_manager_service import DISTRIBUTION_DECISION_NAMESPACE
from app.managed_distribution import MANAGED_ASSIGNMENT_NAMESPACE
from app.reddit_client_publishing import (
    CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
    CUSTOMER_REDDIT_OBSERVATION_NAMESPACE,
    CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_governance import CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE
from app.telegram_client_publishing import (
    CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
    CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
)


def _phase(report, issue_number: int):
    return next(item for item in report.phases if item.issue_number == issue_number)


def _check(phase, key: str):
    return next(item for item in phase.checks if item.key == key)


def _settings(**updates) -> Settings:
    defaults = {
        "app_env": "local",
        "runtime_storage": "memory",
        "partizan_release_sha": "test-release",
    }
    defaults.update(updates)
    return Settings(**defaults)


def _scope(store: MemoryRuntimeStateStore) -> tuple[UUID, UUID]:
    project_id = uuid4()
    product_id = uuid4()
    store.put(
        CUSTOMER_PROJECT_NAMESPACE,
        str(project_id),
        {
            "id": str(project_id),
            "product_id": str(product_id),
            "customer_token_hash": "do-not-leak-token-hash",
        },
    )
    return project_id, product_id


def _execution(
    store: MemoryRuntimeStateStore,
    product_id: UUID,
    *,
    platform: str = "REDDIT",
) -> tuple[UUID, UUID, UUID]:
    opportunity_id = uuid4()
    action_id = uuid4()
    experiment_id = uuid4()
    store.put(
        DISTRIBUTION_EXPERIMENT_NAMESPACE,
        str(experiment_id),
        {
            "id": str(experiment_id),
            "product_id": str(product_id),
            "opportunity_id": str(opportunity_id),
            "action_id": str(action_id),
        },
    )
    store.put(
        DISTRIBUTION_ACTION_NAMESPACE,
        str(action_id),
        {
            "id": str(action_id),
            "platform": platform,
            "opportunity_id": str(opportunity_id),
            "experiment_id": str(experiment_id),
            "status": "EXECUTED",
        },
    )
    return opportunity_id, action_id, experiment_id


def test_ephemeral_or_non_production_runtime_can_never_be_production_verified() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _scope(store)
    _, action_id, _ = _execution(store, product_id, platform="TELEGRAM")
    store.put(
        CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "outcome": "EXECUTED",
            "published_at": datetime.now(UTC).isoformat(),
            "executed_url": "https://t.me/example/42",
        },
    )
    store.put(
        CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "latest": {"state": "PRESENT", "checked_at": datetime.now(UTC).isoformat()},
        },
    )

    report = CommunityDistributionAcceptanceService(
        store=store,
        settings=_settings(
            app_env="production",
            runtime_storage="database",
            partizan_release_sha="abc123",
        ),
    ).report(project_id)

    assert report.production_environment_eligible is False
    assert "RuntimeStateStore is ephemeral" in report.environment_blockers
    assert all(phase.production_verified is False for phase in report.phases)
    assert _phase(report, 251).evidence_complete is True
    assert _phase(report, 251).state == AcceptanceState.BLOCKED


def test_report_never_serializes_connection_secrets_or_account_names() -> None:
    store = MemoryRuntimeStateStore()
    project_id, _ = _scope(store)
    store.put(
        CUSTOMER_TELEGRAM_CONNECTION_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "status": "ACTIVE",
            "secret_reference": "tgs1-super-secret-reference",
            "username": "private_telegram_identity",
        },
    )
    store.put(
        CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "status": "ACTIVE",
            "secret_reference": "rdt1-super-secret-reference",
            "username": "private_reddit_identity",
            "scopes": ["identity", "read", "submit"],
        },
    )

    report = CommunityDistributionAcceptanceService(store=store, settings=_settings()).report(
        project_id
    )
    serialized = report.model_dump_json()

    assert "tgs1-super-secret-reference" not in serialized
    assert "rdt1-super-secret-reference" not in serialized
    assert "private_telegram_identity" not in serialized
    assert "private_reddit_identity" not in serialized
    assert "do-not-leak-token-hash" not in serialized


def test_phase2_and_phase4_recognize_only_real_research_provenance() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _scope(store)
    telegram_id = uuid4()
    reddit_id = uuid4()
    checked_at = datetime.now(UTC).isoformat()
    telegram = {
        "id": str(telegram_id),
        "platform": "TELEGRAM",
        "url": "https://t.me/publiccommunity",
        "metadata": {
            "native_research_status": "VERIFIED",
            "telegram_entity_id": 123456,
            "source_checked_at": checked_at,
        },
    }
    reddit = {
        "id": str(reddit_id),
        "platform": "REDDIT",
        "url": "https://www.reddit.com/r/example/",
        "metadata": {"enrichment": {"action_targets": []}},
    }
    store.put(AUDIENCE_OPPORTUNITY_NAMESPACE, str(telegram_id), telegram)
    store.put(AUDIENCE_OPPORTUNITY_NAMESPACE, str(reddit_id), reddit)
    store.put(
        AUDIENCE_MAP_NAMESPACE,
        str(product_id),
        {
            "product_id": str(product_id),
            "opportunities": [telegram, reddit],
        },
    )
    store.put(
        COMMUNITY_POLICY_NAMESPACE,
        str(reddit_id),
        {
            "opportunity_id": str(reddit_id),
            "source": "manual_review",
            "research_status": "VERIFIED",
            "last_checked_at": checked_at,
            "evidence": [{"url": "https://www.reddit.com/r/example/about/rules"}],
        },
    )
    service = CommunityDistributionAcceptanceService(store=store, settings=_settings())

    first = service.report(project_id)
    assert _check(_phase(first, 250), "real_native_telegram_research").satisfied is True
    assert _check(
        _phase(first, 252), "real_reddit_research_and_verified_policy"
    ).satisfied is False

    store.put(
        COMMUNITY_POLICY_NAMESPACE,
        str(reddit_id),
        {
            "opportunity_id": str(reddit_id),
            "source": "indexed_public_research",
            "research_status": "VERIFIED",
            "last_checked_at": checked_at,
            "evidence": [{"url": "https://www.reddit.com/r/example/about/rules"}],
        },
    )
    second = service.report(project_id)
    reddit_check = _check(
        _phase(second, 252), "real_reddit_research_and_verified_policy"
    )
    assert reddit_check.satisfied is True
    assert reddit_check.sample["policy_source"] == "indexed_public_research"


def test_phase3_requires_same_action_publish_and_observation() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _scope(store)
    _, action_id, _ = _execution(store, product_id, platform="TELEGRAM")
    other_action_id = uuid4()
    store.put(
        CUSTOMER_TELEGRAM_PUBLISH_RECEIPT_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "outcome": "EXECUTED",
            "executed_url": "https://t.me/example/42",
            "published_at": datetime.now(UTC).isoformat(),
        },
    )
    store.put(
        CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE,
        str(other_action_id),
        {
            "action_id": str(other_action_id),
            "latest": {"state": "PRESENT", "checked_at": datetime.now(UTC).isoformat()},
        },
    )
    service = CommunityDistributionAcceptanceService(store=store, settings=_settings())

    first = service.report(project_id)
    check = _check(_phase(first, 251), "real_telegram_publish_and_observation")
    assert check.satisfied is False

    store.put(
        CUSTOMER_TELEGRAM_OBSERVATION_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "remote_message_id": 42,
            "target_username": "must-not-appear",
            "latest": {"state": "PRESENT", "checked_at": datetime.now(UTC).isoformat()},
        },
    )
    second = service.report(project_id)
    check = _check(_phase(second, 251), "real_telegram_publish_and_observation")
    assert check.satisfied is True
    assert check.sample["action_id"] == str(action_id)
    assert "must-not-appear" not in second.model_dump_json()


def test_phase5_requires_commercial_gate_connection_publish_observation_and_attribution() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _scope(store)
    _, action_id, experiment_id = _execution(store, product_id, platform="REDDIT")
    now = datetime.now(UTC).isoformat()
    store.put(
        CUSTOMER_REDDIT_CONNECTION_NAMESPACE,
        str(project_id),
        {
            "project_id": str(project_id),
            "status": "ACTIVE",
            "secret_reference": "rdt1-hidden",
            "scopes": ["identity", "read", "submit"],
        },
    )
    store.put(
        CUSTOMER_REDDIT_PUBLISH_RECEIPT_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "outcome": "EXECUTED",
            "executed_url": "https://www.reddit.com/r/example/comments/abc/post/",
            "published_at": now,
        },
    )
    store.put(
        CUSTOMER_REDDIT_OBSERVATION_NAMESPACE,
        str(action_id),
        {
            "action_id": str(action_id),
            "history": [{"state": "PRESENT", "reply_count": 1, "checked_at": now}],
        },
    )
    store.put(
        DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
        str(uuid4()),
        {
            "event_id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "event_type": "VISIT",
            "occurred_at": now,
        },
    )

    blocked = CommunityDistributionAcceptanceService(
        store=store,
        settings=_settings(reddit_commercial_access_verified=False),
    ).report(project_id)
    phase = _phase(blocked, 253)
    assert _check(phase, "reddit_end_to_end_evidence_chain").satisfied is True
    assert _check(phase, "reddit_commercial_api_access_verified").satisfied is False
    assert phase.evidence_complete is False

    allowed = CommunityDistributionAcceptanceService(
        store=store,
        settings=_settings(reddit_commercial_access_verified=True),
    ).report(project_id)
    assert _phase(allowed, 253).evidence_complete is True
    assert "rdt1-hidden" not in allowed.model_dump_json()


def test_phase6_and_phase7_require_same_real_evidence_chain_and_observed_cost() -> None:
    store = MemoryRuntimeStateStore()
    project_id, product_id = _scope(store)
    _, action_id, experiment_id = _execution(store, product_id, platform="REDDIT")
    assignment_id = uuid4()
    now = datetime.now(UTC).isoformat()
    store.put(
        MANAGED_ASSIGNMENT_NAMESPACE,
        str(assignment_id),
        {
            "id": str(assignment_id),
            "product_id": str(product_id),
            "managed_publisher_id": "must-not-leak-managed-publisher",
            "partner_reference": "must-not-leak-partner-reference",
            "action_id": str(action_id),
            "platform": "REDDIT",
            "action_type": "COMMENT",
            "status": "FULFILLED",
            "executed_url": "https://www.reddit.com/r/example/comments/abc/post/",
            "fulfilled_at": now,
            "cost": {
                "distribution_spend_usd": 0,
                "operational_cost_usd": 0,
                "management_fee_usd": 0,
            },
        },
    )
    store.put(
        DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
        str(uuid4()),
        {
            "event_id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "event_type": "REPLY",
            "occurred_at": now,
        },
    )
    store.put(
        DISTRIBUTION_DECISION_NAMESPACE,
        str(uuid4()),
        {
            "id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "action": "CONTINUE",
            "publisher_mode": "PARTIZAN_MANAGED",
            "action_type": "COMMENT",
            "created_at": now,
        },
    )
    store.put(
        DISTRIBUTION_SPEND_NAMESPACE,
        str(uuid4()),
        {
            "spend_id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "amount": 100,
            "category": "DISTRIBUTION_SPEND",
            "evidence_kind": "SYNTHETIC",
            "occurred_at": now,
        },
    )
    service = CommunityDistributionAcceptanceService(store=store, settings=_settings())

    first = service.report(project_id)
    phase7 = _phase(first, 255)
    assert _check(phase7, "observed_real_cost").satisfied is False
    assert _check(
        phase7, "same_experiment_economics_outcome_decision_chain"
    ).satisfied is False
    phase6 = _phase(first, 254)
    assert _check(phase6, "real_managed_fulfillment").satisfied is True
    assert _check(phase6, "managed_measurable_outcome").satisfied is True
    serialized = first.model_dump_json()
    assert "must-not-leak-managed-publisher" not in serialized
    assert "must-not-leak-partner-reference" not in serialized

    store.put(
        DISTRIBUTION_SPEND_NAMESPACE,
        str(uuid4()),
        {
            "spend_id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "amount": 12,
            "category": "DISTRIBUTION_SPEND",
            "evidence_kind": "OBSERVED",
            "occurred_at": now,
        },
    )
    second = service.report(project_id)
    phase7 = _phase(second, 255)
    assert _check(phase7, "observed_real_cost").satisfied is True
    chain = _check(phase7, "same_experiment_economics_outcome_decision_chain")
    assert chain.satisfied is True
    assert chain.sample["experiment_id"] == str(experiment_id)


def test_project_scope_does_not_count_another_products_evidence() -> None:
    store = MemoryRuntimeStateStore()
    scoped_project_id, _ = _scope(store)
    _, other_product_id = _scope(store)
    _, action_id, experiment_id = _execution(store, other_product_id, platform="REDDIT")
    now = datetime.now(UTC).isoformat()
    store.put(
        DISTRIBUTION_SPEND_NAMESPACE,
        str(uuid4()),
        {
            "spend_id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "amount": 10,
            "evidence_kind": "OBSERVED",
            "occurred_at": now,
        },
    )
    store.put(
        DISTRIBUTION_ANALYTICS_EVENT_NAMESPACE,
        str(uuid4()),
        {
            "event_id": str(uuid4()),
            "experiment_id": str(experiment_id),
            "event_type": "VISIT",
            "occurred_at": now,
        },
    )
    store.put(
        DISTRIBUTION_DECISION_NAMESPACE,
        str(uuid4()),
        {
            "experiment_id": str(experiment_id),
            "action": "CONTINUE",
            "created_at": now,
        },
    )
    assert action_id

    report = CommunityDistributionAcceptanceService(store=store, settings=_settings()).report(
        scoped_project_id
    )
    phase7 = _phase(report, 255)
    assert _check(phase7, "observed_real_cost").satisfied is False
    assert _check(phase7, "observed_outcome").satisfied is False
    assert _check(phase7, "measured_next_decision").satisfied is False
