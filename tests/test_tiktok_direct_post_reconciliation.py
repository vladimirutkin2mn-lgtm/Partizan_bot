from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_schemas import DistributionIdentityView
from app.distribution_types import (
    AttributionLevel,
    AutomationLevel,
    DistributionActionStatus,
    DistributionActionType,
    DistributionIdentityStatus,
    DistributionPlatform,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.tiktok_direct_post import (
    TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
    TikTokContentPostingAuditStatus,
    TikTokDirectPostAttemptStatus,
    TikTokDirectPostAttemptView,
)
from app.tiktok_direct_post_reconciliation import (
    HttpxTikTokPostStatusClient,
    TikTokDirectPostReconciliationService,
    TikTokDirectPostReconciliationStatus,
    TikTokPostStatusApiError,
    TikTokPostStatusView,
    TikTokProviderPostStatus,
)


class FakeDirectPostService:
    def __init__(self, attempt: TikTokDirectPostAttemptView) -> None:
        self.attempt = attempt

    def get_latest(self, action_id):
        assert action_id == self.attempt.action_id
        return self.attempt


class FakeStatusClient:
    def __init__(self, status: TikTokPostStatusView) -> None:
        self.status = status
        self.calls: list[dict] = []

    def fetch_status(self, **kwargs):
        self.calls.append(kwargs)
        return self.status


def _attempt(*, status=TikTokDirectPostAttemptStatus.SUBMITTED):
    now = datetime.now(UTC)
    return TikTokDirectPostAttemptView(
        id=uuid4(),
        action_id=uuid4(),
        authorization_id=uuid4(),
        distribution_identity_id=uuid4(),
        creative_asset_id=uuid4(),
        client_audit_status=TikTokContentPostingAuditStatus.AUDITED,
        status=status,
        provider_publish_id="v_pub_url~v2.123456",
        started_at=now,
        updated_at=now,
    )


def _identity(attempt):
    return DistributionIdentityView(
        id=attempt.distribution_identity_id,
        platform=DistributionPlatform.TIKTOK,
        theme="relationship reflection",
        public_positioning="Creator-owned organic content.",
        profile_config={
            "execution_provider": "tiktok_content_posting",
            "access_token_env": "TIKTOK_CREATOR_TOKEN",
        },
        eligibility={"allowed_actions": [DistributionActionType.ORGANIC_VIDEO.value]},
        status=DistributionIdentityStatus.ACTIVE,
    )


class FakeExecutionService:
    def __init__(self, action_id) -> None:
        self.action = SimpleNamespace(
            id=action_id,
            status=DistributionActionStatus.APPROVED,
        )
        self.mark_calls: list[tuple[object, object]] = []

    def get_action(self, action_id):
        assert action_id == self.action.id
        return self.action

    def mark_executed(self, action_id, payload):
        self.mark_calls.append((action_id, payload))
        self.action = SimpleNamespace(
            id=action_id,
            status=DistributionActionStatus.EXECUTED,
        )
        return SimpleNamespace(action=self.action)


def _patch_dependencies(monkeypatch, attempt, execution):
    identity = _identity(attempt)
    monkeypatch.setattr(
        "app.tiktok_direct_post_reconciliation.distribution_control_plane_service",
        type("Control", (), {"get_identity": staticmethod(lambda identity_id: identity)})(),
    )
    monkeypatch.setattr(
        "app.tiktok_direct_post_reconciliation.distribution_execution_service",
        execution,
    )
    monkeypatch.setenv("TIKTOK_CREATOR_TOKEN", "creator-secret")


def _service(attempt, provider_status, *, store=None):
    store = store or MemoryRuntimeStateStore()
    client = FakeStatusClient(provider_status)
    service = TikTokDirectPostReconciliationService(
        client=client,
        direct_post_service=FakeDirectPostService(attempt),  # type: ignore[arg-type]
        store=store,
    )
    return service, client, store


def test_http_status_client_posts_publish_id_and_parses_public_post_ids(monkeypatch) -> None:
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "data": {
                    "status": "PUBLISH_COMPLETE",
                    "publicaly_available_post_id": [123456789, "987654321"],
                    "downloaded_bytes": 4567,
                },
                "error": {"code": "ok", "message": "", "log_id": "log_1"},
            }

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return Response()

    monkeypatch.setattr(
        "app.tiktok_direct_post_reconciliation.httpx.post",
        fake_post,
    )
    client = HttpxTikTokPostStatusClient(timeout_seconds=13)

    result = client.fetch_status(
        access_token="top-secret",
        publish_id="v_pub_url~v2.123",
    )

    assert result.status == TikTokProviderPostStatus.PUBLISH_COMPLETE
    assert result.public_post_ids == ["123456789", "987654321"]
    assert result.downloaded_bytes == 4567
    assert captured["url"].endswith("/v2/post/publish/status/fetch/")
    assert captured["headers"]["Authorization"] == "Bearer top-secret"
    assert captured["json"] == {"publish_id": "v_pub_url~v2.123"}
    assert captured["timeout"] == 13
    assert "top-secret" not in str(captured["json"])


def test_http_status_client_sanitizes_provider_error(monkeypatch) -> None:
    class Response:
        status_code = 400

        def json(self):
            return {
                "data": {},
                "error": {
                    "code": "invalid_publish_id",
                    "message": "provider internal detail",
                },
            }

    monkeypatch.setattr(
        "app.tiktok_direct_post_reconciliation.httpx.post",
        lambda *args, **kwargs: Response(),
    )
    client = HttpxTikTokPostStatusClient()

    with pytest.raises(TikTokPostStatusApiError) as error:
        client.fetch_status(
            access_token="top-secret",
            publish_id="bad-id",
        )

    assert "invalid_publish_id" in str(error.value)
    assert "provider internal detail" not in str(error.value)
    assert "top-secret" not in str(error.value)


def test_processing_status_does_not_mark_action_executed(monkeypatch) -> None:
    attempt = _attempt()
    execution = FakeExecutionService(attempt.action_id)
    _patch_dependencies(monkeypatch, attempt, execution)
    service, client, _ = _service(
        attempt,
        TikTokPostStatusView(
            status=TikTokProviderPostStatus.PROCESSING_DOWNLOAD,
            downloaded_bytes=1200,
        ),
    )

    result = service.reconcile(attempt.action_id)

    assert result.status == TikTokDirectPostReconciliationStatus.PROCESSING
    assert result.provider_status == TikTokProviderPostStatus.PROCESSING_DOWNLOAD
    assert result.downloaded_bytes == 1200
    assert execution.mark_calls == []
    assert client.calls == [
        {
            "access_token": "creator-secret",
            "publish_id": attempt.provider_publish_id,
        }
    ]


def test_publish_complete_marks_action_executed_even_without_public_post_id(monkeypatch) -> None:
    attempt = _attempt()
    execution = FakeExecutionService(attempt.action_id)
    _patch_dependencies(monkeypatch, attempt, execution)
    service, _, _ = _service(
        attempt,
        TikTokPostStatusView(status=TikTokProviderPostStatus.PUBLISH_COMPLETE),
    )

    result = service.reconcile(attempt.action_id)

    assert result.status == TikTokDirectPostReconciliationStatus.PUBLISHED
    assert result.public_post_ids == []
    assert len(execution.mark_calls) == 1
    action_id, payload = execution.mark_calls[0]
    assert action_id == attempt.action_id
    assert payload.external_reference == attempt.provider_publish_id
    assert "PUBLISH_COMPLETE" in payload.notes


def test_publish_complete_persists_real_public_post_ids_when_available(monkeypatch) -> None:
    attempt = _attempt()
    execution = FakeExecutionService(attempt.action_id)
    _patch_dependencies(monkeypatch, attempt, execution)
    service, _, _ = _service(
        attempt,
        TikTokPostStatusView(
            status=TikTokProviderPostStatus.PUBLISH_COMPLETE,
            public_post_ids=["741852963"],
        ),
    )

    result = service.reconcile(attempt.action_id)

    assert result.public_post_ids == ["741852963"]
    assert "741852963" in execution.mark_calls[0][1].notes


def test_failed_provider_status_never_marks_executed_and_terminates_attempt(monkeypatch) -> None:
    attempt = _attempt()
    execution = FakeExecutionService(attempt.action_id)
    _patch_dependencies(monkeypatch, attempt, execution)
    store = MemoryRuntimeStateStore()
    store.put(
        TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE,
        str(attempt.id),
        attempt.model_dump(mode="json"),
    )
    service, _, _ = _service(
        attempt,
        TikTokPostStatusView(
            status=TikTokProviderPostStatus.FAILED,
            fail_reason="duration_check_failed",
        ),
        store=store,
    )

    result = service.reconcile(attempt.action_id)

    assert result.status == TikTokDirectPostReconciliationStatus.FAILED
    assert result.fail_reason == "duration_check_failed"
    assert execution.mark_calls == []
    persisted = store.get(TIKTOK_DIRECT_POST_ATTEMPT_NAMESPACE, str(attempt.id))
    assert persisted is not None
    assert persisted["status"] == TikTokDirectPostAttemptStatus.REJECTED.value
    assert persisted["provider_error_code"] == "duration_check_failed"


def test_reconcile_requires_real_publish_id_before_status_poll(monkeypatch) -> None:
    attempt = _attempt().model_copy(update={"provider_publish_id": None})
    execution = FakeExecutionService(attempt.action_id)
    _patch_dependencies(monkeypatch, attempt, execution)
    service, client, _ = _service(
        attempt,
        TikTokPostStatusView(status=TikTokProviderPostStatus.PUBLISH_COMPLETE),
    )

    with pytest.raises(ValueError, match="no confirmed publish_id"):
        service.reconcile(attempt.action_id)

    assert client.calls == []
    assert execution.mark_calls == []


def test_repeated_publish_complete_does_not_mark_executed_twice(monkeypatch) -> None:
    attempt = _attempt()
    execution = FakeExecutionService(attempt.action_id)
    _patch_dependencies(monkeypatch, attempt, execution)
    service, _, _ = _service(
        attempt,
        TikTokPostStatusView(status=TikTokProviderPostStatus.PUBLISH_COMPLETE),
    )

    first = service.reconcile(attempt.action_id)
    second = service.reconcile(attempt.action_id)

    assert first.status == TikTokDirectPostReconciliationStatus.PUBLISHED
    assert second.status == TikTokDirectPostReconciliationStatus.PUBLISHED
    assert len(execution.mark_calls) == 1
