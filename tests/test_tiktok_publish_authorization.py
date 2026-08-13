from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.creative_assets import (
    CreativeAssetSource,
    CreativeAssetStatus,
    CreativeAssetView,
    CreativeMediaType,
    CreativePurpose,
)
from app.distribution_types import DistributionPlatform
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_owned_publishing import (
    TikTokCreatorPublishPreflightView,
    TikTokPrivacyLevel,
)
from app.tiktok_publish_authorization import (
    TIKTOK_PUBLISH_AUTHORIZATION_NAMESPACE,
    TikTokPublishAuthorizationCreateRequest,
    TikTokPublishAuthorizationService,
    TikTokPublishAuthorizationStatus,
)


class FakePreflightService:
    def __init__(self, snapshot: TikTokCreatorPublishPreflightView) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[object, bool]] = []

    def get_latest(self, action_id, *, require_fresh=False):
        self.calls.append((action_id, require_fresh))
        return self.snapshot


def _snapshot():
    now = datetime.now(UTC)
    return TikTokCreatorPublishPreflightView(
        id=uuid4(),
        action_id=uuid4(),
        product_id=uuid4(),
        distribution_identity_id=uuid4(),
        creative_asset_id=uuid4(),
        creator_username="creator_123",
        creator_nickname="Oracle Creator",
        privacy_level_options=[
            TikTokPrivacyLevel.PUBLIC_TO_EVERYONE,
            TikTokPrivacyLevel.MUTUAL_FOLLOW_FRIENDS,
            TikTokPrivacyLevel.SELF_ONLY,
        ],
        comment_disabled=False,
        duet_disabled=False,
        stitch_disabled=True,
        max_video_post_duration_sec=300,
        fingerprint="b" * 64,
        fetched_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def _asset(snapshot, *, source=CreativeAssetSource.GENERATED):
    now = datetime.now(UTC)
    return CreativeAssetView(
        id=snapshot.creative_asset_id,
        product_id=snapshot.product_id,
        action_id=snapshot.action_id,
        brief_id=uuid4(),
        brief_fingerprint="c" * 64,
        platform=DistributionPlatform.TIKTOK,
        purpose=CreativePurpose.ORGANIC_VIDEO,
        media_type=CreativeMediaType.VIDEO,
        source=source,
        status=CreativeAssetStatus.READY,
        public_url="https://partizan.example/generated-video.mp4",
        mime_type="video/mp4",
        width=720,
        height=1280,
        duration_seconds=8,
        provenance={"generator": "test"},
        created_at=now,
        updated_at=now,
    )


def _patch_asset(monkeypatch, asset):
    monkeypatch.setattr(
        "app.tiktok_publish_authorization.creative_asset_service",
        type("Assets", (), {"get_asset": staticmethod(lambda asset_id: asset)})(),
    )


def _payload(snapshot, **updates):
    values = {
        "preflight_id": snapshot.id,
        "title": "A calm relationship reflection #oracle",
        "privacy_level": TikTokPrivacyLevel.MUTUAL_FOLLOW_FRIENDS,
        "allow_comment": False,
        "allow_duet": False,
        "allow_stitch": False,
        "commercial_content_enabled": True,
        "brand_organic_toggle": True,
        "brand_content_toggle": False,
        "is_aigc": True,
        "music_usage_confirmation_accepted": True,
        "branded_content_policy_accepted": False,
        "explicit_publish_consent": True,
    }
    values.update(updates)
    return TikTokPublishAuthorizationCreateRequest(**values)


def _service(snapshot):
    store = MemoryRuntimeStateStore()
    preflight = FakePreflightService(snapshot)
    return TikTokPublishAuthorizationService(
        preflight_service=preflight,  # type: ignore[arg-type]
        store=store,
    ), preflight, store


def test_authorization_binds_exact_fresh_preflight_asset_and_creator_choices(monkeypatch) -> None:
    snapshot = _snapshot()
    asset = _asset(snapshot)
    _patch_asset(monkeypatch, asset)
    service, preflight, _ = _service(snapshot)

    authorization = service.authorize(snapshot.action_id, _payload(snapshot))

    assert authorization.status == TikTokPublishAuthorizationStatus.AUTHORIZED
    assert authorization.action_id == snapshot.action_id
    assert authorization.preflight_id == snapshot.id
    assert authorization.preflight_fingerprint == snapshot.fingerprint
    assert authorization.creative_asset_id == asset.id
    assert authorization.creator_username == snapshot.creator_username
    assert authorization.privacy_level == TikTokPrivacyLevel.MUTUAL_FOLLOW_FRIENDS
    assert authorization.brand_organic_toggle is True
    assert authorization.brand_content_toggle is False
    assert authorization.is_aigc is True
    assert authorization.music_usage_confirmation_accepted is True
    assert authorization.explicit_publish_consent is True
    assert authorization.expires_at == snapshot.expires_at
    assert preflight.calls == [(snapshot.action_id, True)]


def test_authorization_rejects_privacy_not_returned_by_creator_info(monkeypatch) -> None:
    snapshot = _snapshot()
    snapshot = snapshot.model_copy(
        update={"privacy_level_options": [TikTokPrivacyLevel.SELF_ONLY]}
    )
    _patch_asset(monkeypatch, _asset(snapshot))
    service, _, _ = _service(snapshot)

    with pytest.raises(ValueError, match="privacy level is not available"):
        service.authorize(
            snapshot.action_id,
            _payload(snapshot, privacy_level=TikTokPrivacyLevel.PUBLIC_TO_EVERYONE),
        )


def test_authorization_rejects_interaction_disabled_by_creator_settings(monkeypatch) -> None:
    snapshot = _snapshot().model_copy(update={"comment_disabled": True})
    _patch_asset(monkeypatch, _asset(snapshot))
    service, _, _ = _service(snapshot)

    with pytest.raises(ValueError, match="disable comments"):
        service.authorize(snapshot.action_id, _payload(snapshot, allow_comment=True))


def test_authorization_enforces_commercial_disclosure_rules(monkeypatch) -> None:
    snapshot = _snapshot()
    _patch_asset(monkeypatch, _asset(snapshot))
    service, _, _ = _service(snapshot)

    with pytest.raises(ValueError, match="Commercial content requires"):
        service.authorize(
            snapshot.action_id,
            _payload(
                snapshot,
                commercial_content_enabled=True,
                brand_organic_toggle=False,
                brand_content_toggle=False,
            ),
        )

    with pytest.raises(ValueError, match="cannot use SELF_ONLY"):
        service.authorize(
            snapshot.action_id,
            _payload(
                snapshot,
                privacy_level=TikTokPrivacyLevel.SELF_ONLY,
                brand_organic_toggle=False,
                brand_content_toggle=True,
                branded_content_policy_accepted=True,
            ),
        )

    with pytest.raises(ValueError, match="Branded Content Policy"):
        service.authorize(
            snapshot.action_id,
            _payload(
                snapshot,
                brand_organic_toggle=False,
                brand_content_toggle=True,
                branded_content_policy_accepted=False,
            ),
        )


def test_authorization_requires_user_confirmations_and_aigc_disclosure(monkeypatch) -> None:
    snapshot = _snapshot()
    _patch_asset(monkeypatch, _asset(snapshot))
    service, _, _ = _service(snapshot)

    with pytest.raises(ValueError, match="Music Usage Confirmation"):
        service.authorize(
            snapshot.action_id,
            _payload(snapshot, music_usage_confirmation_accepted=False),
        )
    with pytest.raises(ValueError, match="Explicit creator consent"):
        service.authorize(
            snapshot.action_id,
            _payload(snapshot, explicit_publish_consent=False),
        )
    with pytest.raises(ValueError, match="AIGC disclosure"):
        service.authorize(snapshot.action_id, _payload(snapshot, is_aigc=False))


def test_external_human_asset_does_not_force_aigc_flag(monkeypatch) -> None:
    snapshot = _snapshot()
    _patch_asset(monkeypatch, _asset(snapshot, source=CreativeAssetSource.EXTERNAL_URL))
    service, _, _ = _service(snapshot)

    authorization = service.authorize(
        snapshot.action_id,
        _payload(snapshot, is_aigc=False),
    )

    assert authorization.is_aigc is False


def test_title_limit_uses_utf16_units(monkeypatch) -> None:
    snapshot = _snapshot()
    _patch_asset(monkeypatch, _asset(snapshot))
    service, _, _ = _service(snapshot)

    with pytest.raises(ValueError, match="2200 UTF-16"):
        service.authorize(snapshot.action_id, _payload(snapshot, title="😀" * 1101))


def test_new_authorization_revokes_previous_and_consume_is_one_shot(monkeypatch) -> None:
    snapshot = _snapshot()
    _patch_asset(monkeypatch, _asset(snapshot))
    service, _, store = _service(snapshot)

    first = service.authorize(snapshot.action_id, _payload(snapshot, title="First"))
    second = service.authorize(snapshot.action_id, _payload(snapshot, title="Second"))

    first_payload = store.get(TIKTOK_PUBLISH_AUTHORIZATION_NAMESPACE, str(first.id))
    assert first_payload is not None
    assert first_payload["status"] == TikTokPublishAuthorizationStatus.REVOKED.value
    assert service.get_current(snapshot.action_id).id == second.id

    consumed = service.consume(second.id)
    assert consumed.status == TikTokPublishAuthorizationStatus.CONSUMED
    assert consumed.consumed_at is not None
    with pytest.raises(ValueError, match="one-shot use"):
        service.consume(second.id)
