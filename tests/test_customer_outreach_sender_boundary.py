from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

import app.outreach_sender as outreach_sender_module
from app.distribution_types import DistributionActionStatus
from app.outreach_briefs import OutreachBriefStatus
from app.outreach_sender import (
    OUTREACH_SEND_ATTEMPT_NAMESPACE,
    OUTREACH_SEND_AUTH_BRIEF_NAMESPACE,
    OUTREACH_SEND_AUTH_NAMESPACE,
    OutreachSendAuthorizationCreateRequest,
    OutreachSendAuthorizationStatus,
    OutreachSendAuthorizationView,
    OutreachSenderReadinessView,
    OutreachSenderService,
    _exact_message,
    _message_fingerprint,
)
from app.runtime_store import MemoryRuntimeStateStore

TRACKING_URL = "https://partizan.example/t/outreach-boundary"
CONTENT = f"Subject: Useful idea\n\nA concise useful note. {TRACKING_URL}"
RECIPIENT = "founder@example.com"
SENDER = "growth@partizan.example"
REPLY_TO = "reply@partizan.example"


class _ExecutionService:
    def __init__(self, action) -> None:
        self.action = action
        self.get_action_calls: list[UUID] = []
        self.approve_calls = 0
        self.mark_calls = 0

    def get_action(self, action_id: UUID):
        assert action_id == self.action.id
        self.get_action_calls.append(action_id)
        return self.action

    def get_experiment(self, _experiment_id: UUID):
        raise AssertionError("Customer-bound authorization must fail before experiment lookup")

    def approve_outreach(self, _action_id: UUID):
        self.approve_calls += 1
        raise AssertionError("Customer-bound outreach must not use generic approval")

    def mark_executed(self, *_args, **_kwargs):
        self.mark_calls += 1
        raise AssertionError("Customer-bound outreach must not use generic completion")


class _ProviderSpy:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def send(self, **kwargs) -> str:
        self.calls.append(kwargs)
        raise AssertionError("Customer-bound outreach must not reach SMTP")


def _readiness() -> OutreachSenderReadinessView:
    return OutreachSenderReadinessView(
        provider="SMTP",
        ready=True,
        from_email=SENDER,
        from_name="Partizan Growth",
        reply_to=REPLY_TO,
        starttls=True,
        auth_configured=True,
    )


def _action(status: DistributionActionStatus):
    return SimpleNamespace(
        id=uuid4(),
        experiment_id=uuid4(),
        status=status,
        content_text=CONTENT,
        operational_metadata={"customer_execution_request_id": str(uuid4())},
    )


def _patch_context(monkeypatch, *, action, execution) -> tuple[UUID, UUID]:
    brief_id = uuid4()
    target_id = uuid4()
    brief = SimpleNamespace(
        id=brief_id,
        action_id=action.id,
        experiment_id=action.experiment_id,
        outreach_target_id=target_id,
        tracking_url=TRACKING_URL,
        status=OutreachBriefStatus.DRAFT,
    )
    target = SimpleNamespace(contact_key=RECIPIENT.casefold())
    monkeypatch.setattr(
        outreach_sender_module,
        "outreach_brief_service",
        SimpleNamespace(get=lambda candidate: brief if candidate == brief_id else None),
    )
    monkeypatch.setattr(
        outreach_sender_module,
        "outreach_target_service",
        SimpleNamespace(
            require_executable=lambda candidate: target if candidate == target_id else None
        ),
    )
    monkeypatch.setattr(
        outreach_sender_module,
        "distribution_execution_service",
        execution,
    )
    return brief_id, target_id


def test_customer_bound_outreach_cannot_create_send_authorization(monkeypatch) -> None:
    action = _action(DistributionActionStatus.PREPARED)
    execution = _ExecutionService(action)
    brief_id, _ = _patch_context(monkeypatch, action=action, execution=execution)
    store = MemoryRuntimeStateStore()
    service = OutreachSenderService(store=store)
    monkeypatch.setattr(service, "readiness", _readiness)

    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        service.authorize(
            brief_id,
            OutreachSendAuthorizationCreateRequest(
                recipient_email=RECIPIENT,
                confirm_one_initial_message=True,
            ),
        )

    assert execution.get_action_calls == [action.id]
    assert store.list_namespace(OUTREACH_SEND_AUTH_NAMESPACE) == []
    assert store.list_namespace(OUTREACH_SEND_AUTH_BRIEF_NAMESPACE) == []


@pytest.mark.asyncio
async def test_already_approved_customer_bound_outreach_never_reaches_smtp(monkeypatch) -> None:
    action = _action(DistributionActionStatus.APPROVED)
    execution = _ExecutionService(action)
    brief_id, target_id = _patch_context(monkeypatch, action=action, execution=execution)
    store = MemoryRuntimeStateStore()
    provider = _ProviderSpy()
    service = OutreachSenderService(store=store, provider=provider)  # type: ignore[arg-type]
    monkeypatch.setattr(service, "readiness", _readiness)

    subject, body = _exact_message(CONTENT, TRACKING_URL)
    fingerprint = _message_fingerprint(
        recipient=RECIPIENT,
        sender=SENDER,
        reply_to=REPLY_TO,
        subject=subject,
        body=body,
    )
    authorization = OutreachSendAuthorizationView(
        id=uuid4(),
        brief_id=brief_id,
        action_id=action.id,
        experiment_id=action.experiment_id,
        outreach_target_id=target_id,
        recipient_email=RECIPIENT,
        sender_email=SENDER,
        sender_name="Partizan Growth",
        reply_to=REPLY_TO,
        message_fingerprint=fingerprint,
        status=OutreachSendAuthorizationStatus.AUTHORIZED,
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
    )
    service._persist_authorization(authorization)

    with pytest.raises(ValueError, match="dedicated customer execution request flow"):
        await service.send(authorization.id)

    persisted = service.get_authorization(authorization.id)
    assert persisted.status == OutreachSendAuthorizationStatus.AUTHORIZED
    assert provider.calls == []
    assert service.get_attempt(brief_id) is None
    assert store.list_namespace(OUTREACH_SEND_ATTEMPT_NAMESPACE) == []
    assert execution.approve_calls == 0
    assert execution.mark_calls == 0
