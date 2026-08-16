import json
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
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.tiktok_marketing_api import (
    HttpxTikTokMarketingApiClient,
    TikTokCampaignInsights,
    TikTokCampaignState,
    TikTokMarketingApiError,
)
from app.tiktok_paid_control import TikTokPaidControlService, tiktok_paid_control_service
from app.tiktok_paid_provider import tiktok_paid_provider_connection_service

client = TestClient(app)


class StaticSecretResolver:
    def resolve(self, name: str) -> str | None:
        return "secret-token" if name == "TIKTOK_TEST_TOKEN" else None


class FakeTikTokControlClient:
    def __init__(
        self,
        *,
        spend: float = 0,
        operation_status: str = "ENABLE",
        fail_pause: bool = False,
    ) -> None:
        self.state = TikTokCampaignState(
            campaign_id="cmp-1",
            operation_status=operation_status,
            primary_status="STATUS_DELIVERING",
            secondary_status=None,
        )
        self.insights = TikTokCampaignInsights(
            campaign_id="cmp-1",
            spend=spend,
            impressions=100,
            clicks=10,
            currency=None,
        )
        self.fail_pause = fail_pause
        self.status_calls: list[tuple[str, str]] = []

    def get_campaign_state(self, **kwargs) -> TikTokCampaignState:
        return self.state

    def get_campaign_insights(self, **kwargs) -> TikTokCampaignInsights:
        return self.insights

    def set_campaign_status(
        self,
        *,
        campaign_id: str,
        operation_status: str,
        **kwargs,
    ) -> None:
        self.status_calls.append((campaign_id, operation_status))
        if self.fail_pause:
            raise TikTokMarketingApiError("pause failed")
        if campaign_id == "cmp-1" and operation_status == "DISABLE":
            self.state = TikTokCampaignState(
                campaign_id="cmp-1",
                operation_status="DISABLE",
                primary_status="STATUS_NOT_DELIVER",
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
    tiktok_paid_provider_connection_service.reset()
    tiktok_paid_control_service.reset()


def _tiktok_action(*, running: bool, budget: float = 50) -> tuple[str, UUID, UUID]:
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
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == "tiktok_ads")
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
        f"/v1/products/{product_id}/paid-provider-connections/tiktok",
        json={
            "advertiser_id": "adv-123",
            "access_token_env": "TIKTOK_TEST_TOKEN",
            "api_version": "v1.3",
            "location_ids": ["6252001"],
            "video_id": "video-123",
            "identity_id": "identity-123",
            "identity_type": "CUSTOMIZED_USER",
            "billing_event": "CPC",
            "optimization_goal": "CLICK",
            "pacing": "PACING_MODE_SMOOTH",
            "budget_mode": "BUDGET_MODE_DAY",
            "schedule_type": "SCHEDULE_FROM_NOW",
            "report_type": "TEST_REPORT_TYPE",
            "report_data_level": "TEST_CAMPAIGN_LEVEL",
            "test_days": 5,
        },
    )
    assert connection.status_code == 200

    receipt = ExecutionAdapterReceipt(
        action_id=action_id,
        adapter_name="tiktok-ads-create-disabled",
        provider="tiktok-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        message="TikTok objects staged",
        external_reference="tiktok:ad:ad-1",
        metadata={
            "provider_ids": {
                "campaign_id": "cmp-1",
                "adgroup_id": "group-1",
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
                external_reference="tiktok:ad:ad-1",
                notes="Test TikTok provider activation",
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


def _service(fake: FakeTikTokControlClient) -> TikTokPaidControlService:
    return TikTokPaidControlService(
        store=get_runtime_store(),
        client=fake,
        secret_resolver=StaticSecretResolver(),
        connection_service=tiktok_paid_provider_connection_service,
        spec_service=paid_campaign_spec_service,
        analytics_service=distribution_analytics_service,
    )


def test_cumulative_tiktok_spend_sync_is_idempotent_and_only_adds_delta() -> None:
    _, action_id, experiment_id = _tiktok_action(running=True, budget=50)
    fake = FakeTikTokControlClient(spend=10)
    service = _service(fake)

    first = service.sync(action_id)
    second = service.sync(action_id)
    fake.insights = TikTokCampaignInsights(
        campaign_id="cmp-1",
        spend=15,
        impressions=200,
        clicks=20,
    )
    third = service.sync(action_id)

    assert first.last_spend_delta == 10
    assert second.last_spend_delta == 0
    assert third.last_spend_delta == 5
    assert third.synced_spend == 15
    analytics = distribution_analytics_service.experiment_analytics(experiment_id)
    assert analytics.metrics.spend == 15


def test_tiktok_budget_cap_crossing_disables_campaign_once() -> None:
    _, action_id, experiment_id = _tiktok_action(running=True, budget=20)
    fake = FakeTikTokControlClient(spend=20)
    service = _service(fake)

    snapshot = service.sync(action_id)
    repeated = service.sync(action_id)

    assert snapshot.budget_guardrail_triggered is True
    assert snapshot.pause_state == "CONFIRMED"
    assert snapshot.pause_reason == "BUDGET_CAP"
    assert snapshot.operation_status == "DISABLE"
    assert fake.status_calls == [("cmp-1", "DISABLE")]
    assert distribution_analytics_service.experiment_analytics(experiment_id).metrics.spend == 20
    assert repeated.pause_state == "CONFIRMED"


def test_staged_but_enabled_tiktok_campaign_is_disabled_for_reconciliation() -> None:
    _, action_id, _ = _tiktok_action(running=False, budget=50)
    fake = FakeTikTokControlClient(spend=0, operation_status="ENABLE")
    service = _service(fake)

    snapshot = service.sync(action_id)

    assert snapshot.pause_reason == "RECONCILIATION"
    assert snapshot.pause_state == "CONFIRMED"
    assert snapshot.operation_status == "DISABLE"
    assert fake.status_calls == [("cmp-1", "DISABLE")]


def test_staged_tiktok_spend_is_not_silently_attributed() -> None:
    _, action_id, _ = _tiktok_action(running=False, budget=50)
    fake = FakeTikTokControlClient(spend=3, operation_status="ENABLE")
    service = _service(fake)

    snapshot = service.sync(action_id)

    assert snapshot.synced_spend == 0
    assert snapshot.pause_state == "CONFIRMED"
    assert "before the local paid experiment was RUNNING" in (snapshot.last_error or "")


def test_tiktok_emergency_pause_failure_never_claims_disabled() -> None:
    _, action_id, _ = _tiktok_action(running=True, budget=50)
    fake = FakeTikTokControlClient(spend=5, fail_pause=True)
    service = _service(fake)

    snapshot = service.pause(action_id)

    assert snapshot.pause_state == "UNKNOWN"
    assert snapshot.requires_reconciliation is True
    assert snapshot.operation_status == "ENABLE"
    assert "pause failed" in (snapshot.last_error or "")


def test_tiktok_control_snapshot_is_exposed_without_secrets() -> None:
    _, action_id, _ = _tiktok_action(running=True, budget=50)
    fake = FakeTikTokControlClient(spend=4)
    service = _service(fake)
    snapshot = service.sync(action_id)
    get_runtime_store().put(
        "tiktok_paid_control_snapshot",
        str(action_id),
        snapshot.model_dump(mode="json"),
    )

    response = client.get(f"/v1/distribution-actions/{action_id}/paid-campaign/tiktok/control")

    assert response.status_code == 200
    assert response.json()["provider_spend"] == 4
    assert "secret-token" not in response.text
    assert "TIKTOK_TEST_TOKEN" not in response.text


def test_tiktok_http_reads_campaign_state_and_lifetime_report(monkeypatch) -> None:
    requests: list[tuple[str, dict, dict]] = []

    class Response:
        status_code = 200

        def __init__(self, payload: dict) -> None:
            self.payload = payload

        def json(self) -> dict:
            return self.payload

    responses = iter(
        [
            Response(
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {
                                "campaign_id": "cmp-1",
                                "operation_status": "ENABLE",
                                "primary_status": "STATUS_DELIVERING",
                            }
                        ]
                    },
                }
            ),
            Response(
                {
                    "code": 0,
                    "data": {
                        "list": [
                            {
                                "dimensions": {"campaign_id": "cmp-1"},
                                "metrics": {
                                    "spend": "12.34",
                                    "impressions": "1000",
                                    "clicks": "42",
                                },
                            }
                        ]
                    },
                }
            ),
        ]
    )

    def fake_get(url, *, params, headers, timeout):
        assert headers == {"Access-Token": "token-value"}
        assert timeout == 20.0
        requests.append((url, dict(params), dict(headers)))
        return next(responses)

    monkeypatch.setattr("app.tiktok_marketing_api.httpx.get", fake_get)
    product_id, _, _ = _tiktok_action(running=True, budget=50)
    connection = tiktok_paid_provider_connection_service.get(UUID(product_id))
    assert connection is not None
    api = HttpxTikTokMarketingApiClient()

    state = api.get_campaign_state(
        connection=connection,
        access_token="token-value",
        campaign_id="cmp-1",
    )
    insights = api.get_campaign_insights(
        connection=connection,
        access_token="token-value",
        campaign_id="cmp-1",
    )

    assert state.operation_status == "ENABLE"
    assert insights.spend == 12.34
    assert insights.impressions == 1000
    assert insights.clicks == 42
    assert requests[0][0].endswith("/campaign/get/")
    campaign_filter = json.loads(requests[0][1]["filtering"])
    assert campaign_filter["campaign_ids"] == ["cmp-1"]
    assert requests[1][0].endswith("/report/integrated/get/")
    assert requests[1][1]["report_type"] == "TEST_REPORT_TYPE"
    assert requests[1][1]["data_level"] == "TEST_CAMPAIGN_LEVEL"
    assert requests[1][1]["query_lifetime"] == "true"
    report_filter = json.loads(requests[1][1]["filtering"])[0]
    assert report_filter["field_name"] == "campaign_ids"
    assert json.loads(report_filter["filter_value"]) == ["cmp-1"]
    assert all("token-value" not in url for url, _, _ in requests)
