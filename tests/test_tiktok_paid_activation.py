from datetime import UTC, datetime
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    EXECUTION_ADAPTER_RECEIPT_NAMESPACE,
    AdapterExecutionOutcome,
    ExecutionAdapterReceipt,
    distribution_execution_adapter_service,
)
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.paid_campaign import paid_campaign_spec_service
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.tiktok_marketing_api import HttpxTikTokMarketingApiClient, TikTokMarketingApiError
from app.tiktok_paid_activation import (
    TikTokPaidActivationAuthorizationRequest,
    TikTokPaidActivationRequest,
    TikTokPaidActivationService,
    tiktok_paid_activation_service,
)
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionCreateRequest,
    tiktok_paid_provider_connection_service,
)

client = TestClient(app)


class StaticSecretResolver:
    def resolve(self, name: str) -> str | None:
        return "secret-token" if name == "TIKTOK_TEST_TOKEN" else None


class FakeTikTokStatusClient:
    def __init__(self, fail_at: str | None = None) -> None:
        self.fail_at = fail_at
        self.calls: list[tuple[str, str]] = []

    def _set(self, step: str, operation_status: str) -> None:
        self.calls.append((step, operation_status))
        if self.fail_at == step:
            raise TikTokMarketingApiError(f"failed at {step}")

    def set_ad_status(self, *, operation_status: str, **kwargs) -> None:
        self._set("ad", operation_status)

    def set_adgroup_status(self, *, operation_status: str, **kwargs) -> None:
        self._set("adgroup", operation_status)

    def set_campaign_status(self, *, operation_status: str, **kwargs) -> None:
        self._set("campaign", operation_status)


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
    tiktok_paid_provider_connection_service.reset()
    tiktok_paid_activation_service.reset()


def _setup() -> tuple[str, UUID, float]:
    response = client.post(
        "/v1/products",
        json={
            "brief": (
                "Product: Oracle\n"
                "Description: AI entertainment product with personalized relationship readings.\n"
                "Problem: People want clarity when relationships feel uncertain.\n"
                "Value proposition: Personalized readings available on demand.\n"
                "Market: US\nLanguage: English\nBudget: 200\nMax CAC: 5\nGoal: paid users"
            )
        },
    )
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate").json()["plays"]
    play = next(item for item in plays if item["tactic_id"] == "tiktok_ads")
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    action_id = UUID(prepared.json()["action"]["id"])
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
    tiktok_paid_provider_connection_service.upsert(
        UUID(product_id),
        TikTokPaidProviderConnectionCreateRequest(
            advertiser_id="adv",
            access_token_env="TIKTOK_TEST_TOKEN",
            location_ids=["6252001"],
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
    receipt = ExecutionAdapterReceipt(
        action_id=action_id,
        adapter_name="tiktok-ads-create-disabled",
        provider="tiktok-marketing-api",
        outcome=AdapterExecutionOutcome.STAGED,
        message="TikTok objects staged",
        external_reference="tiktok:ad:ad_1",
        metadata={
            "provider_ids": {
                "campaign_id": "cmp_1",
                "adgroup_id": "group_1",
                "ad_id": "ad_1",
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
    spec = paid_campaign_spec_service.get(action_id)
    assert spec is not None
    return product_id, action_id, spec.budget_cap


def _service(fake: FakeTikTokStatusClient) -> TikTokPaidActivationService:
    return TikTokPaidActivationService(
        store=get_runtime_store(),
        client=fake,
        secret_resolver=StaticSecretResolver(),
        connection_service=tiktok_paid_provider_connection_service,
        spec_service=paid_campaign_spec_service,
    )


def test_tiktok_activation_requires_exact_explicit_budget_confirmation() -> None:
    _, action_id, budget = _setup()
    service = _service(FakeTikTokStatusClient())

    with pytest.raises(ValueError, match="confirm_spend"):
        service.authorize(
            action_id,
            TikTokPaidActivationAuthorizationRequest(
                approved_budget_cap=budget,
                confirm_spend=False,
            ),
        )
    with pytest.raises(ValueError, match="exactly match"):
        service.authorize(
            action_id,
            TikTokPaidActivationAuthorizationRequest(
                approved_budget_cap=budget + 1,
                confirm_spend=True,
            ),
        )


def test_tiktok_activation_enables_ad_adgroup_campaign_and_starts_experiment() -> None:
    _, action_id, budget = _setup()
    fake = FakeTikTokStatusClient()
    service = _service(fake)
    auth = service.authorize(
        action_id,
        TikTokPaidActivationAuthorizationRequest(
            approved_budget_cap=budget,
            confirm_spend=True,
        ),
    )

    result = service.activate(action_id, TikTokPaidActivationRequest(authorization_id=auth.id))

    assert fake.calls == [("ad", "ENABLE"), ("adgroup", "ENABLE"), ("campaign", "ENABLE")]
    assert result.receipt.outcome == AdapterExecutionOutcome.EXECUTED
    assert result.plan.action.status.value == "EXECUTED"
    assert result.plan.experiment.status.value == "RUNNING"
    stored = service.get_authorization(auth.id)
    assert stored.attempted_at is not None
    assert stored.consumed_at is not None
    assert "secret-token" not in result.receipt.model_dump_json()


def test_tiktok_activation_failure_requires_reconciliation_and_cannot_reuse_auth() -> None:
    _, action_id, budget = _setup()
    fake = FakeTikTokStatusClient(fail_at="adgroup")
    service = _service(fake)
    auth = service.authorize(
        action_id,
        TikTokPaidActivationAuthorizationRequest(
            approved_budget_cap=budget,
            confirm_spend=True,
        ),
    )

    result = service.activate(action_id, TikTokPaidActivationRequest(authorization_id=auth.id))

    assert fake.calls == [("ad", "ENABLE"), ("adgroup", "ENABLE")]
    assert result.receipt.outcome == AdapterExecutionOutcome.STAGED
    assert result.receipt.metadata["requires_reconciliation"] is True
    assert result.receipt.metadata["spend_state"] == "NOT_STARTED"
    assert result.plan.action.status.value == "APPROVED"
    assert result.plan.experiment.status.value == "APPROVED"
    with pytest.raises(ValueError, match="already been attempted"):
        service.activate(action_id, TikTokPaidActivationRequest(authorization_id=auth.id))


def test_tiktok_status_http_payloads_use_enable_and_access_token_header(monkeypatch) -> None:
    requests: list[tuple[str, dict]] = []

    class Response:
        status_code = 200

        def json(self) -> dict:
            return {"code": 0, "message": "OK", "data": {}}

    def fake_post(url, *, json, headers, timeout):
        assert headers == {"Access-Token": "token-value"}
        requests.append((url, dict(json)))
        return Response()

    monkeypatch.setattr("app.tiktok_marketing_api.httpx.post", fake_post)
    connection = tiktok_paid_provider_connection_service.get(
        UUID(_setup()[0])
    )
    assert connection is not None
    api = HttpxTikTokMarketingApiClient()
    api.set_ad_status(
        connection=connection,
        access_token="token-value",
        ad_id="ad",
        operation_status="ENABLE",
    )
    api.set_adgroup_status(
        connection=connection,
        access_token="token-value",
        adgroup_id="group",
        operation_status="ENABLE",
    )
    api.set_campaign_status(
        connection=connection,
        access_token="token-value",
        campaign_id="campaign",
        operation_status="ENABLE",
    )

    assert requests[0][1] == {
        "advertiser_id": "adv",
        "ad_ids": ["ad"],
        "operation_status": "ENABLE",
    }
    assert requests[1][1]["adgroup_ids"] == ["group"]
    assert requests[2][1]["campaign_ids"] == ["campaign"]
    assert all(payload["operation_status"] == "ENABLE" for _, payload in requests)
    assert all("token-value" not in url for url, _ in requests)
