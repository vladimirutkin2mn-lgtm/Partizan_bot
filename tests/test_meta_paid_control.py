from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_analytics_service import distribution_analytics_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_schemas import DistributionActionExecutionRequest
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    ExecutionAdapterReceipt,
    distribution_execution_adapter_service,
)
from app.icp_service import icp_service
from app.main import app
from app.meta_marketing_api import (
    MetaCampaignInsights,
    MetaCampaignState,
    MetaMarketingApiError,
)
from app.meta_paid_control import MetaPaidControlService, meta_paid_control_service
from app.paid_campaign import paid_campaign_spec_service
from app.paid_provider_connections import paid_provider_connection_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class StaticSecretResolver:
    def resolve(self, name: str) -> str | None:
        return "secret-token" if name == "META_TEST_TOKEN" else None


class FakeMetaControlClient:
    def __init__(
        self,
        *,
        spend: float = 0,
        configured_status: str = "ACTIVE",
        effective_status: str = "ACTIVE",
        fail_pause: bool = False,
    ) -> None:
        self.state = MetaCampaignState(
            campaign_id="cmp-1",
            configured_status=configured_status,
            effective_status=effective_status,
        )
        self.insights = MetaCampaignInsights(
            campaign_id="cmp-1",
            spend=spend,
            impressions=100,
            clicks=10,
            account_currency="USD",
        )
        self.fail_pause = fail_pause
        self.status_calls: list[tuple[str, str]] = []

    def get_campaign_state(self, **kwargs) -> MetaCampaignState:
        return self.state

    def get_campaign_insights(self, **kwargs) -> MetaCampaignInsights:
        return self.insights

    def set_status(self, *, object_id: str, status: str, **kwargs) -> None:
        self.status_calls.append((object_id, status))
        if self.fail_pause:
            raise MetaMarketingApiError("pause failed")
        if object_id == "cmp-1" and status == "PAUSED":
            self.state = MetaCampaignState(
                campaign_id="cmp-1",
                configured_status="PAUSED",
                effective_status="PAUSED",
            )


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_execution_adapter_service.reset()
    distribution_analytics_service.reset()
    paid_campaign_spec_service.reset()
    paid_provider_connection_service.reset()
    meta_paid_control_service.reset()


def _meta_action(*, running: bool, budget: float = 50) -> tuple[str, UUID, UUID]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\n"
                "Language: English\n"
                f"Budget: {budget}\n"
                "Max CAC: 5\n"
                "Goal: Acquire paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == "instagram_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = UUID(prepared.json()["action"]["id"])
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    action = distribution_execution_service.get_action(action_id)
    assert action.experiment_id is not None
    experiment_id = action.experiment_id

    connection = client.put(
        f"/v1/products/{product_id}/paid-provider-connections/meta",
        json={
            "ad_account_id": "act_123",
            "page_id": "456",
            "instagram_actor_id": "789",
            "access_token_env": "META_TEST_TOKEN",
            "api_version": "v24.0",
            "country_codes": ["US"],
            "default_image_url": "https://example.com/image.jpg",
            "test_days": 5,
        },
    )
    assert connection.status_code == 200

    receipt = ExecutionAdapterReceipt(
        action_id=action_id,
        adapter_name="meta-ads-create-paused",
        provider="meta-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        message="Meta objects staged",
        external_reference="meta:campaign:cmp-1",
        metadata={
            "provider_ids": {
                "campaign_id": "cmp-1",
                "ad_set_id": "set-1",
                "creative_id": "creative-1",
                "ad_id": "ad-1",
            },
            "spend_started": False,
            "spend_state": "PAUSED",
        },
        created_at=datetime.now(UTC),
    )
    get_runtime_store().put(
        EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
        str(action_id),
        receipt.model_dump(mode="json"),
    )

    if running:
        distribution_execution_service.mark_executed(
            action_id,
            DistributionActionExecutionRequest(
                external_reference="meta:campaign:cmp-1",
                notes="Test provider activation",
            ),
        )
        running_receipt = receipt.model_copy(
            update={
                "outcome": AdapterExecutionOutcome.EXECUTED,
                "metadata": {
                    **receipt.metadata,
                    "spend_started": True,
                    "spend_state": "ACTIVE",
                },
            }
        )
        get_runtime_store().put(
            EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
            str(action_id),
            running_receipt.model_dump(mode="json"),
        )

    return product_id, action_id, experiment_id


def _service(fake: FakeMetaControlClient) -> MetaPaidControlService:
    return MetaPaidControlService(
        store=get_runtime_store(),
        meta_client=fake,
        secret_resolver=StaticSecretResolver(),
    )


def test_cumulative_spend_sync_is_idempotent_and_only_adds_delta() -> None:
    _, action_id, experiment_id = _meta_action(running=True, budget=50)
    fake = FakeMetaControlClient(spend=10)
    service = _service(fake)

    first = service.sync(action_id)
    second = service.sync(action_id)
    fake.insights = MetaCampaignInsights(
        campaign_id="cmp-1",
        spend=15,
        impressions=200,
        clicks=20,
        account_currency="USD",
    )
    third = service.sync(action_id)

    assert first.last_spend_delta == 10
    assert second.last_spend_delta == 0
    assert third.last_spend_delta == 5
    assert third.synced_spend == 15
    analytics = distribution_analytics_service.experiment_analytics(experiment_id)
    assert analytics.metrics.spend == 15


def test_budget_cap_crossing_pauses_campaign_once_and_keeps_real_spend() -> None:
    _, action_id, experiment_id = _meta_action(running=True, budget=20)
    fake = FakeMetaControlClient(spend=20)
    service = _service(fake)

    snapshot = service.sync(action_id)
    repeated = service.sync(action_id)

    assert snapshot.budget_guardrail_triggered is True
    assert snapshot.pause_state == "CONFIRMED"
    assert snapshot.pause_reason == "BUDGET_CAP"
    assert fake.status_calls == [("cmp-1", "PAUSED")]
    assert distribution_analytics_service.experiment_analytics(experiment_id).metrics.spend == 20
    assert repeated.pause_state == "CONFIRMED"


def test_staged_but_active_campaign_is_paused_as_reconciliation_guardrail() -> None:
    _, action_id, _ = _meta_action(running=False, budget=50)
    fake = FakeMetaControlClient(spend=0, configured_status="ACTIVE")
    service = _service(fake)

    snapshot = service.sync(action_id)

    assert snapshot.pause_reason == "RECONCILIATION"
    assert snapshot.pause_state == "CONFIRMED"
    assert snapshot.provider_spend == 0
    assert fake.status_calls == [("cmp-1", "PAUSED")]


def test_staged_spend_is_flagged_and_not_written_to_running_analytics() -> None:
    _, action_id, _ = _meta_action(running=False, budget=50)
    fake = FakeMetaControlClient(spend=3, configured_status="ACTIVE")
    service = _service(fake)

    snapshot = service.sync(action_id)

    assert snapshot.synced_spend == 0
    assert snapshot.requires_reconciliation is False
    assert "before the local paid experiment was RUNNING" in (snapshot.last_error or "")
    assert snapshot.pause_state == "CONFIRMED"


def test_emergency_pause_failure_never_claims_campaign_is_paused() -> None:
    _, action_id, _ = _meta_action(running=True, budget=50)
    fake = FakeMetaControlClient(spend=5, fail_pause=True)
    service = _service(fake)

    snapshot = service.pause(action_id)

    assert snapshot.pause_state == "UNKNOWN"
    assert snapshot.requires_reconciliation is True
    assert snapshot.configured_status == "ACTIVE"
    assert "pause failed" in (snapshot.last_error or "")


def test_control_snapshot_is_persisted_and_exposed_without_secrets() -> None:
    _, action_id, _ = _meta_action(running=True, budget=50)
    fake = FakeMetaControlClient(spend=4)
    service = _service(fake)
    service.sync(action_id)

    response = client.get(f"/v1/distribution-actions/{action_id}/paid-campaign/meta/control")

    assert response.status_code == 200
    payload = response.json()
    assert payload["provider_spend"] == 4
    assert payload["account_currency"] == "USD"
    assert "secret-token" not in response.text
    assert "META_TEST_TOKEN" not in response.text
