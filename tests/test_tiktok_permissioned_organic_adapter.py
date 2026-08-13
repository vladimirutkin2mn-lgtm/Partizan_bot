from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from app.autonomous_controlled_growth import AutonomousControlledGrowthSweepService
from app.autonomous_growth import AutonomousGrowthOutcome
from app.creative_assets import (
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeBriefView,
    CreativeMediaType,
    CreativePurpose,
    CreativeReadinessStatus,
    CreativeReadinessView,
)
from app.creative_generation import CreativeGenerationOutcome, CreativeGenerationView
from app.distribution_schemas import DistributionActionView, DistributionIdentityView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
)
from app.execution_adapters import (
    AdapterExecutionOutcome,
    ExecutionAdapterRegistry,
)
from app.organic_creative_execution import (
    OrganicVideoCreativeExecutionAdapter,
    TikTokPermissionedOrganicVideoExecutionAdapter,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_direct_post import (
    TikTokContentPostingAuditStatus,
    TikTokDirectPostAttemptStatus,
    TikTokDirectPostAttemptView,
)
from app.tiktok_direct_post_reconciliation import (
    TikTokDirectPostReconciliationStatus,
    TikTokDirectPostReconciliationView,
    TikTokProviderPostStatus,
)
from app.tiktok_owned_publishing import (
    TikTokCreatorPublishPreflightView,
    TikTokPrivacyLevel,
)
from app.tiktok_publish_authorization import (
    TikTokPublishAuthorizationStatus,
    TikTokPublishAuthorizationView,
)


class FakeGenerationService:
    def __init__(self, readiness: CreativeReadinessView) -> None:
        self.readiness = readiness
        self.calls = []

    def ensure_ready(self, action_id):
        self.calls.append(action_id)
        return CreativeGenerationView(
            action_id=action_id,
            outcome=CreativeGenerationOutcome.READY,
            brief=self.readiness.brief,
            asset=self.readiness.selected_asset,
            readiness=self.readiness,
            message="video ready",
        )


class FakePreflightService:
    def __init__(self, preflight: TikTokCreatorPublishPreflightView) -> None:
        self.preflight = preflight
        self.get_calls = []
        self.refresh_calls = []

    def get_latest(self, action_id, *, require_fresh=False):
        self.get_calls.append((action_id, require_fresh))
        return self.preflight

    def refresh(self, action_id):
        self.refresh_calls.append(action_id)
        return self.preflight


class FakeAuthorizationService:
    def __init__(self, authorization=None) -> None:
        self.authorization = authorization
        self.calls = []

    def get_current(self, action_id, *, require_usable=False):
        self.calls.append((action_id, require_usable))
        if self.authorization is None:
            raise KeyError(action_id)
        return self.authorization


class FakeDirectPostService:
    def __init__(self, *, attempt=None, submitted=None) -> None:
        self.attempt = attempt
        self.submitted = submitted
        self.get_calls = []
        self.submit_calls = []

    def get_latest(self, action_id):
        self.get_calls.append(action_id)
        if self.attempt is None:
            raise KeyError(action_id)
        return self.attempt

    def submit(self, action_id):
        self.submit_calls.append(action_id)
        return self.submitted or self.attempt


class FakeReconciliationService:
    def __init__(self, result=None) -> None:
        self.result = result
        self.calls = []

    def reconcile(self, action_id, *, mark_executed=True):
        self.calls.append((action_id, mark_executed))
        return self.result


def _fixtures(*, configured=True):
    now = datetime.now(UTC)
    product_id = uuid4()
    action_id = uuid4()
    experiment_id = uuid4()
    identity_id = uuid4()
    brief = CreativeBriefView(
        id=uuid4(),
        product_id=product_id,
        action_id=action_id,
        experiment_id=experiment_id,
        play_id=uuid4(),
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        content={"product_name": "Oracle"},
        constraints=["Use only confirmed product facts."],
        fingerprint="a" * 64,
        created_at=now,
    )
    asset = CreativeAssetView(
        id=uuid4(),
        product_id=product_id,
        action_id=action_id,
        brief_id=brief.id,
        brief_fingerprint=brief.fingerprint,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        source=CreativeAssetSource.GENERATED,
        status=CreativeAssetStatus.READY,
        public_url="https://partizan.example/v1/public/video/asset.mp4",
        mime_type="video/mp4",
        width=720,
        height=1280,
        duration_seconds=8,
        provenance={"generator": "gemini_omni"},
        created_at=now,
        updated_at=now,
    )
    readiness = CreativeReadinessView(
        action_id=action_id,
        brief=brief,
        status=CreativeReadinessStatus.READY,
        selected_asset=asset,
        reasons=["A provider-ready action-level CreativeAsset is available."],
    )
    action = DistributionActionView(
        id=action_id,
        platform=DistributionPlatform.TIKTOK,
        opportunity_id=uuid4(),
        distribution_identity_id=identity_id,
        experiment_id=experiment_id,
        action_type=DistributionActionType.ORGANIC_VIDEO,
        status=DistributionActionStatus.APPROVED,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.ACTION,
        content_text="A short relationship reflection.",
    )
    identity = DistributionIdentityView(
        id=identity_id,
        platform=DistributionPlatform.TIKTOK,
        theme="relationship reflection",
        public_positioning="Creator-owned relationship reflection content.",
        profile_config={
            "execution_provider": (
                "tiktok_content_posting" if configured else "not_configured"
            ),
            "access_token_env": "TIKTOK_CREATOR_TOKEN",
            "content_posting_audit_status": "AUDITED",
            "verified_url_prefix": "https://partizan.example",
        },
        eligibility={"allowed_actions": [DistributionActionType.ORGANIC_VIDEO.value]},
        status=DistributionIdentityStatus.ACTIVE,
    )
    preflight = TikTokCreatorPublishPreflightView(
        id=uuid4(),
        action_id=action_id,
        product_id=product_id,
        distribution_identity_id=identity_id,
        creative_asset_id=asset.id,
        creator_username="creator_123",
        creator_nickname="Oracle Creator",
        privacy_level_options=[TikTokPrivacyLevel.SELF_ONLY],
        comment_disabled=False,
        duet_disabled=False,
        stitch_disabled=False,
        max_video_post_duration_sec=300,
        fingerprint="b" * 64,
        fetched_at=now,
        expires_at=now + timedelta(minutes=5),
    )
    authorization = TikTokPublishAuthorizationView(
        id=uuid4(),
        action_id=action_id,
        product_id=product_id,
        distribution_identity_id=identity_id,
        creative_asset_id=asset.id,
        preflight_id=preflight.id,
        preflight_fingerprint=preflight.fingerprint,
        creator_username="creator_123",
        creator_nickname="Oracle Creator",
        title="A calm relationship reflection",
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
        status=TikTokPublishAuthorizationStatus.AUTHORIZED,
        authorized_at=now,
        expires_at=preflight.expires_at,
    )
    return SimpleNamespace(
        action=action,
        identity=identity,
        readiness=readiness,
        asset=asset,
        preflight=preflight,
        authorization=authorization,
    )


def _attempt(fx, status, *, publish_id="v_pub_url~v2.123"):
    now = datetime.now(UTC)
    return TikTokDirectPostAttemptView(
        id=uuid4(),
        action_id=fx.action.id,
        authorization_id=fx.authorization.id,
        distribution_identity_id=fx.identity.id,
        creative_asset_id=fx.asset.id,
        client_audit_status=TikTokContentPostingAuditStatus.AUDITED,
        status=status,
        provider_publish_id=publish_id,
        started_at=now,
        updated_at=now,
    )


def _reconciliation(fx, attempt, status):
    provider_status = {
        TikTokDirectPostReconciliationStatus.PROCESSING: TikTokProviderPostStatus.PROCESSING_DOWNLOAD,
        TikTokDirectPostReconciliationStatus.PUBLISHED: TikTokProviderPostStatus.PUBLISH_COMPLETE,
        TikTokDirectPostReconciliationStatus.FAILED: TikTokProviderPostStatus.FAILED,
    }[status]
    return TikTokDirectPostReconciliationView(
        action_id=fx.action.id,
        attempt_id=attempt.id,
        provider_publish_id=attempt.provider_publish_id,
        status=status,
        provider_status=provider_status,
        fail_reason=("provider_failed" if status == TikTokDirectPostReconciliationStatus.FAILED else None),
        public_post_ids=(
            ["741852963"]
            if status == TikTokDirectPostReconciliationStatus.PUBLISHED
            else []
        ),
        checked_at=datetime.now(UTC),
    )


def _patch_identity_and_experiment(monkeypatch, fx):
    monkeypatch.setattr(
        "app.organic_creative_execution.distribution_control_plane_service",
        type("Control", (), {"get_identity": staticmethod(lambda identity_id: fx.identity)})(),
    )
    monkeypatch.setattr(
        "app.creative_execution_adapters.distribution_execution_service",
        type(
            "Execution",
            (),
            {
                "get_experiment": staticmethod(
                    lambda experiment_id: SimpleNamespace(product_id=fx.asset.product_id)
                )
            },
        )(),
    )


def _adapter(
    fx,
    *,
    authorization=None,
    attempt=None,
    submitted=None,
    reconciliation=None,
):
    return TikTokPermissionedOrganicVideoExecutionAdapter(
        generation_service=FakeGenerationService(fx.readiness),
        attribution_store=MemoryRuntimeStateStore(),
        preflight_service=FakePreflightService(fx.preflight),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(authorization),  # type: ignore[arg-type]
        direct_post_service=FakeDirectPostService(  # type: ignore[arg-type]
            attempt=attempt,
            submitted=submitted,
        ),
        reconciliation_service=FakeReconciliationService(  # type: ignore[arg-type]
            reconciliation
        ),
    )


def test_ready_video_without_authorization_stops_at_fresh_creator_consent(monkeypatch) -> None:
    fx = _fixtures()
    _patch_identity_and_experiment(monkeypatch, fx)
    adapter = _adapter(fx)

    receipt = adapter.execute(fx.action)

    assert receipt.outcome == AdapterExecutionOutcome.ASSISTED
    assert receipt.requires_operator_confirmation is True
    assert receipt.metadata["creative_asset_id"] == str(fx.asset.id)
    assert receipt.metadata["creator_preflight_id"] == str(fx.preflight.id)
    assert receipt.metadata["privacy_level_options"] == ["SELF_ONLY"]
    assert "authorization is required" in receipt.message


def test_valid_authorization_submits_once_and_enters_in_progress(monkeypatch) -> None:
    fx = _fixtures()
    _patch_identity_and_experiment(monkeypatch, fx)
    submitted = _attempt(fx, TikTokDirectPostAttemptStatus.SUBMITTED)
    direct = FakeDirectPostService(submitted=submitted)
    adapter = TikTokPermissionedOrganicVideoExecutionAdapter(
        generation_service=FakeGenerationService(fx.readiness),
        attribution_store=MemoryRuntimeStateStore(),
        preflight_service=FakePreflightService(fx.preflight),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(fx.authorization),  # type: ignore[arg-type]
        direct_post_service=direct,  # type: ignore[arg-type]
        reconciliation_service=FakeReconciliationService(),  # type: ignore[arg-type]
    )

    receipt = adapter.execute(fx.action)

    assert receipt.outcome == AdapterExecutionOutcome.IN_PROGRESS
    assert receipt.requires_operator_confirmation is False
    assert receipt.external_reference == submitted.provider_publish_id
    assert direct.submit_calls == [fx.action.id]


def test_submitted_post_reconciles_processing_without_resubmit(monkeypatch) -> None:
    fx = _fixtures()
    _patch_identity_and_experiment(monkeypatch, fx)
    attempt = _attempt(fx, TikTokDirectPostAttemptStatus.SUBMITTED)
    reconciliation = _reconciliation(
        fx,
        attempt,
        TikTokDirectPostReconciliationStatus.PROCESSING,
    )
    direct = FakeDirectPostService(attempt=attempt)
    rec = FakeReconciliationService(reconciliation)
    adapter = TikTokPermissionedOrganicVideoExecutionAdapter(
        generation_service=FakeGenerationService(fx.readiness),
        attribution_store=MemoryRuntimeStateStore(),
        preflight_service=FakePreflightService(fx.preflight),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(),  # type: ignore[arg-type]
        direct_post_service=direct,  # type: ignore[arg-type]
        reconciliation_service=rec,  # type: ignore[arg-type]
    )

    receipt = adapter.execute(fx.action)

    assert receipt.outcome == AdapterExecutionOutcome.IN_PROGRESS
    assert receipt.requires_operator_confirmation is False
    assert direct.submit_calls == []
    assert rec.calls == [(fx.action.id, False)]
    assert receipt.metadata["provider_status"] == "PROCESSING_DOWNLOAD"


def test_provider_publish_complete_returns_executed_evidence(monkeypatch) -> None:
    fx = _fixtures()
    _patch_identity_and_experiment(monkeypatch, fx)
    attempt = _attempt(fx, TikTokDirectPostAttemptStatus.SUBMITTED)
    reconciliation = _reconciliation(
        fx,
        attempt,
        TikTokDirectPostReconciliationStatus.PUBLISHED,
    )
    adapter = _adapter(
        fx,
        attempt=attempt,
        reconciliation=reconciliation,
    )

    receipt = adapter.execute(fx.action)

    assert receipt.outcome == AdapterExecutionOutcome.EXECUTED
    assert receipt.external_reference == attempt.provider_publish_id
    assert receipt.metadata["provider_status"] == "PUBLISH_COMPLETE"
    assert receipt.metadata["public_post_ids"] == ["741852963"]


def test_ambiguous_attempt_without_publish_id_never_resubmits(monkeypatch) -> None:
    fx = _fixtures()
    _patch_identity_and_experiment(monkeypatch, fx)
    attempt = _attempt(
        fx,
        TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED,
        publish_id=None,
    )
    direct = FakeDirectPostService(attempt=attempt)
    adapter = TikTokPermissionedOrganicVideoExecutionAdapter(
        generation_service=FakeGenerationService(fx.readiness),
        attribution_store=MemoryRuntimeStateStore(),
        preflight_service=FakePreflightService(fx.preflight),  # type: ignore[arg-type]
        authorization_service=FakeAuthorizationService(fx.authorization),  # type: ignore[arg-type]
        direct_post_service=direct,  # type: ignore[arg-type]
        reconciliation_service=FakeReconciliationService(),  # type: ignore[arg-type]
    )

    receipt = adapter.execute(fx.action)

    assert receipt.outcome == AdapterExecutionOutcome.ASSISTED
    assert receipt.requires_operator_confirmation is True
    assert direct.submit_calls == []
    assert "will not repost" in receipt.message


def test_permissioned_registry_precedes_generic_gate_only_for_configured_identity(monkeypatch) -> None:
    fx = _fixtures(configured=True)
    _patch_identity_and_experiment(monkeypatch, fx)
    permissioned = _adapter(fx)
    generic = OrganicVideoCreativeExecutionAdapter(
        generation_service=FakeGenerationService(fx.readiness),
        attribution_store=MemoryRuntimeStateStore(),
    )
    registry = ExecutionAdapterRegistry([permissioned, generic])

    assert registry.resolve(fx.action) is permissioned

    fx.identity = fx.identity.model_copy(
        update={"profile_config": {"execution_provider": "not_configured"}}
    )
    assert registry.resolve(fx.action) is generic


def test_controlled_worker_treats_provider_in_progress_as_non_failure() -> None:
    worker = AutonomousControlledGrowthSweepService()

    assert (
        worker._adapter_outcome(AdapterExecutionOutcome.IN_PROGRESS)
        == AutonomousGrowthOutcome.ASSISTED
    )
    assert (
        worker._adapter_outcome(AdapterExecutionOutcome.EXECUTED)
        == AutonomousGrowthOutcome.EXECUTED
    )
