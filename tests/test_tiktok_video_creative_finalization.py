from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.creative_assets import (
    CreativeAssetRegisterRequest,
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeBriefView,
    CreativeMediaType,
    CreativePurpose,
    CreativeReadinessStatus,
    CreativeReadinessView,
)
from app.creative_generation import CreativeGenerationOutcome
from app.creative_provider_finalization import (
    CreativeProviderFinalizationOutcome,
    ProviderAwareCreativeGenerationService,
    TikTokVideoCreativeFinalizer,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_creative_api import (
    HttpxTikTokCreativeApiClient,
    TikTokCreativeApiError,
)
from app.tiktok_paid_provider import (
    TikTokPaidProviderConnectionStatus,
    TikTokPaidProviderConnectionView,
)


class FakeAssetService:
    def __init__(self, brief: CreativeBriefView, source: CreativeAssetView) -> None:
        self.brief = brief
        self.source = source
        self.provider_asset: CreativeAssetView | None = None
        self.registered: list[CreativeAssetRegisterRequest] = []

    def readiness(self, action_id):
        assert action_id == self.brief.action_id
        if self.provider_asset is not None:
            return CreativeReadinessView(
                action_id=action_id,
                brief=self.brief,
                status=CreativeReadinessStatus.READY,
                selected_asset=self.provider_asset,
                reasons=["A provider-ready action-level CreativeAsset is available."],
            )
        return CreativeReadinessView(
            action_id=action_id,
            brief=self.brief,
            status=CreativeReadinessStatus.BLOCKED,
            reasons=["TikTok staging requires a real provider video ID."],
        )

    def list_assets(self, product_id):
        assert product_id == self.brief.product_id
        rows = [self.source]
        if self.provider_asset is not None:
            rows.insert(0, self.provider_asset)
        return rows

    def register_asset(self, payload: CreativeAssetRegisterRequest):
        self.registered.append(payload)
        now = datetime.now(UTC)
        self.provider_asset = CreativeAssetView(
            id=uuid4(),
            product_id=self.brief.product_id,
            action_id=self.brief.action_id,
            brief_id=self.brief.id,
            brief_fingerprint=self.brief.fingerprint,
            platform=self.brief.platform,
            purpose=self.brief.purpose,
            media_type=self.brief.media_type,
            source=payload.source,
            status=payload.status,
            public_url=payload.public_url,
            provider_asset_id=payload.provider_asset_id,
            mime_type=payload.mime_type,
            width=payload.width,
            height=payload.height,
            duration_seconds=payload.duration_seconds,
            provenance=payload.provenance,
            failure_reason=payload.failure_reason,
            created_at=now,
            updated_at=now,
        )
        return self.provider_asset


class FakeConnectionService:
    def __init__(self, connection: TikTokPaidProviderConnectionView | None) -> None:
        self.connection = connection

    def get(self, product_id):
        if self.connection is not None:
            assert product_id == self.connection.product_id
        return self.connection


class FakeSecretResolver:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def resolve(self, name: str):
        assert name == "TIKTOK_TEST_TOKEN"
        return self.value


class FakeUploadClient:
    def __init__(self, *, video_id: str = "video_real_123", fail: bool = False) -> None:
        self.video_id = video_id
        self.fail = fail
        self.calls: list[dict] = []

    def upload_video_by_url(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise TikTokCreativeApiError("ambiguous provider failure")
        return self.video_id


class ShouldNotGenerate:
    def ensure_ready(self, action_id):
        raise AssertionError(f"source generator should not run for provider-finalized {action_id}")


def _brief() -> CreativeBriefView:
    return CreativeBriefView(
        id=uuid4(),
        product_id=uuid4(),
        action_id=uuid4(),
        experiment_id=uuid4(),
        play_id=uuid4(),
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.PAID_AD,
        media_type=CreativeMediaType.VIDEO,
        content={"product_name": "Oracle", "message_hook": "A useful reflection."},
        constraints=["Use only confirmed product facts."],
        fingerprint="a" * 64,
        created_at=datetime.now(UTC),
    )


def _source(brief: CreativeBriefView, *, public_url: str = "https://cdn.example.com/ad.mp4"):
    now = datetime.now(UTC)
    return CreativeAssetView(
        id=uuid4(),
        product_id=brief.product_id,
        action_id=brief.action_id,
        brief_id=brief.id,
        brief_fingerprint=brief.fingerprint,
        platform=brief.platform,
        purpose=brief.purpose,
        media_type=brief.media_type,
        source=CreativeAssetSource.EXTERNAL_URL,
        status=CreativeAssetStatus.READY,
        public_url=public_url,
        mime_type="video/mp4",
        width=1080,
        height=1920,
        duration_seconds=12,
        provenance={"origin": "test-video-provider"},
        created_at=now,
        updated_at=now,
    )


def _connection(brief: CreativeBriefView) -> TikTokPaidProviderConnectionView:
    return TikTokPaidProviderConnectionView(
        id=uuid4(),
        product_id=brief.product_id,
        advertiser_id="adv_123",
        access_token_env="TIKTOK_TEST_TOKEN",
        api_version="v1.3",
        location_ids=["6252001"],
        video_id="legacy_video_fallback",
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
        test_days=5,
        status=TikTokPaidProviderConnectionStatus.ACTIVE,
    )


def _finalizer(*, upload=None, token="token-value", connection=True):
    brief = _brief()
    source = _source(brief)
    assets = FakeAssetService(brief, source)
    client = upload or FakeUploadClient()
    finalizer = TikTokVideoCreativeFinalizer(
        client=client,
        connection_service=FakeConnectionService(_connection(brief) if connection else None),
        secret_resolver=FakeSecretResolver(token),
        asset_service=assets,  # type: ignore[arg-type]
        store=MemoryRuntimeStateStore(),
    )
    return finalizer, assets, client


def test_tiktok_url_video_is_uploaded_once_and_promoted_to_provider_ready() -> None:
    finalizer, assets, upload = _finalizer()

    first = finalizer.finalize(assets.brief.action_id)

    assert first.outcome == CreativeProviderFinalizationOutcome.READY
    assert first.readiness.status == CreativeReadinessStatus.READY
    assert first.asset is not None
    assert first.asset.provider_asset_id == "video_real_123"
    assert len(upload.calls) == 1
    call = upload.calls[0]
    assert call["access_token"] == "token-value"
    assert call["video_url"] == "https://cdn.example.com/ad.mp4"
    assert call["connection"].advertiser_id == "adv_123"
    assert call["file_name"].startswith("partizan-")
    assert len(call["file_name"]) <= 100

    registered = assets.registered[0]
    assert registered.source == CreativeAssetSource.EXISTING_PROVIDER
    assert registered.provider_asset_id == "video_real_123"
    assert registered.public_url == assets.source.public_url
    assert registered.provenance["source_asset_id"] == str(assets.source.id)
    assert "token" not in str(registered.provenance).lower()

    repeated = finalizer.finalize(assets.brief.action_id)
    assert repeated.outcome == CreativeProviderFinalizationOutcome.READY
    assert len(upload.calls) == 1


def test_ambiguous_tiktok_upload_is_not_retried_blindly() -> None:
    upload = FakeUploadClient(fail=True)
    finalizer, assets, _ = _finalizer(upload=upload)

    first = finalizer.finalize(assets.brief.action_id)
    second = finalizer.finalize(assets.brief.action_id)

    assert first.outcome == CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED
    assert second.outcome == CreativeProviderFinalizationOutcome.RECONCILIATION_REQUIRED
    assert len(upload.calls) == 1
    assert assets.registered == []
    assert "ambiguous provider failure" not in first.message


def test_missing_tiktok_connection_or_secret_fails_closed_without_upload() -> None:
    without_connection, assets, upload = _finalizer(connection=False)
    result = without_connection.finalize(assets.brief.action_id)
    assert result.outcome == CreativeProviderFinalizationOutcome.UNAVAILABLE
    assert upload.calls == []

    without_secret, assets2, upload2 = _finalizer(token=None)
    result2 = without_secret.finalize(assets2.brief.action_id)
    assert result2.outcome == CreativeProviderFinalizationOutcome.UNAVAILABLE
    assert upload2.calls == []


def test_provider_aware_generation_finalizes_existing_video_before_source_generation() -> None:
    finalizer, assets, upload = _finalizer()
    service = ProviderAwareCreativeGenerationService(
        generation_service=ShouldNotGenerate(),  # type: ignore[arg-type]
        tiktok_finalizer=finalizer,
    )

    result = service.ensure_ready(assets.brief.action_id)

    assert result.outcome == CreativeGenerationOutcome.READY
    assert result.readiness.status == CreativeReadinessStatus.READY
    assert result.asset is not None
    assert result.asset.provider_asset_id == "video_real_123"
    assert len(upload.calls) == 1


def test_tiktok_creative_http_client_uses_multipart_url_upload_and_requires_video_id(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

        def __init__(self, body):
            self.body = body

        def json(self):
            return self.body

    responses = iter(
        [
            Response({"code": 0, "message": "OK", "data": {"video_id": "video_777"}}),
            Response({"code": 0, "message": "OK", "data": {}}),
        ]
    )

    def fake_post(url, *, files, headers, timeout):
        captured["url"] = url
        captured["files"] = files
        captured["headers"] = headers
        captured["timeout"] = timeout
        return next(responses)

    monkeypatch.setattr("app.tiktok_creative_api.httpx.post", fake_post)
    brief = _brief()
    connection = _connection(brief)
    client = HttpxTikTokCreativeApiClient(timeout_seconds=22)

    video_id = client.upload_video_by_url(
        connection=connection,
        access_token="top-secret",
        video_url="https://cdn.example.com/video.mp4",
        file_name="partizan-video.mp4",
    )

    assert video_id == "video_777"
    assert captured["url"].endswith("/open_api/v1.3/file/video/ad/upload/")
    assert captured["headers"] == {"Access-Token": "top-secret"}
    assert captured["timeout"] == 22
    assert captured["files"] == {
        "advertiser_id": (None, "adv_123"),
        "upload_type": (None, "UPLOAD_BY_URL"),
        "video_url": (None, "https://cdn.example.com/video.mp4"),
        "file_name": (None, "partizan-video.mp4"),
    }
    assert "top-secret" not in captured["url"]

    with pytest.raises(TikTokCreativeApiError, match="without returning a video_id"):
        client.upload_video_by_url(
            connection=connection,
            access_token="top-secret",
            video_url="https://cdn.example.com/video.mp4",
            file_name="partizan-video-2.mp4",
        )
