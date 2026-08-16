from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    ExecutionAdapterRegistry,
    TikTokAdsExecutionAdapter,
    distribution_execution_adapter_service,
)
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.tiktok_marketing_api import HttpxTikTokMarketingApiClient, TikTokMarketingApiError
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionCreateRequest,
    tiktok_paid_provider_connection_service,
)

client = TestClient(app)


class FakeSecretResolver:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def resolve(self, name: str) -> str | None:
        assert name == "TIKTOK_TEST_TOKEN"
        return self.value


class FakeTikTokClient:
    def __init__(self, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, dict]] = []

    def _record(self, step: str, kwargs: dict, identifier: str) -> str:
        self.calls.append((step, kwargs))
        if self.fail_at == step:
            raise TikTokMarketingApiError(f"provider failed at {step}")
        return identifier

    def create_campaign(self, **kwargs) -> str:
        return self._record("campaign", kwargs, "cmp_123")

    def create_ad_group(self, **kwargs) -> str:
        return self._record("adgroup", kwargs, "group_123")

    def create_ad(self, **kwargs) -> str:
        return self._record("ad", kwargs, "ad_123")


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    audience_intelligence_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_execution_adapter_service.reset()
    paid_campaign_spec_service.reset()
    tiktok_paid_provider_connection_service.reset()


def _product() -> str:
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
                "Budget: 200\n"
                "Max CAC: 5\n"
                "Goal: Acquire 100 paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _approved_tiktok_paid_action(product_id: str) -> str:
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == "tiktok_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    return action_id


def _connect_tiktok(product_id: str) -> None:
    tiktok_paid_provider_connection_service.upsert(
        UUID(product_id),
        TikTokPaidProviderConnectionCreateRequest(
            advertiser_id="adv_123",
            access_token_env="TIKTOK_TEST_TOKEN",
            api_version="v1.3",
            location_ids=["6252001"],
            video_id="video_123",
            identity_id="identity_123",
            identity_type="CUSTOMIZED_USER",
            call_to_action="LEARN_MORE",
            placements=["PLACEMENT_TIKTOK"],
            languages=["en"],
            billing_event="CPC",
            optimization_goal="CLICK",
            pacing="PACING_MODE_SMOOTH",
            budget_mode="BUDGET_MODE_DAY",
            schedule_type="SCHEDULE_FROM_NOW",
            promotion_type="WEBSITE",
            test_days=5,
        ),
    )


def _service(
    fake_client: FakeTikTokClient,
    secret: str | None,
) -> DistributionExecutionAdapterService:
    adapter = TikTokAdsExecutionAdapter(
        client=fake_client,
        secret_resolver=FakeSecretResolver(secret),
        connection_service=tiktok_paid_provider_connection_service,
        spec_service=paid_campaign_spec_service,
    )
    return DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([adapter]),
        store=get_runtime_store(),
    )


def test_tiktok_provider_stages_disabled_stack_and_keeps_experiment_approved() -> None:
    product_id = _product()
    action_id = _approved_tiktok_paid_action(product_id)
    _connect_tiktok(product_id)
    fake = FakeTikTokClient()
    service = _service(fake, "provider-test-token")

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.STAGED
    assert result.plan.action.status.value == "APPROVED"
    assert result.plan.experiment.status.value == "APPROVED"
    assert [step for step, _ in fake.calls] == ["campaign", "adgroup", "ad"]
    assert result.receipt.metadata["provider_ids"] == {
        "campaign_id": "cmp_123",
        "adgroup_id": "group_123",
        "ad_id": "ad_123",
    }
    assert result.receipt.metadata["all_spend_objects_status"] == "DISABLE"
    assert result.receipt.metadata["spend_started"] is False
    assert "provider-test-token" not in result.receipt.model_dump_json()

    repeated = service.execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(retry=True),
    )
    assert repeated.receipt.outcome == AdapterExecutionOutcome.STAGED
    assert len(fake.calls) == 3


def test_tiktok_provider_is_unavailable_without_connection_or_secret() -> None:
    product_id = _product()
    action_id = _approved_tiktok_paid_action(product_id)
    fake = FakeTikTokClient()

    without_connection = _service(fake, "provider-test-token").execute(
        UUID(action_id), DistributionAdapterExecuteRequest()
    )
    assert without_connection.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert not fake.calls

    get_runtime_store().clear_namespace("distribution_execution_adapter_receipt")
    _connect_tiktok(product_id)
    without_secret = _service(fake, None).execute(
        UUID(action_id), DistributionAdapterExecuteRequest()
    )
    assert without_secret.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert not fake.calls


def test_partial_tiktok_failure_preserves_ids_and_blocks_blind_retry() -> None:
    product_id = _product()
    action_id = _approved_tiktok_paid_action(product_id)
    _connect_tiktok(product_id)
    fake = FakeTikTokClient(fail_at="ad")
    service = _service(fake, "provider-test-token")

    result = service.execute(UUID(action_id), DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.FAILED
    assert result.plan.action.status.value == "APPROVED"
    assert result.receipt.requires_operator_confirmation is True
    assert result.receipt.metadata["partial_provider_ids"] == {
        "campaign_id": "cmp_123",
        "adgroup_id": "group_123",
    }
    with pytest.raises(ValueError, match="reconcile"):
        service.execute(UUID(action_id), DistributionAdapterExecuteRequest(retry=True))


def test_tiktok_http_client_forces_disabled_delivery_and_explicit_targeting(monkeypatch) -> None:
    requests: list[tuple[str, dict, dict]] = []

    class Response:
        status_code = 200

        def __init__(self, data: dict) -> None:
            self.data = data

        def json(self) -> dict:
            return {"code": 0, "message": "OK", "data": self.data}

    responses = iter(
        [
            Response({"campaign_id": "cmp"}),
            Response({"adgroup_id": "group"}),
            Response({"ad_ids": ["ad"]}),
        ]
    )

    def fake_post(url, *, json, headers, timeout):
        assert headers == {"Access-Token": "token-value"}
        assert timeout == 20.0
        requests.append((url, dict(json), dict(headers)))
        return next(responses)

    monkeypatch.setattr("app.tiktok_marketing_api.httpx.post", fake_post)
    connection = tiktok_paid_provider_connection_service.upsert(
        UUID("11111111-1111-1111-1111-111111111111"),
        TikTokPaidProviderConnectionCreateRequest(
            advertiser_id="adv",
            access_token_env="TIKTOK_TEST_TOKEN",
            location_ids=["6252001", "6255148"],
            video_id="video",
            identity_id="identity",
            identity_type="CUSTOMIZED_USER",
            billing_event="CPC",
            optimization_goal="CLICK",
            pacing="PACING_MODE_SMOOTH",
            budget_mode="BUDGET_MODE_DAY",
            schedule_type="SCHEDULE_FROM_NOW",
        ),
    )
    api = HttpxTikTokMarketingApiClient()

    campaign = api.create_campaign(connection=connection, access_token="token-value", name="c")
    group = api.create_ad_group(
        connection=connection,
        access_token="token-value",
        campaign_id=campaign,
        name="g",
        daily_budget=20,
    )
    api.create_ad(
        connection=connection,
        access_token="token-value",
        adgroup_id=group,
        name="a",
        destination_url="https://example.com/tracked",
        ad_text="Useful context",
    )

    assert requests[0][1]["operation_status"] == "DISABLE"
    assert requests[1][1]["operation_status"] == "DISABLE"
    assert requests[1][1]["location_ids"] == ["6252001", "6255148"]
    assert requests[1][1]["budget"] == 20
    creative = requests[2][1]["creatives"][0]
    assert creative["operation_status"] == "DISABLE"
    assert creative["video_id"] == "video"
    assert creative["landing_page_url"] == "https://example.com/tracked"
    assert all("token-value" not in url for url, _, _ in requests)
