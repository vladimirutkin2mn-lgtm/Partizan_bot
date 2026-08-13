from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

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
from app.distribution_schemas import DistributionActionView, DistributionIdentityView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_owned_publishing import (
    HttpxTikTokCreatorInfoClient,
    TikTokCreatorInfo,
    TikTokCreatorInfoApiError,
    TikTokCreatorPublishPreflightService,
    TikTokPrivacyLevel,
)


class FakeCreatorInfoClient:
    def __init__(self, info: TikTokCreatorInfo) -> None:
        self.info = info
        self.tokens: list[str] = []

    def query_creator_info(self, *, access_token: str) -> TikTokCreatorInfo:
        self.tokens.append(access_token)
        return self.info


class FakeSecretResolver:
    def __init__(self, token: str | None = "creator-secret") -> None:
        self.token = token
        self.names: list[str] = []

    def resolve(self, name: str) -> str | None:
        self.names.append(name)
        return self.token


def _dependencies(*, duration_seconds: float = 8, provider: str = "tiktok_content_posting"):
    product_id = uuid4()
    experiment_id = uuid4()
    play_id = uuid4()
    identity_id = uuid4()
    action_id = uuid4()
    opportunity_id = uuid4()
    brief_id = uuid4()
    asset_id = uuid4()

    action = DistributionActionView(
        id=action_id,
        platform=DistributionPlatform.TIKTOK,
        opportunity_id=opportunity_id,
        distribution_identity_id=identity_id,
        experiment_id=experiment_id,
        action_type=DistributionActionType.ORGANIC_VIDEO,
        status=DistributionActionStatus.APPROVED,
        automation_level=AutomationLevel.APPROVAL_GATED,
        attribution_level=AttributionLevel.ACTION,
        content_text="A short relationship reflection video.",
    )
    identity = DistributionIdentityView(
        id=identity_id,
        platform=DistributionPlatform.TIKTOK,
        theme="relationship reflection",
        public_positioning="Helpful creator-owned short-form reflections.",
        profile_config={
            "execution_provider": provider,
            "access_token_env": "TIKTOK_CREATOR_TOKEN",
        },
        eligibility={"allowed_actions": [DistributionActionType.ORGANIC_VIDEO.value]},
        status=DistributionIdentityStatus.ACTIVE,
    )
    brief = CreativeBriefView(
        id=brief_id,
        product_id=product_id,
        action_id=action_id,
        experiment_id=experiment_id,
        play_id=play_id,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        content={"product_name": "Oracle"},
        constraints=["Use only confirmed product facts."],
        fingerprint="a" * 64,
        created_at=datetime.now(UTC),
    )
    asset = CreativeAssetView(
        id=asset_id,
        product_id=product_id,
        action_id=action_id,
        brief_id=brief_id,
        brief_fingerprint=brief.fingerprint,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        source=CreativeAssetSource.GENERATED,
        status=CreativeAssetStatus.READY,
        public_url="https://partizan.example/creative.mp4",
        mime_type="video/mp4",
        width=720,
        height=1280,
        duration_seconds=duration_seconds,
        provenance={"generator": "test"},
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    readiness = CreativeReadinessView(
        action_id=action_id,
        brief=brief,
        status=CreativeReadinessStatus.READY,
        selected_asset=asset,
        reasons=["A provider-ready action-level CreativeAsset is available."],
    )

    return SimpleNamespace(
        product_id=product_id,
        action=action,
        identity=identity,
        readiness=readiness,
    )


def _info(*, max_duration: int = 300) -> TikTokCreatorInfo:
    return TikTokCreatorInfo(
        creator_username="creator_123",
        creator_nickname="Oracle Creator",
        creator_avatar_url="https://p16-sign.example/avatar.jpeg",
        privacy_level_options=[
            TikTokPrivacyLevel.PUBLIC_TO_EVERYONE,
            TikTokPrivacyLevel.MUTUAL_FOLLOW_FRIENDS,
            TikTokPrivacyLevel.SELF_ONLY,
        ],
        comment_disabled=False,
        duet_disabled=False,
        stitch_disabled=True,
        max_video_post_duration_sec=max_duration,
    )


def _patch_dependencies(monkeypatch, deps) -> None:
    execution = SimpleNamespace(
        get_action=lambda action_id: deps.action,
        get_experiment=lambda experiment_id: SimpleNamespace(product_id=deps.product_id),
    )
    control = SimpleNamespace(get_identity=lambda identity_id: deps.identity)
    assets = SimpleNamespace(readiness=lambda action_id: deps.readiness)
    monkeypatch.setattr(
        "app.tiktok_owned_publishing.distribution_execution_service",
        execution,
    )
    monkeypatch.setattr(
        "app.tiktok_owned_publishing.distribution_control_plane_service",
        control,
    )
    monkeypatch.setattr(
        "app.tiktok_owned_publishing.creative_asset_service",
        assets,
    )


def test_creator_preflight_binds_action_asset_and_current_creator_capabilities(monkeypatch) -> None:
    deps = _dependencies()
    _patch_dependencies(monkeypatch, deps)
    client = FakeCreatorInfoClient(_info())
    secrets = FakeSecretResolver()
    store = MemoryRuntimeStateStore()
    service = TikTokCreatorPublishPreflightService(
        client=client,
        secret_resolver=secrets,
        store=store,
        ttl_seconds=300,
    )

    snapshot = service.refresh(deps.action.id)

    assert client.tokens == ["creator-secret"]
    assert secrets.names == ["TIKTOK_CREATOR_TOKEN"]
    assert snapshot.action_id == deps.action.id
    assert snapshot.product_id == deps.product_id
    assert snapshot.distribution_identity_id == deps.identity.id
    assert snapshot.creative_asset_id == deps.readiness.selected_asset.id
    assert snapshot.creator_nickname == "Oracle Creator"
    assert snapshot.privacy_level_options == [
        TikTokPrivacyLevel.PUBLIC_TO_EVERYONE,
        TikTokPrivacyLevel.MUTUAL_FOLLOW_FRIENDS,
        TikTokPrivacyLevel.SELF_ONLY,
    ]
    assert snapshot.stitch_disabled is True
    assert snapshot.expires_at > snapshot.fetched_at
    assert service.get_latest(deps.action.id, require_fresh=True).id == snapshot.id
    assert "creator-secret" not in snapshot.model_dump_json()


def test_creator_preflight_fails_closed_without_explicit_provider_opt_in(monkeypatch) -> None:
    deps = _dependencies(provider="not_configured")
    _patch_dependencies(monkeypatch, deps)
    client = FakeCreatorInfoClient(_info())
    service = TikTokCreatorPublishPreflightService(
        client=client,
        secret_resolver=FakeSecretResolver(),
        store=MemoryRuntimeStateStore(),
    )

    with pytest.raises(ValueError, match="explicitly opt in"):
        service.refresh(deps.action.id)

    assert client.tokens == []


def test_creator_preflight_rejects_video_longer_than_creator_limit(monkeypatch) -> None:
    deps = _dependencies(duration_seconds=90)
    _patch_dependencies(monkeypatch, deps)
    client = FakeCreatorInfoClient(_info(max_duration=60))
    service = TikTokCreatorPublishPreflightService(
        client=client,
        secret_resolver=FakeSecretResolver(),
        store=MemoryRuntimeStateStore(),
    )

    with pytest.raises(ValueError, match="maximum post duration"):
        service.refresh(deps.action.id)

    assert client.tokens == ["creator-secret"]


def test_http_client_sends_token_only_in_authorization_header(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "creator_username": "creator_123",
                    "creator_nickname": "Creator",
                    "creator_avatar_url": "https://example.com/avatar.jpeg",
                    "privacy_level_options": ["SELF_ONLY"],
                    "comment_disabled": False,
                    "duet_disabled": True,
                    "stitch_disabled": True,
                    "max_video_post_duration_sec": 120,
                },
                "error": {"code": "ok", "message": "", "log_id": "log_1"},
            }

    def fake_post(url, *, headers, timeout):
        captured.update(url=url, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr("app.tiktok_owned_publishing.httpx.post", fake_post)
    client = HttpxTikTokCreatorInfoClient(timeout_seconds=17)

    result = client.query_creator_info(access_token="top-secret")

    assert result.creator_username == "creator_123"
    assert captured["url"].endswith("/v2/post/publish/creator_info/query/")
    assert captured["headers"]["Authorization"] == "Bearer top-secret"
    assert captured["timeout"] == 17
    assert "top-secret" not in captured["url"]
    assert set(captured) == {"url", "headers", "timeout"}


def test_http_client_sanitizes_provider_rejection(monkeypatch) -> None:
    class Response:
        status_code = 200

        def json(self):
            return {
                "data": {},
                "error": {
                    "code": "scope_not_authorized",
                    "message": "provider detail that should not be surfaced",
                },
            }

    monkeypatch.setattr(
        "app.tiktok_owned_publishing.httpx.post",
        lambda *args, **kwargs: Response(),
    )
    client = HttpxTikTokCreatorInfoClient()

    with pytest.raises(TikTokCreatorInfoApiError) as error:
        client.query_creator_info(access_token="top-secret")

    assert "scope_not_authorized" in str(error.value)
    assert "provider detail" not in str(error.value)
    assert "top-secret" not in str(error.value)
