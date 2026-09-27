from uuid import uuid4

import pytest

from app.runtime_store import MemoryRuntimeStateStore
from app.telegram_client_publishing import (
    TelegramProfilePhotoReference,
    TelegramProfileSnapshot,
)
from app.telegram_profile_conversion import (
    CUSTOMER_TELEGRAM_PROFILE_MUTATION_NAMESPACE,
    CUSTOMER_TELEGRAM_PROFILE_SNAPSHOT_NAMESPACE,
    TelegramProfileConversionService,
    TelegramProfileMutationError,
    TelegramProfileMutationStatus,
    TelegramProfileSnapshotType,
)


def _avatar(photo_id: int) -> TelegramProfilePhotoReference:
    return TelegramProfilePhotoReference(
        photo_id=photo_id,
        access_hash=photo_id * 10,
        file_reference_b64="YWJj",
    )


def _profile(
    *,
    about: str = "Founder",
    first_name: str = "Nikolay",
    last_name: str = "",
    avatar_id: int | None = 1,
) -> TelegramProfileSnapshot:
    display_name = " ".join(part for part in (first_name, last_name) if part) or None
    return TelegramProfileSnapshot(
        about=about,
        username="nikollaus",
        display_name=display_name,
        first_name=first_name,
        last_name=last_name,
        avatar=(_avatar(avatar_id) if avatar_id is not None else None),
    )


class FakeProfilePublishService:
    def __init__(self, profile: TelegramProfileSnapshot) -> None:
        self.current = profile
        self.next_avatar_id = 100
        self.fail_avatar_update = False

    async def profile_internal(self, project_id):
        return self.current.model_copy(deep=True)

    async def update_profile_fields_internal(
        self,
        project_id,
        *,
        about=None,
        first_name=None,
        last_name=None,
    ):
        self.current = self.current.model_copy(
            update={
                "about": self.current.about if about is None else about.strip(),
                "first_name": self.current.first_name if first_name is None else first_name,
                "last_name": self.current.last_name if last_name is None else last_name,
            }
        )
        self.current = self.current.model_copy(
            update={
                "display_name": " ".join(
                    part for part in (self.current.first_name, self.current.last_name) if part
                )
                or None
            }
        )
        return self.current.model_copy(deep=True)

    async def update_profile_avatar_internal(
        self,
        project_id,
        content: bytes,
        *,
        filename: str = "avatar.jpg",
    ):
        if self.fail_avatar_update:
            raise RuntimeError("avatar upload failed")
        self.current = self.current.model_copy(update={"avatar": _avatar(self.next_avatar_id)})
        self.next_avatar_id += 1
        return self.current.model_copy(deep=True)

    async def restore_profile_avatar_internal(self, project_id, avatar):
        self.current = self.current.model_copy(
            update={"avatar": avatar.model_copy(deep=True) if avatar is not None else None}
        )
        return self.current.model_copy(deep=True)


@pytest.mark.asyncio
async def test_apply_persists_before_and_after_snapshots_with_experiment_context() -> None:
    store = MemoryRuntimeStateStore()
    fake = FakeProfilePublishService(_profile())
    service = TelegramProfileConversionService(store=store, publish_service=fake)
    project_id = uuid4()
    campaign_id = uuid4()
    experiment_id = uuid4()

    mutation = await service.apply_internal(
        project_id,
        about="FemDom ↓\nhttps://t.me/femdom",
        display_name="Nikolay | FemDom",
        avatar_content=b"new-avatar",
        avatar_filename="femdom.jpg",
        campaign_id=campaign_id,
        experiment_id=experiment_id,
    )

    assert mutation.status == TelegramProfileMutationStatus.APPLIED
    assert mutation.project_id == project_id
    assert mutation.campaign_id == campaign_id
    assert mutation.experiment_id == experiment_id
    assert mutation.changed_fields == ["about", "display_name", "avatar"]
    assert fake.current.about == "FemDom ↓\nhttps://t.me/femdom"
    assert fake.current.first_name == "Nikolay"
    assert fake.current.last_name == "| FemDom"
    assert fake.current.avatar is not None
    assert fake.current.avatar.photo_id == 100

    snapshots = store.list_namespace(CUSTOMER_TELEGRAM_PROFILE_SNAPSHOT_NAMESPACE)
    assert {item["snapshot_type"] for item in snapshots} == {
        TelegramProfileSnapshotType.BEFORE_APPLY.value,
        TelegramProfileSnapshotType.AFTER_APPLY.value,
    }
    mutations = store.list_namespace(CUSTOMER_TELEGRAM_PROFILE_MUTATION_NAMESPACE)
    assert len(mutations) == 1
    assert mutations[0]["status"] == TelegramProfileMutationStatus.APPLIED.value


@pytest.mark.asyncio
async def test_rollback_restores_exact_controlled_profile_fields() -> None:
    store = MemoryRuntimeStateStore()
    original = _profile(
        about="Original bio",
        first_name="Nikolay",
        last_name="Founder",
        avatar_id=7,
    )
    fake = FakeProfilePublishService(original)
    service = TelegramProfileConversionService(store=store, publish_service=fake)
    project_id = uuid4()

    applied = await service.apply_internal(
        project_id,
        about="FemDom ↓ https://t.me/femdom",
        display_name="Nikolay | FemDom",
        avatar_content=b"new-avatar",
    )
    rolled_back = await service.rollback_internal(project_id, applied.mutation_id)

    assert rolled_back.status == TelegramProfileMutationStatus.ROLLED_BACK
    assert fake.current.about == original.about
    assert fake.current.first_name == original.first_name
    assert fake.current.last_name == original.last_name
    assert fake.current.avatar is not None
    assert fake.current.avatar.photo_id == 7

    rollback_snapshot = service.get_snapshot(rolled_back.rollback_snapshot_id)
    assert rollback_snapshot.snapshot_type == TelegramProfileSnapshotType.AFTER_ROLLBACK


@pytest.mark.asyncio
async def test_rollback_refuses_to_overwrite_out_of_band_customer_edit() -> None:
    store = MemoryRuntimeStateStore()
    fake = FakeProfilePublishService(_profile())
    service = TelegramProfileConversionService(store=store, publish_service=fake)
    project_id = uuid4()

    applied = await service.apply_internal(
        project_id,
        about="FemDom ↓ https://t.me/femdom",
    )
    fake.current = fake.current.model_copy(update={"about": "Customer changed this manually"})

    with pytest.raises(TelegramProfileMutationError, match="refusing to overwrite customer edits"):
        await service.rollback_internal(project_id, applied.mutation_id)

    stored = service.get_mutation(applied.mutation_id)
    assert stored.status == TelegramProfileMutationStatus.ROLLBACK_BLOCKED
    assert fake.current.about == "Customer changed this manually"


@pytest.mark.asyncio
async def test_partial_apply_failure_attempts_rollback() -> None:
    store = MemoryRuntimeStateStore()
    original = _profile(about="Original")
    fake = FakeProfilePublishService(original)
    fake.fail_avatar_update = True
    service = TelegramProfileConversionService(store=store, publish_service=fake)
    project_id = uuid4()

    with pytest.raises(TelegramProfileMutationError, match="rollback was attempted"):
        await service.apply_internal(
            project_id,
            about="Temporary campaign bio",
            avatar_content=b"broken-avatar",
        )

    assert fake.current.about == original.about
    assert fake.current.avatar is not None
    assert fake.current.avatar.photo_id == original.avatar.photo_id
    mutation_payload = store.list_namespace(CUSTOMER_TELEGRAM_PROFILE_MUTATION_NAMESPACE)[0]
    assert mutation_payload["status"] == TelegramProfileMutationStatus.FAILED.value
    assert mutation_payload["rollback_error"] is None


@pytest.mark.asyncio
async def test_bio_readback_allows_telegram_whitespace_normalization() -> None:
    class FlatteningBioService(FakeProfilePublishService):
        async def update_profile_fields_internal(self, project_id, **kwargs):
            profile = await super().update_profile_fields_internal(project_id, **kwargs)
            if kwargs.get("about") is not None:
                self.current = profile.model_copy(
                    update={"about": " ".join(profile.about.split())}
                )
            return self.current.model_copy(deep=True)

    store = MemoryRuntimeStateStore()
    fake = FlatteningBioService(_profile())
    service = TelegramProfileConversionService(store=store, publish_service=fake)

    mutation = await service.apply_internal(
        uuid4(),
        about="FemDom ↓\nhttps://t.me/femdom",
    )

    assert mutation.status == TelegramProfileMutationStatus.APPLIED
