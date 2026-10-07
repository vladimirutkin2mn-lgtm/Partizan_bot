from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.distribution_types import (
    DistributionActionStatus,
    DistributionActionType,
    DistributionPlatform,
)
from app.managed_distribution import ManagedDistributionError
from app.managed_distribution_schemas import (
    ManagedAssignmentStatus,
    ManagedPublisherHealth,
    ManagedPublisherOwnership,
)
from app.managed_telegram_execution import (
    ManagedTelegramActionPreviewRequest,
    ManagedTelegramExecuteRequest,
    ManagedTelegramExecutionError,
    ManagedTelegramExecutionService,
    ManagedTelegramExecutionStatus,
    ManagedTelegramReconcileRequest,
    ManagedTelegramSessionInstallRequest,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import (
    TelegramClientPublishTransportError,
    TelegramProfileSnapshot,
    TelegramPublishResult,
)


class FakeSecretStore:
    def __init__(self) -> None:
        self.values = {}
        self.deleted = []
        self.counter = 0

    def create_reference(self, *, prefix):
        self.counter += 1
        return f"{prefix}TEST{self.counter}"

    def put(self, reference, plaintext):
        self.values[reference] = plaintext

    def get(self, reference):
        return self.values.get(reference)

    def delete(self, reference):
        self.deleted.append(reference)
        self.values.pop(reference, None)


class FakeTransport:
    def __init__(self) -> None:
        self.profile_snapshot = TelegramProfileSnapshot(
            about="Managed profile",
            username="managed_operator",
            display_name="Managed Operator",
            first_name="Managed",
            last_name="Operator",
            avatar=None,
        )
        self.publish_calls = []
        self.publish_error = None

    async def profile(self, *, session):
        assert session
        return self.profile_snapshot

    async def publish(self, *, session, target, action_type, text):
        self.publish_calls.append(
            {
                "session": session,
                "target": target,
                "action_type": action_type,
                "text": text,
            }
        )
        if self.publish_error is not None:
            raise self.publish_error
        return TelegramPublishResult(
            peer_id=123,
            message_id=456,
            published_at=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
            executed_url="https://t.me/a_sfera/2089?comment=456",
        )


class FakeManagedService:
    def __init__(self, publisher, assignment) -> None:
        self.publisher = publisher
        self.assignment = assignment
        self.fulfill_calls = []
        self.health_calls = []
        self.fail_fulfill = False

    def get_publisher(self, publisher_id):
        if publisher_id != self.publisher.id:
            raise KeyError(publisher_id)
        return self.publisher

    def get_assignment(self, assignment_id):
        if assignment_id != self.assignment.id:
            raise KeyError(assignment_id)
        return self.assignment

    def fulfill(self, assignment_id, payload):
        if self.fail_fulfill:
            raise ManagedDistributionError("database unavailable")
        self.fulfill_calls.append((assignment_id, payload))
        self.assignment.status = ManagedAssignmentStatus.FULFILLED
        return self.assignment

    def set_health(self, publisher_id, health, reason):
        self.health_calls.append((publisher_id, health, reason))
        self.publisher.health = health
        return self.publisher


class FakeExecutionService:
    def __init__(self, action, experiment) -> None:
        self.action = action
        self.experiment = experiment

    def get_action(self, action_id):
        if action_id != self.action.id:
            raise KeyError(action_id)
        return self.action

    def get_experiment(self, experiment_id):
        if experiment_id != self.experiment.id:
            raise KeyError(experiment_id)
        return self.experiment


def _fixture(*, ownership=ManagedPublisherOwnership.PARTIZAN_MANAGED):
    product_id = uuid4()
    publisher_id = uuid4()
    identity_id = uuid4()
    assignment_id = uuid4()
    opportunity_id = uuid4()
    action_id = uuid4()
    experiment_id = uuid4()
    publisher = SimpleNamespace(
        id=publisher_id,
        distribution_identity_id=identity_id,
        platform=DistributionPlatform.TELEGRAM,
        ownership=ownership,
        health=ManagedPublisherHealth.ELIGIBLE,
    )
    assignment = SimpleNamespace(
        id=assignment_id,
        product_id=product_id,
        managed_publisher_id=publisher_id,
        distribution_identity_id=identity_id,
        platform=DistributionPlatform.TELEGRAM,
        action_type=DistributionActionType.COMMENT,
        opportunity_id=opportunity_id,
        status=ManagedAssignmentStatus.RESERVED,
        persona=SimpleNamespace(value="EXPERT"),
        profile_strategy_key="expert-native-cta-v1",
        message_strategy="expertise_signal",
        experiment_arm="expert__native__expertise",
    )
    action = SimpleNamespace(
        id=action_id,
        platform=DistributionPlatform.TELEGRAM,
        action_type=DistributionActionType.COMMENT,
        distribution_identity_id=identity_id,
        opportunity_id=opportunity_id,
        experiment_id=experiment_id,
        status=DistributionActionStatus.APPROVED,
        target_url="https://t.me/a_sfera/2089",
        content_text="Exact reviewed comment",
    )
    experiment = SimpleNamespace(
        id=experiment_id,
        product_id=product_id,
    )
    managed = FakeManagedService(publisher, assignment)
    transport = FakeTransport()
    secrets = FakeSecretStore()
    service = ManagedTelegramExecutionService(
        store=MemoryRuntimeStateStore(),
        secret_store=secrets,
        transport=transport,
        managed_service=managed,
        execution_service=FakeExecutionService(action, experiment),
    )
    return service, managed, transport, secrets, publisher, assignment, action


async def _connect(service, publisher):
    return await service.install_session(
        publisher.id,
        ManagedTelegramSessionInstallRequest(
            session=SecretStr("managed-session-secret"),
            expected_username="managed_operator",
            confirm_management_authorization=True,
        ),
    )


@pytest.mark.asyncio
async def test_managed_session_is_verified_and_kept_out_of_connection_view() -> None:
    service, _, _, secrets, publisher, _, _ = _fixture()

    connection = await _connect(service, publisher)

    assert connection.username == "managed_operator"
    assert connection.managed_publisher_id == publisher.id
    assert "session" not in connection.model_dump()
    assert list(secrets.values.values()) == ["managed-session-secret"]


@pytest.mark.asyncio
async def test_managed_session_requires_exact_expected_username() -> None:
    service, _, transport, secrets, publisher, _, _ = _fixture()
    transport.profile_snapshot = transport.profile_snapshot.model_copy(
        update={"username": "different_operator"}
    )

    with pytest.raises(ManagedTelegramExecutionError, match="does not match"):
        await service.install_session(
            publisher.id,
            ManagedTelegramSessionInstallRequest(
                session=SecretStr("managed-session-secret"),
                expected_username="managed_operator",
                confirm_management_authorization=True,
            ),
        )

    assert secrets.values == {}


@pytest.mark.asyncio
async def test_partner_managed_inventory_cannot_install_direct_partizan_session() -> None:
    service, _, _, _, publisher, _, _ = _fixture(
        ownership=ManagedPublisherOwnership.PARTNER_MANAGED
    )

    with pytest.raises(ManagedTelegramExecutionError, match="Partizan-managed"):
        await _connect(service, publisher)


@pytest.mark.asyncio
async def test_preview_exposes_exact_text_target_and_experiment_arm() -> None:
    service, _, _, _, publisher, assignment, action = _fixture()
    await _connect(service, publisher)

    preview = service.preview(
        assignment.id,
        ManagedTelegramActionPreviewRequest(action_id=action.id),
    )

    assert preview.content_text == "Exact reviewed comment"
    assert preview.target_url == "https://t.me/a_sfera/2089"
    assert preview.telegram_username == "managed_operator"
    assert preview.profile_strategy_key == "expert-native-cta-v1"
    assert preview.message_strategy == "expertise_signal"
    assert preview.experiment_arm == "expert__native__expertise"
    assert preview.requires_publish_confirmation is True
    assert len(preview.fingerprint) == 64


@pytest.mark.asyncio
async def test_execute_requires_explicit_confirmation_and_exact_fingerprint() -> None:
    service, _, transport, _, publisher, assignment, action = _fixture()
    await _connect(service, publisher)
    preview = service.preview(
        assignment.id,
        ManagedTelegramActionPreviewRequest(action_id=action.id),
    )

    with pytest.raises(ManagedTelegramExecutionError, match="confirmation"):
        await service.execute(
            assignment.id,
            ManagedTelegramExecuteRequest(
                action_id=action.id,
                confirm_execute=False,
                expected_fingerprint=preview.fingerprint,
            ),
        )
    assert transport.publish_calls == []

    action.content_text = "Changed after review"
    with pytest.raises(ManagedTelegramExecutionError, match="changed after review"):
        await service.execute(
            assignment.id,
            ManagedTelegramExecuteRequest(
                action_id=action.id,
                confirm_execute=True,
                expected_fingerprint=preview.fingerprint,
            ),
        )
    assert transport.publish_calls == []


@pytest.mark.asyncio
async def test_confirmed_execution_publishes_once_and_fulfills_assignment() -> None:
    service, managed, transport, _, publisher, assignment, action = _fixture()
    await _connect(service, publisher)
    preview = service.preview(
        assignment.id,
        ManagedTelegramActionPreviewRequest(action_id=action.id),
    )

    receipt = await service.execute(
        assignment.id,
        ManagedTelegramExecuteRequest(
            action_id=action.id,
            confirm_execute=True,
            expected_fingerprint=preview.fingerprint,
        ),
    )

    assert receipt.status == ManagedTelegramExecutionStatus.EXECUTED
    assert receipt.remote_message_id == 456
    assert receipt.executed_url == "https://t.me/a_sfera/2089?comment=456"
    assert len(transport.publish_calls) == 1
    assert transport.publish_calls[0]["text"] == "Exact reviewed comment"
    assert transport.publish_calls[0]["target"].username == "a_sfera"
    assert transport.publish_calls[0]["target"].reply_to_message_id == 2089
    assert len(managed.fulfill_calls) == 1
    _, fulfillment = managed.fulfill_calls[0]
    assert fulfillment.action_id == action.id
    assert fulfillment.external_reference == "telegram:123:456"


@pytest.mark.asyncio
async def test_remote_publish_is_never_repeated_when_local_fulfillment_needs_reconcile() -> None:
    service, managed, transport, _, publisher, assignment, action = _fixture()
    await _connect(service, publisher)
    preview = service.preview(
        assignment.id,
        ManagedTelegramActionPreviewRequest(action_id=action.id),
    )
    managed.fail_fulfill = True

    with pytest.raises(ManagedTelegramExecutionError, match="needs reconciliation"):
        await service.execute(
            assignment.id,
            ManagedTelegramExecuteRequest(
                action_id=action.id,
                confirm_execute=True,
                expected_fingerprint=preview.fingerprint,
            ),
        )

    assert len(transport.publish_calls) == 1
    pending = service.receipt(assignment.id)
    assert pending is not None
    assert pending.status == ManagedTelegramExecutionStatus.PUBLISHED_UNRECONCILED

    repeated = await service.execute(
        assignment.id,
        ManagedTelegramExecuteRequest(
            action_id=action.id,
            confirm_execute=True,
            expected_fingerprint=preview.fingerprint,
        ),
    )
    assert repeated.status == ManagedTelegramExecutionStatus.PUBLISHED_UNRECONCILED
    assert len(transport.publish_calls) == 1

    managed.fail_fulfill = False
    reconciled = service.reconcile(
        assignment.id,
        ManagedTelegramReconcileRequest(confirm_reconcile=True),
    )
    assert reconciled.status == ManagedTelegramExecutionStatus.EXECUTED
    assert len(transport.publish_calls) == 1
    assert len(managed.fulfill_calls) == 1


@pytest.mark.asyncio
async def test_provider_restriction_marks_managed_publisher_restricted() -> None:
    service, managed, transport, _, publisher, assignment, action = _fixture()
    await _connect(service, publisher)
    preview = service.preview(
        assignment.id,
        ManagedTelegramActionPreviewRequest(action_id=action.id),
    )
    transport.publish_error = TelegramClientPublishTransportError(
        "PEER_FLOOD",
        restriction_signal="telegram_peer_flood",
    )

    with pytest.raises(ManagedTelegramExecutionError, match="PEER_FLOOD"):
        await service.execute(
            assignment.id,
            ManagedTelegramExecuteRequest(
                action_id=action.id,
                confirm_execute=True,
                expected_fingerprint=preview.fingerprint,
            ),
        )

    assert managed.health_calls == [
        (
            publisher.id,
            ManagedPublisherHealth.RESTRICTED,
            "telegram_peer_flood",
        )
    ]


@pytest.mark.asyncio
async def test_private_invite_target_is_rejected_before_publish() -> None:
    service, _, transport, _, publisher, assignment, action = _fixture()
    await _connect(service, publisher)
    action.target_url = "https://t.me/+privateInvite"

    with pytest.raises(ManagedTelegramExecutionError, match="Private Telegram invite"):
        service.preview(
            assignment.id,
            ManagedTelegramActionPreviewRequest(action_id=action.id),
        )

    assert transport.publish_calls == []
