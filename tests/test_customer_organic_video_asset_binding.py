from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

import app.creative_assets as creative_assets_module
from app.channel_execution import PublisherMode
from app.creative_assets import (
    CreativeAssetService,
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeBriefView,
    CreativeMediaType,
    CreativePurpose,
    CreativeReadinessStatus,
)
from app.customer_execution_request_schemas import CustomerExecutionRequestView
from app.customer_execution_requests import CUSTOMER_EXECUTION_REQUEST_NAMESPACE
from app.customer_operator_approval import CustomerOperatorApprovalService
from app.customer_publish_confirmation import CustomerPublishConfirmationService
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
from app.runtime_store import MemoryRuntimeStateStore

REQUEST_ID = UUID("11111111-1111-4111-8111-111111111111")
PROJECT_ID = UUID("22222222-2222-4222-8222-222222222222")
PRODUCT_ID = UUID("33333333-3333-4333-8333-333333333333")
PLAY_ID = UUID("44444444-4444-4444-8444-444444444444")
OPPORTUNITY_ID = UUID("55555555-5555-4555-8555-555555555555")
ACTION_ID = UUID("66666666-6666-4666-8666-666666666666")
EXPERIMENT_ID = UUID("77777777-7777-4777-8777-777777777777")
ASSET_A_ID = UUID("88888888-8888-4888-8888-888888888888")
ASSET_B_ID = UUID("99999999-9999-4999-8999-999999999999")
BRIEF_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
FINGERPRINT = "a" * 64
SOURCE_URL = "https://www.tiktok.com/@partizan/video/123456789"
VIDEO_A = "https://cdn.example.com/customer-video-a.mp4"
VIDEO_B = "https://cdn.example.com/customer-video-b.mp4"
CONTEXT = "A researched TikTok content cluster discussing the exact user problem."
CONTENT = "A concise educational organic video script grounded in the accepted research evidence."
TITLE = "Exact customer organic video"


class FakeRequestService:
    def __init__(self, request: CustomerExecutionRequestView) -> None:
        self.request = request

    def view(self, *, project: dict, draft):
        return self.request


class FakeExecutionService:
    def __init__(self, plan: DistributionExecutionPlanView) -> None:
        self.plan = plan

    def get_plan(self, action_id: UUID) -> DistributionExecutionPlanView:
        assert action_id == self.plan.action.id
        return self.plan


class FakeCreativeService:
    def __init__(self, assets: list[CreativeAssetView], selected: CreativeAssetView) -> None:
        self.assets = {asset.id: asset for asset in assets}
        self.selected = selected

    def readiness(self, action_id: UUID):
        assert action_id == ACTION_ID
        return SimpleNamespace(
            status=CreativeReadinessStatus.READY,
            selected_asset=self.selected,
        )

    def get_asset(self, asset_id: UUID) -> CreativeAssetView:
        if asset_id not in self.assets:
            raise KeyError(asset_id)
        return self.assets[asset_id]


def _asset(asset_id: UUID, url: str, *, updated_offset: int = 0) -> CreativeAssetView:
    now = datetime.now(UTC)
    return CreativeAssetView(
        id=asset_id,
        product_id=PRODUCT_ID,
        action_id=ACTION_ID,
        brief_id=BRIEF_ID,
        brief_fingerprint=FINGERPRINT,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        source=CreativeAssetSource.EXTERNAL_URL,
        status=CreativeAssetStatus.READY,
        public_url=url,
        mime_type="video/mp4",
        duration_seconds=12,
        provenance={},
        created_at=now,
        updated_at=now,
    )


def _plan() -> DistributionExecutionPlanView:
    action = DistributionActionView(
        id=ACTION_ID,
        platform=DistributionPlatform.TIKTOK,
        opportunity_id=OPPORTUNITY_ID,
        experiment_id=EXPERIMENT_ID,
        action_type=DistributionActionType.ORGANIC_VIDEO,
        status=DistributionActionStatus.PREPARED,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.ACTION,
        target_url=SOURCE_URL,
        content_text=CONTENT,
        content_payload={"title": TITLE, "context_text": CONTEXT},
        operational_metadata={
            "customer_execution_request_id": str(REQUEST_ID),
            "customer_exact_content_locked": True,
            "customer_publish_confirmation_required": True,
        },
    )
    experiment = DistributionExperimentView(
        id=EXPERIMENT_ID,
        product_id=PRODUCT_ID,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        action_id=ACTION_ID,
        status=DistributionExperimentStatus.DRAFT,
        attribution_level=AttributionLevel.ACTION,
        tracking_url="https://partizan.example/t/customer-video",
        referral_token="customer-video",
    )
    return DistributionExecutionPlanView(action=action, experiment=experiment)


def _request() -> CustomerExecutionRequestView:
    return CustomerExecutionRequestView(
        id=REQUEST_ID,
        project_id=PROJECT_ID,
        product_id=PRODUCT_ID,
        platform=DistributionPlatform.TIKTOK,
        publisher_mode=PublisherMode.MANUAL,
        status="ACTION_PREPARED",
        source_title="TikTok research source",
        source_url=SOURCE_URL,
        draft_title=TITLE,
        context_text=CONTEXT,
        content_text=CONTENT,
        distribution_play_id=PLAY_ID,
        opportunity_id=OPPORTUNITY_ID,
        distribution_action_id=ACTION_ID,
        experiment_id=EXPERIMENT_ID,
        requested_at=datetime.now(UTC),
    )


def test_confirmation_requires_the_exact_video_id_shown_to_customer() -> None:
    plan = _plan()
    request = _request()
    asset_a = _asset(ASSET_A_ID, VIDEO_A)
    asset_b = _asset(ASSET_B_ID, VIDEO_B)
    store = MemoryRuntimeStateStore()
    creative = FakeCreativeService([asset_a, asset_b], selected=asset_a)
    service = CustomerPublishConfirmationService(
        request_service=FakeRequestService(request),
        execution_service=FakeExecutionService(plan),
        creative_service=creative,
        store=store,
    )

    with pytest.raises(ValueError, match="Creative asset changed after customer review"):
        service.confirm(
            project={"id": str(PROJECT_ID)},
            draft=SimpleNamespace(),
            creative_asset_id=ASSET_B_ID,
        )

    assert store.get(CUSTOMER_EXECUTION_REQUEST_NAMESPACE, str(REQUEST_ID)) is None
    assert "customer_publish_confirmed_at" not in plan.action.operational_metadata


def test_confirmation_persists_video_binding_in_request_fingerprint_and_action_stamp() -> None:
    plan = _plan()
    request = _request()
    asset_a = _asset(ASSET_A_ID, VIDEO_A)
    store = MemoryRuntimeStateStore()
    creative = FakeCreativeService([asset_a], selected=asset_a)
    service = CustomerPublishConfirmationService(
        request_service=FakeRequestService(request),
        execution_service=FakeExecutionService(plan),
        creative_service=creative,
        store=store,
    )

    view = service.confirm(
        project={"id": str(PROJECT_ID)},
        draft=SimpleNamespace(),
        creative_asset_id=ASSET_A_ID,
    )
    persisted = CustomerExecutionRequestView.model_validate(
        store.get(CUSTOMER_EXECUTION_REQUEST_NAMESPACE, str(REQUEST_ID))
    )

    assert view.creative_asset_id == ASSET_A_ID
    assert str(view.creative_asset_url) == VIDEO_A
    assert persisted.confirmed_creative_asset_id == ASSET_A_ID
    assert str(persisted.confirmed_creative_asset_url) == VIDEO_A
    assert persisted.confirmed_creative_brief_fingerprint == FINGERPRINT
    assert persisted.customer_publish_confirmation_fingerprint
    assert plan.action.operational_metadata["customer_confirmed_creative_asset_id"] == str(
        ASSET_A_ID
    )
    assert plan.action.operational_metadata["customer_confirmed_creative_asset_url"] == VIDEO_A
    assert (
        plan.action.operational_metadata["customer_confirmed_creative_brief_fingerprint"]
        == FINGERPRINT
    )

    approval = CustomerOperatorApprovalService(
        execution_service=FakeExecutionService(plan),
        creative_service=creative,
    )
    approval.validate_exact_confirmation(request=persisted, plan=plan)

    creative.selected = _asset(ASSET_B_ID, VIDEO_B)
    with pytest.raises(ValueError, match="no longer provider-ready"):
        approval.validate_exact_confirmation(request=persisted, plan=plan)


def test_creative_readiness_pins_confirmed_asset_instead_of_newer_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_a = _asset(ASSET_A_ID, VIDEO_A)
    asset_b = _asset(ASSET_B_ID, VIDEO_B)
    action = _plan().action.model_copy(
        update={
            "operational_metadata": {
                **_plan().action.operational_metadata,
                "customer_confirmed_creative_asset_id": str(ASSET_A_ID),
                "customer_confirmed_creative_asset_url": VIDEO_A,
                "customer_confirmed_creative_brief_fingerprint": FINGERPRINT,
            }
        }
    )
    brief = CreativeBriefView(
        id=BRIEF_ID,
        product_id=PRODUCT_ID,
        action_id=ACTION_ID,
        experiment_id=EXPERIMENT_ID,
        play_id=PLAY_ID,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        content={},
        constraints=[],
        fingerprint=FINGERPRINT,
        created_at=datetime.now(UTC),
    )
    service = CreativeAssetService(MemoryRuntimeStateStore())
    monkeypatch.setattr(
        creative_assets_module,
        "distribution_execution_service",
        SimpleNamespace(get_action=lambda action_id: action),
    )
    monkeypatch.setattr(service, "ensure_brief", lambda action_id: brief)
    monkeypatch.setattr(service, "get_asset", lambda asset_id: asset_a)
    monkeypatch.setattr(service, "list_assets", lambda product_id: [asset_b, asset_a])

    readiness = service.readiness(ACTION_ID)

    assert readiness.status == CreativeReadinessStatus.READY
    assert readiness.selected_asset is not None
    assert readiness.selected_asset.id == ASSET_A_ID


def test_confirmed_creative_asset_cannot_be_retired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset_a = _asset(ASSET_A_ID, VIDEO_A)
    action = _plan().action.model_copy(
        update={
            "operational_metadata": {
                **_plan().action.operational_metadata,
                "customer_confirmed_creative_asset_id": str(ASSET_A_ID),
                "customer_confirmed_creative_asset_url": VIDEO_A,
                "customer_confirmed_creative_brief_fingerprint": FINGERPRINT,
            }
        }
    )
    service = CreativeAssetService(MemoryRuntimeStateStore())
    monkeypatch.setattr(service, "get_asset", lambda asset_id: asset_a)
    monkeypatch.setattr(
        creative_assets_module,
        "distribution_execution_service",
        SimpleNamespace(get_action=lambda action_id: action),
    )

    with pytest.raises(ValueError, match="cannot be retired after exact review"):
        service.retire(ASSET_A_ID)


def test_customer_ui_posts_the_reviewed_video_asset_id() -> None:
    source = Path("app/web/workspace.execution-request.v1.js").read_text()

    assert '<video controls preload="metadata"' in source
    assert "creative_asset_id: preparedAction.creative_asset_id || null" in source
    assert "Open the exact video" in source
