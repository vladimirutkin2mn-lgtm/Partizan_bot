from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.audience_intelligence_service import audience_intelligence_service
from app.channel_service import channel_service
from app.creative_assets import creative_asset_service
from app.creative_execution_adapters import (
    CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE,
    MetaCreativeAdsExecutionAdapter,
    TikTokCreativeAdsExecutionAdapter,
)
from app.distribution_control_plane_service import distribution_control_plane_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_play_service import distribution_play_service
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecuteRequest,
    DistributionExecutionAdapterService,
    ExecutionAdapterRegistry,
    distribution_execution_adapter_service,
)
from app.growth_play_service import growth_play_service
from app.icp_service import icp_service
from app.main import app
from app.meta_marketing_api import MetaMarketingApiError
from app.paid_campaign import paid_campaign_spec_service
from app.paid_provider_connections import (
    PaidProviderConnectionCreateRequest,
    paid_provider_connection_service,
)
from app.product_intake import product_intake_service
from app.runtime_store import get_runtime_store
from app.tiktok_marketing_api import TikTokMarketingApiError
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionCreateRequest,
    tiktok_paid_provider_connection_service,
)

client = TestClient(app)


class FakeMetaSecretResolver:
    def resolve(self, name: str) -> str | None:
        assert name == "META_TEST_TOKEN"
        return "meta-secret-value"


class FakeTikTokSecretResolver:
    def resolve(self, name: str) -> str | None:
        assert name == "TIKTOK_TEST_TOKEN"
        return "tiktok-secret-value"


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
    channel_service.reset()
    audience_intelligence_service.reset()
    growth_play_service.reset()
    distribution_play_service.reset()
    distribution_control_plane_service.reset()
    distribution_execution_service.reset()
    distribution_execution_adapter_service.reset()
    paid_campaign_spec_service.reset()
    paid_provider_connection_service.reset()
    tiktok_paid_provider_connection_service.reset()
    creative_asset_service.reset()
    store = get_runtime_store()
    if store.ephemeral:
        store.clear_namespace(CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE)


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
    assert response.status_code == 201
    product_id = response.json()["product"]["id"]
    assert client.post(f"/v1/products/{product_id}/confirm").status_code == 200
    assert client.post(f"/v1/products/{product_id}/icps/generate").status_code == 200
    assert client.post(f"/v1/products/{product_id}/distribution/discover").status_code == 200
    return product_id


def _approved_paid_action(product_id: str, tactic_id: str) -> UUID:
    plays = client.post(f"/v1/products/{product_id}/distribution-plays/generate")
    assert plays.status_code == 200
    play = next(item for item in plays.json()["plays"] if item["tactic_id"] == tactic_id)
    prepared = client.post(
        f"/v1/products/{product_id}/distribution-plays/{play['id']}/actions/prepare",
        json={"destination_url": "https://example.com/oracle"},
    )
    assert prepared.status_code == 200
    action_id = UUID(prepared.json()["action"]["id"])
    assert client.post(f"/v1/distribution-actions/{action_id}/approve").status_code == 200
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
            default_image_url="https://cdn.example.com/legacy-meta.jpg",
            budget_minor_unit_factor=100,
            test_days=5,
        ),
    )


def _connect_tiktok(product_id: str) -> None:
    tiktok_paid_provider_connection_service.upsert(
        UUID(product_id),
        TikTokPaidProviderConnectionCreateRequest(
            advertiser_id="adv_123",
            access_token_env="TIKTOK_TEST_TOKEN",
            api_version="v1.3",
            location_ids=["6252001"],
            video_id="legacy_video_123",
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


def _meta_service(fake: FakeMetaClient) -> DistributionExecutionAdapterService:
    adapter = MetaCreativeAdsExecutionAdapter(
        client=fake,
        secret_resolver=FakeMetaSecretResolver(),
        connection_service=paid_provider_connection_service,
        spec_service=paid_campaign_spec_service,
        creative_service=creative_asset_service,
        attribution_store=get_runtime_store(),
    )
    return DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([adapter]),
        store=get_runtime_store(),
    )


def _tiktok_service(fake: FakeTikTokClient) -> DistributionExecutionAdapterService:
    adapter = TikTokCreativeAdsExecutionAdapter(
        client=fake,
        secret_resolver=FakeTikTokSecretResolver(),
        connection_service=tiktok_paid_provider_connection_service,
        spec_service=paid_campaign_spec_service,
        creative_service=creative_asset_service,
        attribution_store=get_runtime_store(),
    )
    return DistributionExecutionAdapterService(
        registry=ExecutionAdapterRegistry([adapter]),
        store=get_runtime_store(),
    )


def _brief(action_id: UUID) -> dict:
    response = client.post(f"/v1/distribution-actions/{action_id}/creative-brief")
    assert response.status_code == 200
    return response.json()


def test_meta_action_level_image_overrides_connection_fallback() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id, "instagram_ads")
    _connect_meta(product_id)
    brief = _brief(action_id)
    asset = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXTERNAL_URL",
            "status": "READY",
            "public_url": "https://cdn.example.com/action-meta.jpg",
            "mime_type": "image/jpeg",
            "provenance": {"source_system": "test"},
        },
    )
    assert asset.status_code == 201
    fake = FakeMetaClient()

    result = _meta_service(fake).execute(action_id, DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.STAGED
    creative_call = next(kwargs for step, kwargs in fake.calls if step == "creative")
    assert str(creative_call["connection"].default_image_url) == (
        "https://cdn.example.com/action-meta.jpg"
    )
    assert result.receipt.metadata["creative_source"] == "ACTION_ASSET"
    assert result.receipt.metadata["creative_asset_id"] == asset.json()["id"]
    assert result.receipt.metadata["creative_brief_id"] == brief["id"]
    assert result.receipt.metadata["spend_started"] is False

    rows = get_runtime_store().list_namespace(CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE)
    assert len(rows) == 1
    assert rows[0]["creative_source"] == "ACTION_ASSET"
    assert rows[0]["asset_id"] == asset.json()["id"]
    assert rows[0]["brief_fingerprint"] == brief["fingerprint"]
    assert "meta-secret-value" not in result.receipt.model_dump_json()
    assert "meta-secret-value" not in str(rows)


def test_meta_without_ready_action_asset_uses_explicit_legacy_fallback() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id, "instagram_ads")
    _connect_meta(product_id)
    brief = _brief(action_id)
    fake = FakeMetaClient()

    result = _meta_service(fake).execute(action_id, DistributionAdapterExecuteRequest())

    creative_call = next(kwargs for step, kwargs in fake.calls if step == "creative")
    assert str(creative_call["connection"].default_image_url) == (
        "https://cdn.example.com/legacy-meta.jpg"
    )
    assert result.receipt.metadata["creative_source"] == "CONNECTION_FALLBACK"
    assert result.receipt.metadata["creative_brief_id"] == brief["id"]
    assert "creative_asset_id" not in result.receipt.metadata


def test_tiktok_action_provider_video_overrides_connection_fallback() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id, "tiktok_ads")
    _connect_tiktok(product_id)
    brief = _brief(action_id)
    asset = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXISTING_PROVIDER",
            "status": "READY",
            "provider_asset_id": "action_video_456",
            "mime_type": "video/mp4",
            "duration_seconds": 12,
            "provenance": {"provider": "tiktok", "source_system": "test"},
        },
    )
    assert asset.status_code == 201
    fake = FakeTikTokClient()

    result = _tiktok_service(fake).execute(action_id, DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.STAGED
    ad_call = next(kwargs for step, kwargs in fake.calls if step == "ad")
    assert ad_call["connection"].video_id == "action_video_456"
    assert result.receipt.metadata["creative_source"] == "ACTION_ASSET"
    assert result.receipt.metadata["creative_asset_id"] == asset.json()["id"]
    assert result.receipt.metadata["spend_started"] is False
    assert "tiktok-secret-value" not in result.receipt.model_dump_json()


def test_tiktok_public_url_only_asset_does_not_fake_provider_video() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id, "tiktok_ads")
    _connect_tiktok(product_id)
    brief = _brief(action_id)
    external = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXTERNAL_URL",
            "status": "READY",
            "public_url": "https://cdn.example.com/action-video.mp4",
            "mime_type": "video/mp4",
            "duration_seconds": 12,
        },
    )
    assert external.status_code == 201
    readiness = client.get(f"/v1/distribution-actions/{action_id}/creative-readiness")
    assert readiness.status_code == 200
    assert readiness.json()["status"] == "BLOCKED"
    fake = FakeTikTokClient()

    result = _tiktok_service(fake).execute(action_id, DistributionAdapterExecuteRequest())

    ad_call = next(kwargs for step, kwargs in fake.calls if step == "ad")
    assert ad_call["connection"].video_id == "legacy_video_123"
    assert result.receipt.metadata["creative_source"] == "CONNECTION_FALLBACK"
    assert "creative_asset_id" not in result.receipt.metadata


def test_partial_provider_failure_keeps_creative_attribution_without_secret() -> None:
    product_id = _product()
    action_id = _approved_paid_action(product_id, "instagram_ads")
    _connect_meta(product_id)
    brief = _brief(action_id)
    asset = client.post(
        "/v1/creative-assets",
        json={
            "brief_id": brief["id"],
            "source": "EXTERNAL_URL",
            "status": "READY",
            "public_url": "https://cdn.example.com/action-meta.jpg",
        },
    )
    assert asset.status_code == 201
    fake = FakeMetaClient(fail_at="creative")

    result = _meta_service(fake).execute(action_id, DistributionAdapterExecuteRequest())

    assert result.receipt.outcome == AdapterExecutionOutcome.FAILED
    assert result.receipt.requires_operator_confirmation is True
    assert result.receipt.metadata["creative_source"] == "ACTION_ASSET"
    assert result.receipt.metadata["creative_asset_id"] == asset.json()["id"]
    assert result.receipt.metadata["partial_provider_ids"] == {
        "campaign_id": "cmp_123",
        "ad_set_id": "set_123",
    }
    rows = get_runtime_store().list_namespace(CREATIVE_EXECUTION_ATTRIBUTION_NAMESPACE)
    assert rows[0]["adapter_outcome"] == "FAILED"
    assert "meta-secret-value" not in str(rows)
