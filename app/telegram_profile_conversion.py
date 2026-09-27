from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    TelegramProfileSnapshot,
    customer_telegram_client_publish_service,
)

CUSTOMER_TELEGRAM_PROFILE_SNAPSHOT_NAMESPACE = "customer_telegram_profile_snapshot"
CUSTOMER_TELEGRAM_PROFILE_MUTATION_NAMESPACE = "customer_telegram_profile_mutation"

_READBACK_ATTEMPTS = 4
_READBACK_DELAY_SECONDS = 0.5


class TelegramProfileMutationError(RuntimeError):
    pass


class TelegramProfileSnapshotType(StrEnum):
    BEFORE_APPLY = "BEFORE_APPLY"
    AFTER_APPLY = "AFTER_APPLY"
    AFTER_ROLLBACK = "AFTER_ROLLBACK"


class TelegramProfileMutationStatus(StrEnum):
    APPLYING = "APPLYING"
    APPLIED = "APPLIED"
    FAILED = "FAILED"
    ROLLING_BACK = "ROLLING_BACK"
    ROLLED_BACK = "ROLLED_BACK"
    ROLLBACK_BLOCKED = "ROLLBACK_BLOCKED"


class TelegramProfileSnapshotRecord(BaseModel):
    snapshot_id: UUID
    project_id: UUID
    campaign_id: UUID | None = None
    experiment_id: UUID | None = None
    mutation_id: UUID | None = None
    snapshot_type: TelegramProfileSnapshotType
    profile: TelegramProfileSnapshot
    created_at: datetime


class TelegramProfileMutationRecord(BaseModel):
    mutation_id: UUID
    project_id: UUID
    campaign_id: UUID | None = None
    experiment_id: UUID | None = None
    status: TelegramProfileMutationStatus
    changed_fields: list[str] = Field(default_factory=list)
    before_snapshot_id: UUID
    after_snapshot_id: UUID | None = None
    rollback_snapshot_id: UUID | None = None
    avatar_filename: str | None = None
    created_at: datetime
    updated_at: datetime
    error: str | None = None
    rollback_error: str | None = None


class TelegramProfileConversionService:
    """Safe, reversible Telegram profile mutations for customer-owned distribution."""

    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        publish_service=None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._publish_service = publish_service or customer_telegram_client_publish_service
        self._mutation_lock = asyncio.Lock()

    async def apply_internal(
        self,
        project_id: UUID,
        *,
        about: str | None = None,
        display_name: str | None = None,
        avatar_content: bytes | None = None,
        avatar_filename: str = "avatar.jpg",
        campaign_id: UUID | None = None,
        experiment_id: UUID | None = None,
    ) -> TelegramProfileMutationRecord:
        changed_fields = self._changed_fields(
            about=about,
            display_name=display_name,
            avatar_content=avatar_content,
        )
        if not changed_fields:
            raise TelegramProfileMutationError("At least one Telegram profile field must change")

        async with self._mutation_lock:
            mutation_id = uuid4()
            before_profile = await self._publish_service.profile_internal(project_id)
            before = self._persist_snapshot(
                project_id=project_id,
                campaign_id=campaign_id,
                experiment_id=experiment_id,
                mutation_id=mutation_id,
                snapshot_type=TelegramProfileSnapshotType.BEFORE_APPLY,
                profile=before_profile,
            )
            now = datetime.now(UTC)
            mutation = TelegramProfileMutationRecord(
                mutation_id=mutation_id,
                project_id=project_id,
                campaign_id=campaign_id,
                experiment_id=experiment_id,
                status=TelegramProfileMutationStatus.APPLYING,
                changed_fields=changed_fields,
                before_snapshot_id=before.snapshot_id,
                avatar_filename=(avatar_filename if avatar_content is not None else None),
                created_at=now,
                updated_at=now,
            )
            self._persist_mutation(mutation)

            try:
                expected_about = before_profile.about
                expected_first = before_profile.first_name
                expected_last = before_profile.last_name

                if "about" in changed_fields or "display_name" in changed_fields:
                    first_name = None
                    last_name = None
                    if display_name is not None:
                        first_name, last_name = self._split_display_name(display_name)
                        expected_first = first_name
                        expected_last = last_name
                    if about is not None:
                        expected_about = about.strip()
                    await self._publish_service.update_profile_fields_internal(
                        project_id,
                        about=(about if "about" in changed_fields else None),
                        first_name=(first_name if "display_name" in changed_fields else None),
                        last_name=(last_name if "display_name" in changed_fields else None),
                    )

                if avatar_content is not None:
                    await self._publish_service.update_profile_avatar_internal(
                        project_id,
                        avatar_content,
                        filename=avatar_filename,
                    )

                after_profile = await self._await_profile(
                    project_id,
                    lambda profile: self._matches_apply_expectation(
                        profile,
                        before=before_profile,
                        changed_fields=changed_fields,
                        expected_about=expected_about,
                        expected_first=expected_first,
                        expected_last=expected_last,
                    ),
                )
                after = self._persist_snapshot(
                    project_id=project_id,
                    campaign_id=campaign_id,
                    experiment_id=experiment_id,
                    mutation_id=mutation_id,
                    snapshot_type=TelegramProfileSnapshotType.AFTER_APPLY,
                    profile=after_profile,
                )
                mutation = mutation.model_copy(
                    update={
                        "status": TelegramProfileMutationStatus.APPLIED,
                        "after_snapshot_id": after.snapshot_id,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._persist_mutation(mutation)
                return mutation
            except Exception as exc:
                rollback_error = None
                try:
                    await self._restore_snapshot(
                        project_id,
                        before.profile,
                        changed_fields=changed_fields,
                    )
                except Exception as rollback_exc:  # fail closed and record both failures
                    rollback_error = f"{type(rollback_exc).__name__}: {rollback_exc}"[:1000]
                mutation = mutation.model_copy(
                    update={
                        "status": TelegramProfileMutationStatus.FAILED,
                        "error": f"{type(exc).__name__}: {exc}"[:1000],
                        "rollback_error": rollback_error,
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._persist_mutation(mutation)
                raise TelegramProfileMutationError(
                    "Telegram profile mutation failed; rollback was attempted"
                ) from exc

    async def rollback_internal(
        self,
        project_id: UUID,
        mutation_id: UUID,
    ) -> TelegramProfileMutationRecord:
        async with self._mutation_lock:
            mutation = self.get_mutation(mutation_id)
            if mutation.project_id != project_id:
                raise TelegramProfileMutationError("Telegram profile mutation belongs to another project")
            if mutation.status == TelegramProfileMutationStatus.ROLLED_BACK:
                return mutation
            if mutation.status != TelegramProfileMutationStatus.APPLIED:
                raise TelegramProfileMutationError(
                    f"Telegram profile mutation is not rollbackable from {mutation.status.value}"
                )
            if mutation.after_snapshot_id is None:
                raise TelegramProfileMutationError("Applied Telegram profile mutation has no AFTER snapshot")

            before = self.get_snapshot(mutation.before_snapshot_id)
            after = self.get_snapshot(mutation.after_snapshot_id)
            current = await self._publish_service.profile_internal(project_id)
            if not self._controlled_fields_match(
                current,
                after.profile,
                mutation.changed_fields,
            ):
                blocked = mutation.model_copy(
                    update={
                        "status": TelegramProfileMutationStatus.ROLLBACK_BLOCKED,
                        "error": (
                            "Telegram profile changed outside Partizan after apply; "
                            "refusing to overwrite customer edits"
                        ),
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._persist_mutation(blocked)
                raise TelegramProfileMutationError(blocked.error or "Rollback blocked")

            rolling_back = mutation.model_copy(
                update={
                    "status": TelegramProfileMutationStatus.ROLLING_BACK,
                    "updated_at": datetime.now(UTC),
                }
            )
            self._persist_mutation(rolling_back)

            try:
                restored = await self._restore_snapshot(
                    project_id,
                    before.profile,
                    changed_fields=mutation.changed_fields,
                )
                rollback_snapshot = self._persist_snapshot(
                    project_id=project_id,
                    campaign_id=mutation.campaign_id,
                    experiment_id=mutation.experiment_id,
                    mutation_id=mutation.mutation_id,
                    snapshot_type=TelegramProfileSnapshotType.AFTER_ROLLBACK,
                    profile=restored,
                )
                completed = rolling_back.model_copy(
                    update={
                        "status": TelegramProfileMutationStatus.ROLLED_BACK,
                        "rollback_snapshot_id": rollback_snapshot.snapshot_id,
                        "updated_at": datetime.now(UTC),
                        "error": None,
                    }
                )
                self._persist_mutation(completed)
                return completed
            except Exception as exc:
                failed = rolling_back.model_copy(
                    update={
                        "status": TelegramProfileMutationStatus.FAILED,
                        "rollback_error": f"{type(exc).__name__}: {exc}"[:1000],
                        "updated_at": datetime.now(UTC),
                    }
                )
                self._persist_mutation(failed)
                raise TelegramProfileMutationError("Telegram profile rollback failed") from exc

    def get_mutation(self, mutation_id: UUID) -> TelegramProfileMutationRecord:
        payload = self._store.get(
            CUSTOMER_TELEGRAM_PROFILE_MUTATION_NAMESPACE,
            str(mutation_id),
        )
        if payload is None:
            raise TelegramProfileMutationError("Telegram profile mutation not found")
        return TelegramProfileMutationRecord.model_validate(payload)

    def get_snapshot(self, snapshot_id: UUID) -> TelegramProfileSnapshotRecord:
        payload = self._store.get(
            CUSTOMER_TELEGRAM_PROFILE_SNAPSHOT_NAMESPACE,
            str(snapshot_id),
        )
        if payload is None:
            raise TelegramProfileMutationError("Telegram profile snapshot not found")
        return TelegramProfileSnapshotRecord.model_validate(payload)

    def reset(self) -> None:
        self._store.clear_namespace(CUSTOMER_TELEGRAM_PROFILE_SNAPSHOT_NAMESPACE)
        self._store.clear_namespace(CUSTOMER_TELEGRAM_PROFILE_MUTATION_NAMESPACE)

    async def _restore_snapshot(
        self,
        project_id: UUID,
        target: TelegramProfileSnapshot,
        *,
        changed_fields: list[str],
    ) -> TelegramProfileSnapshot:
        if "about" in changed_fields or "display_name" in changed_fields:
            await self._publish_service.update_profile_fields_internal(
                project_id,
                about=(target.about if "about" in changed_fields else None),
                first_name=(target.first_name if "display_name" in changed_fields else None),
                last_name=(target.last_name if "display_name" in changed_fields else None),
            )
        if "avatar" in changed_fields:
            await self._publish_service.restore_profile_avatar_internal(
                project_id,
                target.avatar,
            )
        return await self._await_profile(
            project_id,
            lambda profile: self._controlled_fields_match(
                profile,
                target,
                changed_fields,
            ),
        )

    async def _await_profile(self, project_id: UUID, predicate) -> TelegramProfileSnapshot:
        last = None
        for attempt in range(_READBACK_ATTEMPTS):
            last = await self._publish_service.profile_internal(project_id)
            if predicate(last):
                return last
            if attempt + 1 < _READBACK_ATTEMPTS:
                await asyncio.sleep(_READBACK_DELAY_SECONDS)
        raise TelegramProfileMutationError(
            f"Telegram did not confirm the expected profile state after {_READBACK_ATTEMPTS} reads"
        )

    def _persist_snapshot(
        self,
        *,
        project_id: UUID,
        campaign_id: UUID | None,
        experiment_id: UUID | None,
        mutation_id: UUID,
        snapshot_type: TelegramProfileSnapshotType,
        profile: TelegramProfileSnapshot,
    ) -> TelegramProfileSnapshotRecord:
        snapshot = TelegramProfileSnapshotRecord(
            snapshot_id=uuid4(),
            project_id=project_id,
            campaign_id=campaign_id,
            experiment_id=experiment_id,
            mutation_id=mutation_id,
            snapshot_type=snapshot_type,
            profile=profile,
            created_at=datetime.now(UTC),
        )
        self._store.put(
            CUSTOMER_TELEGRAM_PROFILE_SNAPSHOT_NAMESPACE,
            str(snapshot.snapshot_id),
            snapshot.model_dump(mode="json"),
        )
        return snapshot

    def _persist_mutation(self, mutation: TelegramProfileMutationRecord) -> None:
        self._store.put(
            CUSTOMER_TELEGRAM_PROFILE_MUTATION_NAMESPACE,
            str(mutation.mutation_id),
            mutation.model_dump(mode="json"),
        )

    def _changed_fields(
        self,
        *,
        about: str | None,
        display_name: str | None,
        avatar_content: bytes | None,
    ) -> list[str]:
        fields: list[str] = []
        if about is not None:
            fields.append("about")
        if display_name is not None:
            fields.append("display_name")
        if avatar_content is not None:
            fields.append("avatar")
        return fields

    def _split_display_name(self, display_name: str) -> tuple[str, str]:
        normalized = " ".join(display_name.split())
        if not normalized:
            raise TelegramProfileMutationError("Telegram display name is required")
        first_name, separator, last_name = normalized.partition(" ")
        return first_name, (last_name if separator else "")

    def _matches_apply_expectation(
        self,
        profile: TelegramProfileSnapshot,
        *,
        before: TelegramProfileSnapshot,
        changed_fields: list[str],
        expected_about: str,
        expected_first: str,
        expected_last: str,
    ) -> bool:
        if "about" in changed_fields and self._normal_text(profile.about) != self._normal_text(
            expected_about
        ):
            return False
        if "display_name" in changed_fields and (
            profile.first_name != expected_first or profile.last_name != expected_last
        ):
            return False
        if "avatar" in changed_fields:
            if profile.avatar is None:
                return False
            if before.avatar is not None and profile.avatar.photo_id == before.avatar.photo_id:
                return False
        return True

    def _controlled_fields_match(
        self,
        actual: TelegramProfileSnapshot,
        expected: TelegramProfileSnapshot,
        changed_fields: list[str],
    ) -> bool:
        if "about" in changed_fields and self._normal_text(actual.about) != self._normal_text(
            expected.about
        ):
            return False
        if "display_name" in changed_fields and (
            actual.first_name != expected.first_name or actual.last_name != expected.last_name
        ):
            return False
        if "avatar" in changed_fields:
            actual_photo_id = actual.avatar.photo_id if actual.avatar is not None else None
            expected_photo_id = expected.avatar.photo_id if expected.avatar is not None else None
            if actual_photo_id != expected_photo_id:
                return False
        return True

    def _normal_text(self, value: str) -> str:
        return " ".join(value.split())


telegram_profile_conversion_service = TelegramProfileConversionService()
