from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    ExecutionAdapterRegistry,
    MetaAdsExecutionAdapter,
    distribution_execution_adapter_service,
)
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.meta_marketing_api import HttpxMetaMarketingApiClient, MetaMarketingApiError
from app.paid_campaign import paid_campaign_spec_service
from app.paid_provider_connections import (
    PaidProviderConnectionCreateRequest,
    paid_provider_connection_service,
)
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store

client = TestClient(app)


class FakeSecretResolver:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def resolve(self, name: str) -> str | None:
        assert name == "META_TEST_TOKEN"
        return self.value


class FakeMetaClient:
    def __init__(self, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, dict]] = []

    def _record(self, step: str, kwargs: dict, identifier: str) -> str:
        self.calls.append((step, kwargs))
        if self.fail_at == step:
            raise MetaMarketingApiError(f"provider failed at {step}")
        return identifier

    def create_campaign(self, **kwargs) -> str:
        return self._record("campaign", kwargs, "cmp_123")

    def create_ad_set(self, **kwargs) -> str:
        return self._record("adset", kwargs, "set_123")

    def create_ad_creative(self, **kwargs) -> str:
        return self._record("creative", kwargs, "creative_123")

    def create_ad(self, **kwargs) -> str:
        return self._record("ad", kwargs, "ad_123")


@pytest.fixture(autouse=True)
def reset_state() -> None:
    product_intake_service.reset()
    icp_service.reset()
    channel_service.reset()
    audience_intelligence_service.reset()
    growth_play_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_execution_adapter_service.reset()
    paid_campaign_spec_service.reset()
    paid_provider_connection_service.reset()


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


def _approved_instagram_paid_action(product_id: str) -> str:
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    play = next(
        item for item in plays.json()["plays"] if item["tactic_id"] == "instagram_ads"
    )
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = prepared.json()["action"]["id"]
    approved = client.post(f"/v1/distribution-actions/{action_id}/approve")
    assert approved.status_code == 200
    return action_id


def _connect_meta(product_id: str) -> None:
    paid_provider_connection_service.upsert_meta(
        UUID(product_id),
        PaidProviderConnectionCreateRequest(
            ad_account_id="act_123456789",
            page_id="page_123",
            instagram_actor_id="ig_123",
            access_token_env="META_TEST_TOKEN",
            api_version="v99.0",
            country_codes=["US"],
            default_image_url="https://cdn.example.com/oracle-ad.jpg",
            budget_minor_unit_factor=100,
            test_days=5,
        ),
    )


def _service(fake_client: FakeMetaClient, secret: str | None) -> DistributionExecutionAdapterService:
    adapter = MetaAdsExecutionAdapter(
        client=fake_client,
        secret_resolver=FakeSecretResolver(secret),
        connection_service=paid_provider_connection_service,
        spec_service=paid_campaign_spec_service,
    )
    return DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([adapter]),
        store=get_runtime_store(),
    )


def test_meta_provider_creates_paused_stack_and_keeps_experiment_approved() -> None:
    product_id = _product()
    action_id = _approved_instagram_paid_action(product_id)
    _connect_meta(product_id)
    fake = FakeMetaClient()
    service = _service(fake, "top-secret-token")

    result = service.execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(),
    )

    assert result.receipt.outcome == AdapterExecutionOutcome.STAGED
    assert result.plan.action.status.value == "APPROVED"
    assert result.plan.experiment.status.value == "APPROVED"
    assert [step for step, _ in fake.calls] == ["campaign", "adset", "creative", "ad"]
    assert result.receipt.metadata["provider_ids"] == {
        "campaign_id": "cmp_123",
        "ad_set_id": "set_123",
        "creative_id": "creative_123",
        "ad_id": "ad_123",
    }
    assert result.receipt.metadata["spend_started"] is False
    assert result.receipt.metadata["all_spend_objects_status"] == "PAUSED"
    assert "top-secret-token" not in result.receipt.model_dump_json()

    repeated = service.execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(retry=True),
    )
    assert repeated.receipt.outcome == AdapterExecutionOutcome.STAGED
    assert len(fake.calls) == 4


def test_meta_provider_is_unavailable_without_connection_or_secret() -> None:
    product_id = _product()
    action_id = _approved_instagram_paid_action(product_id)
    fake = FakeMetaClient()

    without_connection = _service(fake, "top-secret-token").execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(),
    )
    assert without_connection.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert not fake.calls

    get_runtime_store().clear_namespace("distribution_execution_adapter_receipt")
    _connect_meta(product_id)
    without_secret = _service(fake, None).execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(),
    )
    assert without_secret.receipt.outcome == AdapterExecutionOutcome.UNAVAILABLE
    assert not fake.calls


def test_partial_meta_failure_preserves_ids_and_blocks_blind_retry() -> None:
    product_id = _product()
    action_id = _approved_instagram_paid_action(product_id)
    _connect_meta(product_id)
    fake = FakeMetaClient(fail_at="creative")
    service = _service(fake, "top-secret-token")

    result = service.execute(
        UUID(action_id),
        DistributionAdapterExecuteRequest(),
    )

    assert result.receipt.outcome == AdapterExecutionOutcome.FAILED
    assert result.plan.action.status.value == "APPROVED"
    assert result.plan.experiment.status.value == "APPROVED"
    assert result.receipt.requires_operator_confirmation is True
    assert result.receipt.metadata["partial_provider_ids"] == {
        "campaign_id": "cmp_123",
        "ad_set_id": "set_123",
    }
    assert result.receipt.metadata["spend_started"] is False

    with pytest.raises(ValueError, match="reconcile"):
        service.execute(
            UUID(action_id),
            DistributionAdapterExecuteRequest(retry=True),
        )


def test_meta_http_client_sends_paused_objects_and_explicit_geo(monkeypatch) -> None:
    requests: list[tuple[str, dict]] = []

    class Response:
        status_code = 200

        def __init__(self, identifier: str) -> None:
            self.identifier = identifier

        def json(self) -> dict[str, str]:
            return {"id": self.identifier}

    identifiers = iter(["cmp", "set", "creative", "ad"])

    def fake_post(url, *, data, headers, timeout):
        assert headers == {"Authorization": "Bearer token-value"}
        assert timeout == 20.0
        requests.append((url, dict(data)))
        return Response(next(identifiers))

    monkeypatch.setattr("app.meta_marketing_api.httpx.post", fake_post)
    connection = paid_provider_connection_service.upsert_meta(
        UUID("11111111-1111-1111-1111-111111111111"),
        PaidProviderConnectionCreateRequest(
            ad_account_id="123",
            page_id="456",
            instagram_actor_id="789",
            access_token_env="META_TEST_TOKEN",
            api_version="v99.0",
            country_codes=["US", "GB"],
            default_image_url="https://cdn.example.com/image.jpg",
        ),
    )
    meta = HttpxMetaMarketingApiClient()

    campaign_id = meta.create_campaign(
        connection=connection,
        access_token="token-value",
        name="campaign",
    )
    ad_set_id = meta.create_ad_set(
        connection=connection,
        access_token="token-value",
        campaign_id=campaign_id,
        name="ad set",
        daily_budget_minor_units=4000,
    )
    creative_id = meta.create_ad_creative(
        connection=connection,
        access_token="token-value",
        name="creative",
        destination_url="https://example.com/tracked",
        primary_text="Useful product context",
        headline="Oracle",
    )
    meta.create_ad(
        connection=connection,
        access_token="token-value",
        ad_set_id=ad_set_id,
        creative_id=creative_id,
        name="ad",
    )

    assert requests[0][1]["status"] == "PAUSED"
    assert requests[1][1]["status"] == "PAUSED"
    assert requests[3][1]["status"] == "PAUSED"
    assert '"countries": ["US", "GB"]' in requests[1][1]["targeting"]
    assert requests[1][1]["daily_budget"] == "4000"
    assert all("token-value" not in url for url, _ in requests)
