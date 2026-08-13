from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest

from app.creative_assets import (
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeMediaType,
    CreativePurpose,
)
from app.distribution_schemas import DistributionIdentityView
from app.distribution_types import (
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_direct_post import (
    TIKTOK_DIRECT_POST_ACTION_NAMESPACE,
    TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
    HttpxTikTokDirectPostClient,
    TikTokContentPostingAuditStatus,
    TikTokDirectPostApiError,
    TikTokDirectPostAttemptStatus,
    TikTokDirectPostAttemptView,
    TikTokDirectPostService,
)
from app.tiktok_publish_authorization import (
    TikTokPublishAuthorizationStatus,
    TikTokPublishAuthorizationView,
    TikTokPrivacyLevel,
)


class FakeAuthorizationService:
    def __init__(self, authorization: TikTokPublishAuthorizationView) -> None:
        self.current = authorization
        self.get_calls: list[tuple[object, bool]] = []
        self.consume_calls: list[object] = []
        self.fail_consume = False

    def get_current(self, action_id, *, require_usable=False):
        self.get_calls.append((action_id, require_usable))
        return self.current

    def consume(self, authorization_id):
        self.consume_calls.append(authorization_id)
        if self.fail_consume:
            raise ValueError("authorization changed")
        self.current = self.current.model_copy(
            update={
                "status": TikTokPublishAuthorizationStatus.CONSUMED,
                "consumed_at": datetime.now(UTC),
            }
        )
        return self.current


class FakeSecretResolver:
    def __init__(self, token="creator-secret") -> None:
        self.token = token
        self.names: list[str] = []

    def resolve(self, name):
        self.names.append(name)
        return self.token


class FakeDirectPostClient:
    def __init__(self, *, publish_id="v_pub_url~v2.123", error=None) -> None:
        self.publish_id = publish_id
        self.error = error
        self.calls: list[dict] = []

    def initialize_video(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.publish_id


def _authorization(*, privacy=TikTokPrivacyLevel.MUTUAL_FOLLOW_FRIENDS):
    now = datetime.now(UTC)
    return TikTokPublishAuthorizationView(
        id=uuid4(),
        action_id=uuid4(),
        product_id=uuid4(),
        distribution_identity_id=uuid4(),
        creative_asset_id=uuid4(),
        preflight_id=uuid4(),
        preflight_fingerprint="a" * 64,
        creator_username="creator_123",
        creator_nickname="Oracle Creator",
        title="A calm relationship reflection #oracle",
        privacy_level=privacy,
        allow_comment=True,
        allow_duet=False,
        allow_stitch=False,
        commercial_content_enabled=True,
        brand_organic_toggle=True,
        brand_content_toggle=False,
        is_aigc=True,
        music_usage_confirmation_accepted=True,
        branded_content_policy_accepted=False,
        explicit_publish_consent=True,
        status=TikTokPublishAuthorizationStatus.AUTHORIZED,
        authorized_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def _identity(authorization, *, audit="AUDITED", prefix="https://partizan.example"):
    return DistributionIdentityView(
        id=authorization.distribution_identity_id,
        platform=DistributionPlatform.TIKTOK,
        theme="relationship reflection",
        public_positioning="Creator-owned relationship reflection content.",
        profile_config={
            "execution_provider": "tiktok_content_posting",
            "access_token_env": "TIKTOK_CREATOR_TOKEN",
            "content_posting_audit_status": audit,
            "verified_url_prefix": prefix,
        },
        eligibility={"allowed_actions": [DistributionActionType.ORGANIC_VIDEO.value]},
        status=DistributionIdentityStatus.ACTIVE,
    )


def _asset(authorization, *, url="https://partizan.example/v1/public/video/123.mp4"):
    now = datetime.now(UTC)
    return CreativeAssetView(
        id=authorization.creative_asset_id,
        product_id=authorization.product_id,
        action_id=authorization.action_id,
        brief_id=uuid4(),
        brief_fingerprint="b" * 64,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        source=CreativeAssetSource.GENERATED,
        status=CreativeAssetStatus.READY,
        public_url=url,
        mime_type="video/mp4",
        width=720,
        height=1280,
        duration_seconds=8,
        provenance={"generator": "gemini_omni"},
        created_at=now,
        updated_at=now,
    )


def _patch_dependencies(monkeypatch, identity, asset):
    monkeypatch.setattr(
        "app.tiktok_direct_post.distribution_control_plane_service",
        type("Control", (), {"get_identity": staticmethod(lambda identity_id: identity)})(),
    )
    monkeypatch.setattr(
        "app.tiktok_direct_post.creative_asset_service",
        type("Assets", (), {"get_asset": staticmethod(lambda asset_id: asset)})(),
    )


def _service(authorization, *, client=None, secrets=None, store=None):
    auth = FakeAuthorizationService(authorization)
    client = client or FakeDirectPostClient()
    secrets = secrets or FakeSecretResolver()
    store = store or MemoryRuntimeStateStore()
    service = TikTokDirectPostService(
        client=client,
        authorization_service=auth,  # type: ignore[arg-type]
        secret_resolver=secrets,
        store=store,
    )
    return service, auth, client, secrets, store


def test_http_client_builds_exact_direct_post_pull_from_url_payload(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "data": {"publish_id": "v_pub_url~v2.777"},
                "error": {"code": "ok", "message": "", "log_id": "log_1"},
            }

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr("app.tiktok_direct_post.httpx.post", fake_post)
    authorization = _authorization()
    client = HttpxTikTokDirectPostClient(timeout_seconds=19)

    publish_id = client.initialize_video(
        access_token="top-secret",
        authorization=authorization,
        video_url="https://partizan.example/v1/public/video/123.mp4",
    )

    assert publish_id == "v_pub_url~v2.777"
    assert captured["url"].endswith("/v2/post/publish/video/init/")
    assert captured["headers"]["Authorization"] == "Bearer top-secret"
    assert captured["timeout"] == 19
    assert captured["json"] == {
        "post_info": {
            "title": authorization.title,
            "privacy_level": "MUTUAL_FOLLOW_FRIENDS",
            "disable_duet": True,
            "disable_comment": False,
            "disable_stitch": True,
            "brand_content_toggle": False,
            "brand_organic_toggle": True,
            "is_aigc": True,
        },
        "source_info": {
            "source": "PULL_FROM_URL",
            "video_url": "https://partizan.example/v1/public/video/123.mp4",
        },
    }
    assert "top-secret" not in str(captured["json"])


def test_http_client_marks_network_and_missing_publish_id_as_ambiguous(monkeypatch) -> None:
    authorization = _authorization()
    client = HttpxTikTokDirectPostClient()

    def fail_network(*args, **kwargs):
        raise httpx.ConnectError("network failed")

    monkeypatch.setattr("app.tiktok_direct_post.httpx.post", fail_network)
    with pytest.raises(TikTokDirectPostApiError) as network_error:
        client.initialize_video(
            access_token="secret",
            authorization=authorization,
            video_url="https://partizan.example/video.mp4",
        )
    assert network_error.value.ambiguous is True

    class MissingIdResponse:
        status_code = 200

        def json(self):
            return {"data": {}, "error": {"code": "ok", "message": ""}}

    monkeypatch.setattr(
        "app.tiktok_direct_post.httpx.post",
        lambda *args, **kwargs: MissingIdResponse(),
    )
    with pytest.raises(TikTokDirectPostApiError) as missing_id:
        client.initialize_video(
            access_token="secret",
            authorization=authorization,
            video_url="https://partizan.example/video.mp4",
        )
    assert missing_id.value.ambiguous is True
    assert missing_id.value.code == "missing_publish_id"


def test_submit_persists_attempt_consumes_authorization_and_records_real_publish_id(monkeypatch) -> None:
    authorization = _authorization()
    identity = _identity(authorization)
    asset = _asset(authorization)
    _patch_dependencies(monkeypatch, identity, asset)
    service, auth, client, secrets, _ = _service(authorization)

    result = service.submit(authorization.action_id)

    assert result.status == TikTokDirectPostAttemptStatus.SUBMITTED
    assert result.provider_publish_id == "v_pub_url~v2.123"
    assert result.authorization_id == authorization.id
    assert result.creative_asset_id == asset.id
    assert result.client_audit_status == TikTokContentPostingAuditStatus.AUDITED
    assert auth.consume_calls == [authorization.id]
    assert len(client.calls) == 1
    assert client.calls[0]["access_token"] == "creator-secret"
    assert client.calls[0]["video_url"] == str(asset.public_url)
    assert secrets.names == ["TIKTOK_CREATOR_TOKEN"]
    assert "creator-secret" not in result.model_dump_json()


def test_ambiguous_provider_result_is_never_retried_blindly(monkeypatch) -> None:
    authorization = _authorization()
    _patch_dependencies(monkeypatch, _identity(authorization), _asset(authorization))
    client = FakeDirectPostClient(
        error=TikTokDirectPostApiError(
            "ambiguous",
            code="network_error",
            ambiguous=True,
        )
    )
    service, _, _, _, _ = _service(authorization, client=client)

    first = service.submit(authorization.action_id)
    second = service.submit(authorization.action_id)

    assert first.status == TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED
    assert second.id == first.id
    assert len(client.calls) == 1


def test_interrupted_started_attempt_moves_to_reconciliation_without_provider_call(monkeypatch) -> None:
    authorization = _authorization()
    _patch_dependencies(monkeypatch, _identity(authorization), _asset(authorization))
    store = MemoryRuntimeStateStore()
    now = datetime.now(UTC)
    attempt = TikTokDirectPostAttemptView(
        id=uuid4(),
        action_id=authorization.action_id,
        authorization_id=authorization.id,
        distribution_identity_id=authorization.distribution_identity_id,
        creative_asset_id=authorization.creative_asset_id,
        client_audit_status=TikTokContentPostingAuditStatus.AUDITED,
        status=TikTokDirectPostAttemptStatus.STARTED,
        started_at=now,
        updated_at=now,
    )
    store.put(
        TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
        str(attempt.id),
        attempt.model_dump(mode="json"),
    )
    store.put(
        TIKTOK_DIRECT_POST_ACTION_NAMESPACE,
        str(authorization.action_id),
        {"attempt_id": str(attempt.id)},
    )
    service, _, client, _, _ = _service(authorization, store=store)

    result = service.submit(authorization.action_id)

    assert result.status == TikTokDirectPostAttemptStatus.RECONCILIATION_REQUIRED
    assert result.provider_error_code == "interrupted_after_attempt_started"
    assert client.calls == []


def test_definitive_provider_rejection_is_not_retried_with_same_authorization(monkeypatch) -> None:
    authorization = _authorization()
    _patch_dependencies(monkeypatch, _identity(authorization), _asset(authorization))
    client = FakeDirectPostClient(
        error=TikTokDirectPostApiError(
            "rejected",
            code="url_ownership_unverified",
            ambiguous=False,
        )
    )
    service, _, _, _, _ = _service(authorization, client=client)

    first = service.submit(authorization.action_id)
    second = service.submit(authorization.action_id)

    assert first.status == TikTokDirectPostAttemptStatus.REJECTED
    assert first.provider_error_code == "url_ownership_unverified"
    assert second.id == first.id
    assert len(client.calls) == 1


def test_submit_blocks_unverified_url_and_unaudited_non_private_before_mutation(monkeypatch) -> None:
    authorization = _authorization()
    outside_asset = _asset(
        authorization,
        url="https://attacker.example/v1/public/video.mp4",
    )
    _patch_dependencies(monkeypatch, _identity(authorization), outside_asset)
    service, auth, client, _, _ = _service(authorization)

    with pytest.raises(ValueError, match="outside the configured verified URL prefix"):
        service.submit(authorization.action_id)
    assert auth.consume_calls == []
    assert client.calls == []

    unaudited = _authorization(privacy=TikTokPrivacyLevel.PUBLIC_TO_EVERYONE)
    _patch_dependencies(monkeypatch, _identity(unaudited, audit="UNAUDITED"), _asset(unaudited))
    service2, auth2, client2, _, _ = _service(unaudited)

    with pytest.raises(ValueError, match="require SELF_ONLY"):
        service2.submit(unaudited.action_id)
    assert auth2.consume_calls == []
    assert client2.calls == []


def test_invalidated_authorization_after_attempt_start_blocks_without_provider_call(monkeypatch) -> None:
    authorization = _authorization()
    _patch_dependencies(monkeypatch, _identity(authorization), _asset(authorization))
    service, auth, client, _, _ = _service(authorization)
    auth.fail_consume = True

    result = service.submit(authorization.action_id)

    assert result.status == TikTokDirectPostAttemptStatus.BLOCKED
    assert result.provider_error_code == "authorization_invalidated_before_provider_call"
    assert len(auth.consume_calls) == 1
    assert client.calls == []
