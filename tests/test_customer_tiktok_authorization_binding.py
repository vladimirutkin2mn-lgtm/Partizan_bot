from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from app.customer_execution_boundary import customer_execution_request_scope
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_direct_post import TikTokDirectPostService
from app.tiktok_owned_publishing import TikTokPrivacyLevel
from app.tiktok_publish_authorization import (
    TikTokPublishAuthorizationCreateRequest,
    TikTokPublishAuthorizationService,
    TikTokPublishAuthorizationStatus,
    TikTokPublishAuthorizationView,
)

REQUEST_ID = UUID("11111111-2222-4333-8444-555555555555")
ACTION_ID = UUID("22222222-3333-4444-8555-666666666666")
EXACT_TITLE = "Exact customer-confirmed TikTok caption"


def _customer_action():
    return SimpleNamespace(
        id=ACTION_ID,
        content_payload={"title": EXACT_TITLE},
        operational_metadata={"customer_execution_request_id": str(REQUEST_ID)},
    )


class _PreflightMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    def get_latest(self, action_id, *, require_fresh=False):
        self.calls += 1
        raise AssertionError("preflight must not run before exact customer title validation")


class _AuthorizationService:
    def __init__(self, authorization: TikTokPublishAuthorizationView) -> None:
        self.authorization = authorization
        self.get_calls = 0
        self.consume_calls = 0

    def get_current(self, action_id, *, require_usable=False):
        assert action_id == ACTION_ID
        self.get_calls += 1
        return self.authorization

    def consume(self, authorization_id):
        self.consume_calls += 1
        return self.authorization


class _ProviderMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    def initialize_video(self, **kwargs):
        self.calls += 1
        raise AssertionError("provider must not run for mismatched customer content")


class _SecretsMustNotRun:
    def __init__(self) -> None:
        self.calls = 0

    def resolve(self, name):
        self.calls += 1
        raise AssertionError("secret resolution must not run for mismatched customer content")


def _authorization(title: str) -> TikTokPublishAuthorizationView:
    now = datetime.now(UTC)
    return TikTokPublishAuthorizationView(
        id=uuid4(),
        action_id=ACTION_ID,
        product_id=uuid4(),
        distribution_identity_id=uuid4(),
        creative_asset_id=uuid4(),
        preflight_id=uuid4(),
        preflight_fingerprint="a" * 64,
        creator_username="creator_123",
        creator_nickname="Creator",
        title=title,
        privacy_level=TikTokPrivacyLevel.SELF_ONLY,
        allow_comment=False,
        allow_duet=False,
        allow_stitch=False,
        commercial_content_enabled=False,
        brand_organic_toggle=False,
        brand_content_toggle=False,
        is_aigc=False,
        music_usage_confirmation_accepted=True,
        branded_content_policy_accepted=False,
        explicit_publish_consent=True,
        status=TikTokPublishAuthorizationStatus.AUTHORIZED,
        authorized_at=now,
        expires_at=now + timedelta(minutes=5),
    )


def test_customer_bound_authorization_rejects_changed_title_before_preflight(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _customer_action()
    monkeypatch.setattr(
        "app.tiktok_publish_authorization.distribution_execution_service",
        SimpleNamespace(get_action=lambda action_id: action),
    )
    preflight = _PreflightMustNotRun()
    service = TikTokPublishAuthorizationService(
        preflight_service=preflight,  # type: ignore[arg-type]
        store=MemoryRuntimeStateStore(),
    )
    payload = TikTokPublishAuthorizationCreateRequest(
        preflight_id=uuid4(),
        title="Operator changed the customer-confirmed caption",
        privacy_level=TikTokPrivacyLevel.SELF_ONLY,
        music_usage_confirmation_accepted=True,
        explicit_publish_consent=True,
    )

    with pytest.raises(ValueError, match="exactly match the customer-confirmed action"):
        service.authorize(ACTION_ID, payload)

    assert preflight.calls == 0


def test_direct_post_rechecks_customer_title_before_any_provider_side_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    action = _customer_action()
    monkeypatch.setattr(
        "app.tiktok_direct_post.distribution_execution_service",
        SimpleNamespace(get_action=lambda action_id: action),
    )
    authorization_service = _AuthorizationService(
        _authorization("Stale or changed authorization caption")
    )
    provider = _ProviderMustNotRun()
    secrets = _SecretsMustNotRun()
    service = TikTokDirectPostService(
        client=provider,  # type: ignore[arg-type]
        authorization_service=authorization_service,  # type: ignore[arg-type]
        secret_resolver=secrets,  # type: ignore[arg-type]
        store=MemoryRuntimeStateStore(),
    )

    with customer_execution_request_scope(REQUEST_ID):
        with pytest.raises(ValueError, match="no longer matches the customer-confirmed action"):
            service.submit(ACTION_ID)

    assert authorization_service.get_calls == 2
    assert authorization_service.consume_calls == 0
    assert secrets.calls == 0
    assert provider.calls == 0
