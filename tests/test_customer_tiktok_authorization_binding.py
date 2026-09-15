from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.channel_execution import PublisherMode
from app.creative_assets import (
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeMediaType,
    CreativePurpose,
)
from app.customer_execution_request_schemas import CustomerExecutionRequestView
from app.customer_operator_execution import CustomerOperatorExecutionService
from app.distribution_execution_schemas import (
    DistributionExecutionPlanView,
    DistributionExperimentStatus,
    DistributionExperimentView,
)
from app.distribution_schemas import DistributionActionView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.execution_adapters import (
    AdapterExecutionOutcome,
    DistributionAdapterExecutionView,
    ExecutionAdapterReceipt,
)
from app.tiktok_owned_publishing import (
    TikTokCreatorPublishPreflightView,
    TikTokPrivacyLevel,
)
from app.tiktok_publish_authorization import (
    TikTokPublishAuthorizationCreateRequest,
    TikTokPublishAuthorizationService,
)

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRODUCT_ID = UUID("33333333-3333-4333-8333-333333333333")
PLAY_ID = UUID("44444444-4444-4444-8444-444444444444")
OPPORTUNITY_ID = UUID("55555555-5555-4555-8555-555555555555")
ACTION_ID = UUID("66666666-6666-4666-8666-666666666666")
EXPERIMENT_ID = UUID("77777777-7777-4777-8777-777777777777")
ASSET_ID = UUID("88888888-8888-4888-8888-888888888888")
BLOB_ID = UUID("99999999-9999-4999-8999-999999999999")
IDENTITY_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
TITLE = "Exact customer TikTok caption"
SOURCE_URL = "https://www.tiktok.com/@partizan/video/123456789"
CONTEXT = "A researched TikTok content cluster discussing the exact customer problem."
CONTENT = "A short customer-confirmed video script grounded in the accepted research evidence."
SHA256 = "1" * 64


class FakePreflightService:
    def __init__(self, snapshot: TikTokCreatorPublishPreflightView) -> None:
        self.snapshot = snapshot

    def get_latest(self, action_id, *, require_fresh=False):
        assert action_id == ACTION_ID
        return self.snapshot


class FakeRequestService:
    def __init__(self, request: CustomerExecutionRequestView) -> None:
        self.request = request

    def get_request(self, request_id: UUID) -> CustomerExecutionRequestView:
        assert request_id == REQUEST_ID
        return self.request


class FakeExecutionService:
    def __init__(self, plan: DistributionExecutionPlanView) -> None:
        self.plan = plan

    def get_plan(self, action_id: UUID) -> DistributionExecutionPlanView:
        assert action_id == ACTION_ID
        return self.plan


class FakeApprovalService:
    def validate_exact_confirmation(self, *, request, plan) -> None:
        assert request.id == REQUEST_ID
        assert plan.action.id == ACTION_ID


class FakeAuthorizationLookup:
    def __init__(self, authorization=None, *, missing=False) -> None:
        self.authorization = authorization
        self.missing = missing

    def get_current(self, action_id: UUID, *, require_usable=False):
        assert action_id == ACTION_ID
        assert require_usable is True
        if self.missing:
            raise KeyError(action_id)
        return self.authorization


class FakeAdapterService:
    def __init__(self, plan: DistributionExecutionPlanView) -> None:
        self.plan = plan
        self.execute_calls = 0

    def get_receipt(self, action_id: UUID):
        assert action_id == ACTION_ID
        return None

    def execute(self, action_id: UUID, payload):
        assert action_id == ACTION_ID
        assert payload.retry is False
        self.execute_calls += 1
        receipt = ExecutionAdapterReceipt(
            action_id=ACTION_ID,
            adapter_name="customer-tiktok-test",
            provider="test",
            outcome=AdapterExecutionOutcome.ASSISTED,
            message="No provider mutation in regression test.",
            requires_operator_confirmation=True,
            created_at=datetime.now(UTC),
        )
        return DistributionAdapterExecutionView(receipt=receipt, plan=self.plan)


def _asset() -> CreativeAssetView:
    now = datetime.now(UTC)
    return CreativeAssetView(
        id=ASSET_ID,
        product_id=PRODUCT_ID,
        action_id=ACTION_ID,
        brief_id=uuid4(),
        brief_fingerprint="b" * 64,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        source=CreativeAssetSource.GENERATED,
        status=CreativeAssetStatus.READY,
        public_url=f"https://partizan.example/v1/public/creative-blobs/{BLOB_ID}",
        mime_type="video/mp4",
        duration_seconds=8,
        provenance={"blob_id": str(BLOB_ID), "sha256": SHA256},
        created_at=now,
        updated_at=now,
    )


def _preflight() -> TikTokCreatorPublishPreflightView:
    now = datetime.now(UTC)
    return TikTokCreatorPublishPreflightView(
        id=uuid4(),
        action_id=ACTION_ID,
        product_id=PRODUCT_ID,
        distribution_identity_id=IDENTITY_ID,
        creative_asset_id=ASSET_ID,
        creator_username="creator_123",
        creator_nickname="Creator",
        privacy_level_options=[TikTokPrivacyLevel.SELF_ONLY],
        comment_disabled=False,
        duet_disabled=False,
        stitch_disabled=False,
        max_video_post_duration_sec=300,
        fingerprint="c" * 64,
        fetched_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def _action() -> DistributionActionView:
    now = datetime.now(UTC)
    return DistributionActionView(
        id=ACTION_ID,
        platform=DistributionPlatform.TIKTOK,
        opportunity_id=OPPORTUNITY_ID,
        distribution_identity_id=IDENTITY_ID,
        experiment_id=EXPERIMENT_ID,
        action_type=DistributionActionType.ORGANIC_VIDEO,
        status=DistributionActionStatus.APPROVED,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.ACTION,
        target_url=SOURCE_URL,
        content_text=CONTENT,
        content_payload={"title": TITLE, "context_text": CONTEXT},
        operational_metadata={
            "customer_execution_request_id": str(REQUEST_ID),
            "customer_exact_content_locked": True,
            "customer_publish_confirmation_required": True,
            "customer_publish_confirmed_at": now.isoformat(),
            "customer_publish_confirmation_fingerprint": "d" * 64,
            "customer_confirmed_creative_asset_id": str(ASSET_ID),
            "customer_confirmed_creative_asset_url": (
                f"https://partizan.example/v1/public/creative-blobs/{BLOB_ID}"
            ),
            "customer_confirmed_creative_brief_fingerprint": "b" * 64,
            "customer_confirmed_creative_blob_id": str(BLOB_ID),
            "customer_confirmed_creative_sha256": SHA256,
        },
    )


def _payload(preflight: TikTokCreatorPublishPreflightView, *, title: str = TITLE):
    return TikTokPublishAuthorizationCreateRequest(
        preflight_id=preflight.id,
        title=title,
        privacy_level=TikTokPrivacyLevel.SELF_ONLY,
        allow_comment=False,
        allow_duet=False,
        allow_stitch=False,
        commercial_content_enabled=False,
        brand_organic_toggle=False,
        brand_content_toggle=False,
        is_aigc=True,
        music_usage_confirmation_accepted=True,
        branded_content_policy_accepted=False,
        explicit_publish_consent=True,
    )


def test_customer_bound_tiktok_authorization_rejects_changed_title(monkeypatch) -> None:
    action = _action()
    asset = _asset()
    preflight = _preflight()
    monkeypatch.setattr(
        "app.tiktok_publish_authorization.distribution_execution_service",
        SimpleNamespace(get_action=lambda action_id: action),
    )
    monkeypatch.setattr(
        "app.tiktok_publish_authorization.creative_asset_service",
        SimpleNamespace(get_asset=lambda asset_id: asset),
    )
    service = TikTokPublishAuthorizationService(
        preflight_service=FakePreflightService(preflight),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError, match="title must exactly match"):
        service.authorize(ACTION_ID, _payload(preflight, title="Changed after confirmation"))


def test_customer_bound_tiktok_authorization_accepts_exact_confirmed_title_and_asset(
    monkeypatch,
) -> None:
    action = _action()
    asset = _asset()
    preflight = _preflight()
    monkeypatch.setattr(
        "app.tiktok_publish_authorization.distribution_execution_service",
        SimpleNamespace(get_action=lambda action_id: action),
    )
    monkeypatch.setattr(
        "app.tiktok_publish_authorization.creative_asset_service",
        SimpleNamespace(get_asset=lambda asset_id: asset),
    )
    service = TikTokPublishAuthorizationService(
        preflight_service=FakePreflightService(preflight),  # type: ignore[arg-type]
    )

    authorization = service.authorize(ACTION_ID, _payload(preflight))

    assert authorization.creative_asset_id == ASSET_ID
    assert authorization.title == TITLE


def test_request_bound_execution_rejects_old_mismatched_usable_tiktok_authorization() -> None:
    action = _action()
    experiment = DistributionExperimentView(
        id=EXPERIMENT_ID,
        product_id=PRODUCT_ID,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        action_id=ACTION_ID,
        status=DistributionExperimentStatus.APPROVED,
        attribution_level=AttributionLevel.ACTION,
        tracking_url="https://partizan.example/t/customer-tiktok",
        referral_token="customer-tiktok",
    )
    plan = DistributionExecutionPlanView(action=action, experiment=experiment)
    now = datetime.now(UTC)
    request = CustomerExecutionRequestView(
        id=REQUEST_ID,
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        platform=DistributionPlatform.TIKTOK,
        publisher_mode=PublisherMode.MANUAL,
        status="OPERATOR_APPROVED",
        source_title="TikTok customer source",
        source_url=SOURCE_URL,
        draft_title=TITLE,
        context_text=CONTEXT,
        content_text=CONTENT,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        distribution_action_id=ACTION_ID,
        experiment_id=EXPERIMENT_ID,
        customer_publish_confirmed_at=now,
        customer_publish_confirmation_fingerprint="d" * 64,
        confirmed_creative_asset_id=ASSET_ID,
        confirmed_creative_asset_url=(
            f"https://partizan.example/v1/public/creative-blobs/{BLOB_ID}"
        ),
        confirmed_creative_brief_fingerprint="b" * 64,
        confirmed_creative_blob_id=BLOB_ID,
        confirmed_creative_sha256=SHA256,
        operator_approved_at=now,
        requested_at=now,
    )
    adapter = FakeAdapterService(plan)
    authorization = SimpleNamespace(
        action_id=ACTION_ID,
        creative_asset_id=ASSET_ID,
        title="Changed after customer confirmation",
    )
    service = CustomerOperatorExecutionService(
        request_service=FakeRequestService(request),  # type: ignore[arg-type]
        execution_service=FakeExecutionService(plan),  # type: ignore[arg-type]
        approval_service=FakeApprovalService(),  # type: ignore[arg-type]
        adapter_service=adapter,  # type: ignore[arg-type]
        tiktok_authorization_service=FakeAuthorizationLookup(  # type: ignore[arg-type]
            authorization
        ),
    )

    with pytest.raises(ValueError, match="title does not match"):
        service.execute(REQUEST_ID)

    assert adapter.execute_calls == 0


def test_request_bound_execution_without_usable_tiktok_authorization_remains_assisted() -> None:
    action = _action()
    experiment = DistributionExperimentView(
        id=EXPERIMENT_ID,
        product_id=PRODUCT_ID,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        action_id=ACTION_ID,
        status=DistributionExperimentStatus.APPROVED,
        attribution_level=AttributionLevel.ACTION,
        tracking_url="https://partizan.example/t/customer-tiktok",
        referral_token="customer-tiktok",
    )
    plan = DistributionExecutionPlanView(action=action, experiment=experiment)
    now = datetime.now(UTC)
    request = CustomerExecutionRequestView(
        id=REQUEST_ID,
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        platform=DistributionPlatform.TIKTOK,
        publisher_mode=PublisherMode.MANUAL,
        status="OPERATOR_APPROVED",
        source_title="TikTok customer source",
        source_url=SOURCE_URL,
        draft_title=TITLE,
        context_text=CONTEXT,
        content_text=CONTENT,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        distribution_action_id=ACTION_ID,
        experiment_id=EXPERIMENT_ID,
        customer_publish_confirmed_at=now,
        customer_publish_confirmation_fingerprint="d" * 64,
        confirmed_creative_asset_id=ASSET_ID,
        confirmed_creative_asset_url=(
            f"https://partizan.example/v1/public/creative-blobs/{BLOB_ID}"
        ),
        confirmed_creative_brief_fingerprint="b" * 64,
        confirmed_creative_blob_id=BLOB_ID,
        confirmed_creative_sha256=SHA256,
        operator_approved_at=now,
        requested_at=now,
    )
    adapter = FakeAdapterService(plan)
    service = CustomerOperatorExecutionService(
        request_service=FakeRequestService(request),  # type: ignore[arg-type]
        execution_service=FakeExecutionService(plan),  # type: ignore[arg-type]
        approval_service=FakeApprovalService(),  # type: ignore[arg-type]
        adapter_service=adapter,  # type: ignore[arg-type]
        tiktok_authorization_service=FakeAuthorizationLookup(  # type: ignore[arg-type]
            missing=True
        ),
    )

    result = service.execute(REQUEST_ID)

    assert adapter.execute_calls == 1
    assert result.receipt is not None
    assert result.receipt.outcome == AdapterExecutionOutcome.ASSISTED
