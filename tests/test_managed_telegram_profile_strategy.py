import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.distribution_types import DistributionPlatform
from app.managed_distribution_schemas import (
    ManagedAssignmentStatus,
    ManagedPublisherOwnership,
)
from app.managed_telegram_execution import (
    MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
    ManagedTelegramConnectionStatus,
)
from app.managed_telegram_profile_strategy import (
    ManagedTelegramProfileAvatarInput,
    ManagedTelegramProfileStrategyApplyRequest,
    ManagedTelegramProfileStrategyApprovalRequest,
    ManagedTelegramProfileStrategyCreateRequest,
    ManagedTelegramProfileStrategyError,
    ManagedTelegramProfileStrategyRollbackRequest,
    ManagedTelegramProfileStrategyService,
    ManagedTelegramProfileStrategyStatus,
)
from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import (
    TelegramProfilePhotoReference,
    TelegramProfileSnapshot,
)


class FakeSecretStore:
    def __init__(self) -> None:
        self.values = {"MANAGED_TELEGRAM_SESSION_TEST": "managed-session"}

    def get(self, reference):
        return self.values.get(reference)


class FakeManagedService:
    def __init__(self, assignment, publisher) -> None:
        self.assignment = assignment
        self.publisher = publisher

    def get_assignment(self, assignment_id):
        if assignment_id != self.assignment.id:
            raise KeyError(assignment_id)
        return self.assignment

    def get_publisher(self, publisher_id):
        if publisher_id != self.publisher.id:
            raise KeyError(publisher_id)
        return self.publisher


class FakeTransport:
    def __init__(self) -> None:
        self.photo_seq = 10
        self.profile_state = TelegramProfileSnapshot(
            about="Original bio",
            username="managed_operator",
            display_name="Managed Operator",
            first_name="Managed",
            last_name="Operator",
            avatar=TelegramProfilePhotoReference(
                photo_id=1,
                access_hash=2,
                file_reference_b64=base64.b64encode(b"original-ref").decode("ascii"),
            ),
        )
        self.update_calls = []
        self.photo_calls = []
        self.restore_calls = []

    async def profile(self, *, session):
        assert session == "managed-session"
        return self.profile_state.model_copy(deep=True)

    async def update_profile(
        self,
        *,
        session,
        about=None,
        first_name=None,
        last_name=None,
    ):
        assert session == "managed-session"
        self.update_calls.append((about, first_name, last_name))
        update = {}
        if about is not None:
            update["about"] = about
        if first_name is not None:
            update["first_name"] = first_name
        if last_name is not None:
            update["last_name"] = last_name
        if first_name is not None or last_name is not None:
            first = update.get("first_name", self.profile_state.first_name)
            last = update.get("last_name", self.profile_state.last_name)
            update["display_name"] = " ".join(x for x in (first, last) if x) or None
        self.profile_state = self.profile_state.model_copy(update=update)
        return self.profile_state.model_copy(deep=True)

    async def upload_profile_photo(self, *, session, content, filename):
        assert session == "managed-session"
        self.photo_calls.append((content, filename))
        self.photo_seq += 1
        self.profile_state = self.profile_state.model_copy(
            update={
                "avatar": TelegramProfilePhotoReference(
                    photo_id=self.photo_seq,
                    access_hash=99,
                    file_reference_b64=base64.b64encode(b"new-ref").decode("ascii"),
                )
            }
        )
        return self.profile_state.model_copy(deep=True)

    async def restore_profile_photo(self, *, session, avatar):
        assert session == "managed-session"
        self.restore_calls.append(avatar)
        self.profile_state = self.profile_state.model_copy(update={"avatar": avatar})
        return self.profile_state.model_copy(deep=True)


def _fixture():
    store = MemoryRuntimeStateStore()
    publisher_id = uuid4()
    identity_id = uuid4()
    assignment = SimpleNamespace(
        id=uuid4(),
        product_id=uuid4(),
        managed_publisher_id=publisher_id,
        distribution_identity_id=identity_id,
        platform=DistributionPlatform.TELEGRAM,
        status=ManagedAssignmentStatus.RESERVED,
        profile_strategy_key="expert-native-cta-v1",
    )
    publisher = SimpleNamespace(
        id=publisher_id,
        distribution_identity_id=identity_id,
        ownership=ManagedPublisherOwnership.PARTIZAN_MANAGED,
    )
    store.put(
        MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
        str(publisher_id),
        {
            "managed_publisher_id": str(publisher_id),
            "distribution_identity_id": str(identity_id),
            "status": ManagedTelegramConnectionStatus.ACTIVE.value,
            "secret_reference": "MANAGED_TELEGRAM_SESSION_TEST",
            "username": "managed_operator",
            "display_name": "Managed Operator",
            "connected_at": "2026-10-07T12:00:00+00:00",
            "last_verified_at": "2026-10-07T12:00:00+00:00",
        },
    )
    transport = FakeTransport()
    service = ManagedTelegramProfileStrategyService(
        store=store,
        secret_store=FakeSecretStore(),
        transport=transport,
        managed_service=FakeManagedService(assignment, publisher),
    )
    return service, store, transport, assignment, publisher


def _request(*, with_avatar=True):
    avatar = None
    if with_avatar:
        png = b"\x89PNG\r\n\x1a\n" + b"managed-avatar"
        avatar = ManagedTelegramProfileAvatarInput(
            filename="profile.png",
            mime_type="image/png",
            content_base64=base64.b64encode(png).decode("ascii"),
        )
    return ManagedTelegramProfileStrategyCreateRequest(
        display_name="Nikolay | FemDom",
        bio="FemDom ↓ https://t.me/femdom",
        cta_value="https://t.me/femdom",
        avatar=avatar,
    )


def test_strategy_is_bound_to_assignment_key_and_keeps_avatar_private() -> None:
    service, _, _, assignment, _ = _fixture()

    created = service.create(assignment.id, _request())

    assert created.status == ManagedTelegramProfileStrategyStatus.DRAFT
    assert created.profile_strategy_key == "expert-native-cta-v1"
    assert created.avatar is not None
    assert created.avatar.preview_path.endswith(f"/{created.id}/avatar")
    assert len(created.fingerprint) == 64
    persisted = service._store.list_namespace("managed_telegram_profile_strategy")[0]
    assert persisted["avatar_content_base64"]
    assert "avatar_content_base64" not in created.model_dump()
    assert "before_profile" not in created.model_dump()


@pytest.mark.asyncio
async def test_preview_compares_live_profile_with_exact_proposed_treatment() -> None:
    service, _, _, assignment, _ = _fixture()
    created = service.create(assignment.id, _request())

    preview = await service.preview(created.id)

    assert preview.current.about == "Original bio"
    assert preview.current.display_name == "Managed Operator"
    assert preview.proposed_display_name == "Nikolay | FemDom"
    assert preview.proposed_bio == "FemDom ↓ https://t.me/femdom"
    assert preview.proposed_avatar_sha256 == created.avatar.sha256
    assert preview.changed_fields == ["display_name", "bio", "avatar"]


def test_approval_requires_exact_fingerprint() -> None:
    service, _, _, assignment, _ = _fixture()
    created = service.create(assignment.id, _request())

    with pytest.raises(ManagedTelegramProfileStrategyError, match="changed"):
        service.approve(
            created.id,
            ManagedTelegramProfileStrategyApprovalRequest(
                confirm=True,
                expected_fingerprint="0" * 64,
            ),
        )

    approved = service.approve(
        created.id,
        ManagedTelegramProfileStrategyApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )
    assert approved.status == ManagedTelegramProfileStrategyStatus.READY
    assert approved.approved_fingerprint == approved.fingerprint


@pytest.mark.asyncio
async def test_apply_mutates_profile_records_snapshot_and_marks_exact_connection_strategy() -> None:
    service, store, transport, assignment, publisher = _fixture()
    created = service.create(assignment.id, _request())
    approved = service.approve(
        created.id,
        ManagedTelegramProfileStrategyApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )

    applied = await service.apply(
        approved.id,
        ManagedTelegramProfileStrategyApplyRequest(
            confirm_apply=True,
            expected_fingerprint=approved.fingerprint,
        ),
    )

    assert applied.status == ManagedTelegramProfileStrategyStatus.APPLIED
    assert transport.profile_state.about == "FemDom ↓ https://t.me/femdom"
    assert transport.profile_state.first_name == "Nikolay"
    assert transport.profile_state.last_name == "| FemDom"
    assert transport.profile_state.avatar.photo_id != 1
    assert service.is_exact_strategy_applied(assignment.id) is True
    connection = store.get(MANAGED_TELEGRAM_CONNECTION_NAMESPACE, str(publisher.id))
    assert connection["applied_profile_assignment_id"] == str(assignment.id)
    assert connection["applied_profile_strategy_key"] == "expert-native-cta-v1"
    assert connection["applied_profile_strategy_fingerprint"] == applied.fingerprint


@pytest.mark.asyncio
async def test_rollback_restores_snapshot_and_clears_connection_strategy() -> None:
    service, store, transport, assignment, publisher = _fixture()
    created = service.create(assignment.id, _request())
    approved = service.approve(
        created.id,
        ManagedTelegramProfileStrategyApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )
    applied = await service.apply(
        approved.id,
        ManagedTelegramProfileStrategyApplyRequest(
            confirm_apply=True,
            expected_fingerprint=approved.fingerprint,
        ),
    )

    rolled_back = await service.rollback(
        applied.id,
        ManagedTelegramProfileStrategyRollbackRequest(confirm_rollback=True),
    )

    assert rolled_back.status == ManagedTelegramProfileStrategyStatus.ROLLED_BACK
    assert transport.profile_state.about == "Original bio"
    assert transport.profile_state.display_name == "Managed Operator"
    assert transport.profile_state.avatar.photo_id == 1
    connection = store.get(MANAGED_TELEGRAM_CONNECTION_NAMESPACE, str(publisher.id))
    assert connection["applied_profile_assignment_id"] is None
    assert connection["applied_profile_strategy_key"] is None
    assert service.is_exact_strategy_applied(assignment.id) is False


@pytest.mark.asyncio
async def test_rollback_refuses_to_overwrite_external_profile_edits() -> None:
    service, _, transport, assignment, _ = _fixture()
    created = service.create(assignment.id, _request(with_avatar=False))
    approved = service.approve(
        created.id,
        ManagedTelegramProfileStrategyApprovalRequest(
            confirm=True,
            expected_fingerprint=created.fingerprint,
        ),
    )
    applied = await service.apply(
        approved.id,
        ManagedTelegramProfileStrategyApplyRequest(
            confirm_apply=True,
            expected_fingerprint=approved.fingerprint,
        ),
    )
    transport.profile_state = transport.profile_state.model_copy(
        update={"about": "Human changed this after Partizan"}
    )

    with pytest.raises(ManagedTelegramProfileStrategyError, match="external edits"):
        await service.rollback(
            applied.id,
            ManagedTelegramProfileStrategyRollbackRequest(confirm_rollback=True),
        )

    assert transport.profile_state.about == "Human changed this after Partizan"


def test_strategy_requires_partizan_managed_inventory() -> None:
    service, _, _, assignment, publisher = _fixture()
    publisher.ownership = ManagedPublisherOwnership.PARTNER_MANAGED

    with pytest.raises(ManagedTelegramProfileStrategyError, match="Partizan-managed"):
        service.create(assignment.id, _request())
