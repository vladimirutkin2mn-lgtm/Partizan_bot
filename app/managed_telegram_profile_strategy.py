from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.distribution_types import DistributionPlatform
from app.managed_distribution import managed_distribution_service
from app.managed_distribution_schemas import (
    ManagedAssignmentStatus,
    ManagedPublisherOwnership,
)
from app.managed_telegram_execution import (
    MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
    ManagedTelegramConnectionStatus,
)
from app.provider_secret_store import ProviderSecretStore, provider_secret_store
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    TelegramClientPublishTransportError,
    TelegramProfileSnapshot,
    TelethonClientPublishTransport,
)

MANAGED_TELEGRAM_PROFILE_STRATEGY_NAMESPACE = "managed_telegram_profile_strategy"

_MAX_BIO_LENGTH = 70
_MAX_AVATAR_BYTES = 5 * 1024 * 1024
_ALLOWED_AVATAR_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_READBACK_ATTEMPTS = 4
_READBACK_DELAY_SECONDS = 0.5


class ManagedTelegramProfileStrategyError(RuntimeError):
    pass


class ManagedTelegramProfileStrategyStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"


class ManagedTelegramProfileAvatarInput(BaseModel):
    filename: str = Field(default="avatar.jpg", min_length=1, max_length=120)
    mime_type: str = Field(min_length=1, max_length=80)
    content_base64: str = Field(min_length=4)

    @field_validator("mime_type")
    @classmethod
    def normalize_mime_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_AVATAR_MIME_TYPES:
            raise ValueError("Avatar must be JPEG, PNG or WebP")
        return normalized


class ManagedTelegramProfileAvatarView(BaseModel):
    filename: str
    mime_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview_path: str


class ManagedTelegramProfileStrategyCreateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=129)
    bio: str = Field(min_length=1, max_length=_MAX_BIO_LENGTH)
    cta_value: str = Field(min_length=5, max_length=500)
    avatar: ManagedTelegramProfileAvatarInput | None = None

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Telegram display name is required")
        return normalized

    @field_validator("bio")
    @classmethod
    def normalize_bio(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Telegram bio is required")
        return normalized

    @field_validator("cta_value")
    @classmethod
    def normalize_cta_value(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_visible_cta(self):
        if self.cta_value not in self.bio:
            raise ValueError("Telegram bio must contain the exact CTA value")
        return self


class ManagedTelegramProfileStrategyApprovalRequest(BaseModel):
    confirm: bool = False
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ManagedTelegramProfileStrategyApplyRequest(BaseModel):
    confirm_apply: bool = False
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class ManagedTelegramProfileStrategyRollbackRequest(BaseModel):
    confirm_rollback: bool = False


class ManagedTelegramProfileStrategyView(BaseModel):
    id: UUID
    assignment_id: UUID
    product_id: UUID
    managed_publisher_id: UUID
    distribution_identity_id: UUID
    profile_strategy_key: str
    display_name: str
    bio: str
    cta_value: str
    avatar: ManagedTelegramProfileAvatarView | None = None
    status: ManagedTelegramProfileStrategyStatus
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_fingerprint: str | None = None
    approved_at: datetime | None = None
    applied_at: datetime | None = None
    rolled_back_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class ManagedTelegramProfileStrategyPreview(BaseModel):
    strategy: ManagedTelegramProfileStrategyView
    current: TelegramProfileSnapshot
    proposed_display_name: str
    proposed_bio: str
    proposed_avatar_sha256: str | None = None
    changed_fields: list[str] = Field(default_factory=list)
    requires_confirmation: bool = True


class ManagedTelegramProfileStrategyService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        secret_store: ProviderSecretStore | None = None,
        transport=None,
        managed_service=None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._secret_store = secret_store or provider_secret_store
        self._transport = transport or TelethonClientPublishTransport()
        self._managed = managed_service or managed_distribution_service
        self._mutation_lock = asyncio.Lock()

    def create(
        self,
        assignment_id: UUID,
        payload: ManagedTelegramProfileStrategyCreateRequest,
    ) -> ManagedTelegramProfileStrategyView:
        assignment, publisher = self._validated_assignment(assignment_id)
        if not assignment.profile_strategy_key:
            raise ManagedTelegramProfileStrategyError(
                "Managed assignment has no profile_strategy_key"
            )
        existing = self.for_assignment(assignment.id)
        if existing is not None:
            return existing

        strategy_id = uuid4()
        avatar_record = (
            self._avatar_record(strategy_id, payload.avatar)
            if payload.avatar is not None
            else None
        )
        now = datetime.now(UTC)
        record = {
            "id": str(strategy_id),
            "assignment_id": str(assignment.id),
            "product_id": str(assignment.product_id),
            "managed_publisher_id": str(publisher.id),
            "distribution_identity_id": str(publisher.distribution_identity_id),
            "profile_strategy_key": str(assignment.profile_strategy_key),
            "display_name": payload.display_name,
            "bio": payload.bio,
            "cta_value": payload.cta_value,
            "avatar": avatar_record["view"] if avatar_record is not None else None,
            "avatar_content_base64": (
                avatar_record["content_base64"] if avatar_record is not None else None
            ),
            "status": ManagedTelegramProfileStrategyStatus.DRAFT.value,
            "approved_fingerprint": None,
            "approved_at": None,
            "applied_at": None,
            "rolled_back_at": None,
            "before_profile": None,
            "after_profile": None,
            "last_error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        record["fingerprint"] = self._fingerprint(record)
        self._persist(record)
        return self._view(record)

    def get(self, strategy_id: UUID) -> ManagedTelegramProfileStrategyView:
        return self._view(self._payload(strategy_id))

    def for_assignment(
        self,
        assignment_id: UUID,
    ) -> ManagedTelegramProfileStrategyView | None:
        matches: list[ManagedTelegramProfileStrategyView] = []
        for payload in self._store.list_namespace(
            MANAGED_TELEGRAM_PROFILE_STRATEGY_NAMESPACE
        ):
            if str(payload.get("assignment_id") or "") != str(assignment_id):
                continue
            try:
                matches.append(self._view(payload))
            except ValueError:
                continue
        matches.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return matches[0] if matches else None

    async def preview(
        self,
        strategy_id: UUID,
    ) -> ManagedTelegramProfileStrategyPreview:
        record = self._payload(strategy_id)
        self._assert_strategy_scope(record)
        _, session = self._active_session(UUID(str(record["managed_publisher_id"])))
        try:
            current = await self._transport.profile(session=session)
        except TelegramClientPublishTransportError as exc:
            raise ManagedTelegramProfileStrategyError(exc.code) from exc
        changed_fields = ["display_name", "bio"]
        if record.get("avatar") is not None:
            changed_fields.append("avatar")
        avatar = record.get("avatar")
        return ManagedTelegramProfileStrategyPreview(
            strategy=self._view(record),
            current=current,
            proposed_display_name=str(record["display_name"]),
            proposed_bio=str(record["bio"]),
            proposed_avatar_sha256=(
                str(avatar["sha256"]) if isinstance(avatar, dict) else None
            ),
            changed_fields=changed_fields,
        )

    def approve(
        self,
        strategy_id: UUID,
        payload: ManagedTelegramProfileStrategyApprovalRequest,
    ) -> ManagedTelegramProfileStrategyView:
        if not payload.confirm:
            raise ManagedTelegramProfileStrategyError(
                "Explicit managed Telegram profile approval is required"
            )
        current = self._payload(strategy_id)
        self._assert_strategy_scope(current)
        if current["status"] not in {
            ManagedTelegramProfileStrategyStatus.DRAFT.value,
            ManagedTelegramProfileStrategyStatus.READY.value,
        }:
            raise ManagedTelegramProfileStrategyError(
                "Only a managed Telegram profile draft can be approved"
            )
        if payload.expected_fingerprint != current["fingerprint"]:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram profile strategy changed; review it again"
            )
        now = datetime.now(UTC)
        updated = {
            **current,
            "status": ManagedTelegramProfileStrategyStatus.READY.value,
            "approved_fingerprint": current["fingerprint"],
            "approved_at": now.isoformat(),
            "last_error": None,
            "updated_at": now.isoformat(),
        }
        self._persist(updated)
        return self._view(updated)

    async def apply(
        self,
        strategy_id: UUID,
        payload: ManagedTelegramProfileStrategyApplyRequest,
    ) -> ManagedTelegramProfileStrategyView:
        if not payload.confirm_apply:
            raise ManagedTelegramProfileStrategyError(
                "Explicit managed Telegram profile apply confirmation is required"
            )
        async with self._mutation_lock:
            current = self._payload(strategy_id)
            if current["status"] == ManagedTelegramProfileStrategyStatus.APPLIED.value:
                return self._view(current)
            if current["status"] != ManagedTelegramProfileStrategyStatus.READY.value:
                raise ManagedTelegramProfileStrategyError(
                    "Managed Telegram profile strategy must be approved before apply"
                )
            if (
                payload.expected_fingerprint != current["fingerprint"]
                or current.get("approved_fingerprint") != current["fingerprint"]
            ):
                raise ManagedTelegramProfileStrategyError(
                    "Approved managed Telegram profile strategy changed; review it again"
                )
            self._assert_strategy_scope(current)
            publisher_id = UUID(str(current["managed_publisher_id"]))
            connection, session = self._active_session(publisher_id)
            before = await self._safe_profile(session)
            first_name, last_name = self._split_display_name(str(current["display_name"]))
            avatar_content = self._decode_avatar(current)
            avatar = current.get("avatar")

            try:
                await self._transport.update_profile(
                    session=session,
                    about=str(current["bio"]),
                    first_name=first_name,
                    last_name=last_name,
                )
                if avatar_content is not None:
                    await self._transport.upload_profile_photo(
                        session=session,
                        content=avatar_content,
                        filename=str(avatar["filename"]),
                    )
                after = await self._await_profile(
                    session,
                    lambda profile: self._matches_applied(
                        profile,
                        before=before,
                        expected_bio=str(current["bio"]),
                        expected_first=first_name,
                        expected_last=last_name,
                        avatar_changed=avatar_content is not None,
                    ),
                )
            except Exception as exc:
                rollback_error = None
                try:
                    await self._restore_profile(
                        session,
                        before,
                        restore_avatar=avatar_content is not None,
                    )
                except Exception as rollback_exc:
                    rollback_error = (
                        f"; rollback failed: {type(rollback_exc).__name__}: {rollback_exc}"
                    )[:800]
                failed = {
                    **current,
                    "last_error": (
                        f"{type(exc).__name__}: {exc}{rollback_error or ''}"
                    )[:1000],
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                self._persist(failed)
                raise ManagedTelegramProfileStrategyError(
                    "Managed Telegram profile apply failed; rollback was attempted"
                ) from exc

            now = datetime.now(UTC)
            applied = {
                **current,
                "status": ManagedTelegramProfileStrategyStatus.APPLIED.value,
                "before_profile": before.model_dump(mode="json"),
                "after_profile": after.model_dump(mode="json"),
                "applied_at": now.isoformat(),
                "last_error": None,
                "updated_at": now.isoformat(),
            }
            self._persist(applied)
            self._mark_connection_strategy(
                connection,
                assignment_id=UUID(str(current["assignment_id"])),
                strategy_key=str(current["profile_strategy_key"]),
                fingerprint=str(current["fingerprint"]),
            )
            return self._view(applied)

    async def rollback(
        self,
        strategy_id: UUID,
        payload: ManagedTelegramProfileStrategyRollbackRequest,
    ) -> ManagedTelegramProfileStrategyView:
        if not payload.confirm_rollback:
            raise ManagedTelegramProfileStrategyError(
                "Explicit managed Telegram profile rollback confirmation is required"
            )
        async with self._mutation_lock:
            current = self._payload(strategy_id)
            if current["status"] == ManagedTelegramProfileStrategyStatus.ROLLED_BACK.value:
                return self._view(current)
            if current["status"] != ManagedTelegramProfileStrategyStatus.APPLIED.value:
                raise ManagedTelegramProfileStrategyError(
                    "Only an applied managed Telegram profile strategy can be rolled back"
                )
            before_raw = current.get("before_profile")
            after_raw = current.get("after_profile")
            if not isinstance(before_raw, dict) or not isinstance(after_raw, dict):
                raise ManagedTelegramProfileStrategyError(
                    "Managed Telegram profile strategy has no rollback snapshots"
                )
            before = TelegramProfileSnapshot.model_validate(before_raw)
            after = TelegramProfileSnapshot.model_validate(after_raw)
            publisher_id = UUID(str(current["managed_publisher_id"]))
            connection, session = self._active_session(publisher_id)
            live = await self._safe_profile(session)
            if not self._controlled_match(
                live,
                after,
                compare_avatar=current.get("avatar") is not None,
            ):
                raise ManagedTelegramProfileStrategyError(
                    "Telegram profile changed after Partizan apply; refusing to overwrite external edits"
                )
            try:
                restored = await self._restore_profile(
                    session,
                    before,
                    restore_avatar=current.get("avatar") is not None,
                )
            except Exception as exc:
                failed = {
                    **current,
                    "last_error": f"{type(exc).__name__}: {exc}"[:1000],
                    "updated_at": datetime.now(UTC).isoformat(),
                }
                self._persist(failed)
                raise ManagedTelegramProfileStrategyError(
                    "Managed Telegram profile rollback failed"
                ) from exc
            if not self._controlled_match(
                restored,
                before,
                compare_avatar=current.get("avatar") is not None,
            ):
                raise ManagedTelegramProfileStrategyError(
                    "Telegram did not confirm the expected rollback state"
                )
            now = datetime.now(UTC)
            rolled_back = {
                **current,
                "status": ManagedTelegramProfileStrategyStatus.ROLLED_BACK.value,
                "rolled_back_at": now.isoformat(),
                "last_error": None,
                "updated_at": now.isoformat(),
            }
            self._persist(rolled_back)
            self._clear_connection_strategy(
                connection,
                assignment_id=UUID(str(current["assignment_id"])),
                fingerprint=str(current["fingerprint"]),
            )
            return self._view(rolled_back)

    def avatar_bytes(
        self,
        strategy_id: UUID,
    ) -> tuple[bytes, str, str]:
        current = self._payload(strategy_id)
        avatar = current.get("avatar")
        if not isinstance(avatar, dict):
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram profile strategy has no avatar"
            )
        content = self._decode_avatar(current)
        if content is None:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram profile avatar is unavailable"
            )
        return content, str(avatar["mime_type"]), str(avatar["filename"])

    def is_exact_strategy_applied(
        self,
        assignment_id: UUID,
    ) -> bool:
        assignment, _ = self._validated_assignment(assignment_id)
        if not assignment.profile_strategy_key:
            return True
        connection = self._store.get(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(assignment.managed_publisher_id),
        )
        if connection is None:
            return False
        strategy = self.for_assignment(assignment_id)
        if strategy is None or strategy.status != ManagedTelegramProfileStrategyStatus.APPLIED:
            return False
        return (
            str(connection.get("applied_profile_assignment_id") or "")
            == str(assignment.id)
            and connection.get("applied_profile_strategy_key")
            == assignment.profile_strategy_key
            and connection.get("applied_profile_strategy_fingerprint")
            == strategy.fingerprint
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(MANAGED_TELEGRAM_PROFILE_STRATEGY_NAMESPACE)

    def _validated_assignment(self, assignment_id: UUID):
        try:
            assignment = self._managed.get_assignment(assignment_id)
        except KeyError as exc:
            raise ManagedTelegramProfileStrategyError(
                "Managed assignment not found"
            ) from exc
        if assignment.status != ManagedAssignmentStatus.RESERVED:
            raise ManagedTelegramProfileStrategyError(
                "Managed profile strategy requires a RESERVED assignment"
            )
        if assignment.platform != DistributionPlatform.TELEGRAM:
            raise ManagedTelegramProfileStrategyError(
                "Managed assignment is not Telegram"
            )
        try:
            publisher = self._managed.get_publisher(assignment.managed_publisher_id)
        except KeyError as exc:
            raise ManagedTelegramProfileStrategyError(
                "Managed publisher not found"
            ) from exc
        if publisher.ownership != ManagedPublisherOwnership.PARTIZAN_MANAGED:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram profile strategies require Partizan-managed inventory"
            )
        if publisher.distribution_identity_id != assignment.distribution_identity_id:
            raise ManagedTelegramProfileStrategyError(
                "Managed assignment identity does not match its publisher"
            )
        return assignment, publisher

    def _assert_strategy_scope(self, record: dict) -> None:
        assignment, publisher = self._validated_assignment(
            UUID(str(record["assignment_id"]))
        )
        if (
            str(assignment.product_id) != str(record["product_id"])
            or str(publisher.id) != str(record["managed_publisher_id"])
            or str(publisher.distribution_identity_id)
            != str(record["distribution_identity_id"])
            or assignment.profile_strategy_key != record["profile_strategy_key"]
        ):
            raise ManagedTelegramProfileStrategyError(
                "Managed profile strategy no longer matches its assignment"
            )

    def _active_session(self, publisher_id: UUID) -> tuple[dict, str]:
        connection = self._store.get(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(publisher_id),
        )
        if (
            connection is None
            or connection.get("status") != ManagedTelegramConnectionStatus.ACTIVE.value
        ):
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram publisher has no active session"
            )
        reference = str(connection.get("secret_reference") or "")
        session = self._secret_store.get(reference) if reference else None
        if not session:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram session is no longer available"
            )
        return connection, session

    async def _safe_profile(self, session: str) -> TelegramProfileSnapshot:
        try:
            return await self._transport.profile(session=session)
        except TelegramClientPublishTransportError as exc:
            raise ManagedTelegramProfileStrategyError(exc.code) from exc

    async def _await_profile(self, session: str, predicate) -> TelegramProfileSnapshot:
        last = None
        for attempt in range(_READBACK_ATTEMPTS):
            last = await self._safe_profile(session)
            if predicate(last):
                return last
            if attempt + 1 < _READBACK_ATTEMPTS:
                await asyncio.sleep(_READBACK_DELAY_SECONDS)
        raise ManagedTelegramProfileStrategyError(
            f"Telegram did not confirm the expected managed profile after {_READBACK_ATTEMPTS} reads"
        )

    async def _restore_profile(
        self,
        session: str,
        target: TelegramProfileSnapshot,
        *,
        restore_avatar: bool,
    ) -> TelegramProfileSnapshot:
        try:
            await self._transport.update_profile(
                session=session,
                about=target.about,
                first_name=target.first_name,
                last_name=target.last_name,
            )
            if restore_avatar:
                await self._transport.restore_profile_photo(
                    session=session,
                    avatar=target.avatar,
                )
        except TelegramClientPublishTransportError as exc:
            raise ManagedTelegramProfileStrategyError(exc.code) from exc
        return await self._await_profile(
            session,
            lambda profile: self._controlled_match(
                profile,
                target,
                compare_avatar=restore_avatar,
            ),
        )

    def _mark_connection_strategy(
        self,
        connection: dict,
        *,
        assignment_id: UUID,
        strategy_key: str,
        fingerprint: str,
    ) -> None:
        updated = {
            **connection,
            "applied_profile_assignment_id": str(assignment_id),
            "applied_profile_strategy_key": strategy_key,
            "applied_profile_strategy_fingerprint": fingerprint,
            "profile_strategy_applied_at": datetime.now(UTC).isoformat(),
        }
        self._store.put(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(connection["managed_publisher_id"]),
            updated,
        )

    def _clear_connection_strategy(
        self,
        connection: dict,
        *,
        assignment_id: UUID,
        fingerprint: str,
    ) -> None:
        if (
            str(connection.get("applied_profile_assignment_id") or "")
            != str(assignment_id)
            or connection.get("applied_profile_strategy_fingerprint") != fingerprint
        ):
            return
        updated = {
            **connection,
            "applied_profile_assignment_id": None,
            "applied_profile_strategy_key": None,
            "applied_profile_strategy_fingerprint": None,
            "profile_strategy_applied_at": None,
        }
        self._store.put(
            MANAGED_TELEGRAM_CONNECTION_NAMESPACE,
            str(connection["managed_publisher_id"]),
            updated,
        )

    def _avatar_record(
        self,
        strategy_id: UUID,
        avatar: ManagedTelegramProfileAvatarInput,
    ) -> dict:
        try:
            content = base64.b64decode(avatar.content_base64, validate=True)
        except (TypeError, ValueError) as exc:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram avatar is not valid base64"
            ) from exc
        if not content:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram avatar is empty"
            )
        if len(content) > _MAX_AVATAR_BYTES:
            raise ManagedTelegramProfileStrategyError(
                f"Managed Telegram avatar exceeds {_MAX_AVATAR_BYTES} bytes"
            )
        self._validate_image_signature(content, avatar.mime_type)
        sha256 = hashlib.sha256(content).hexdigest()
        return {
            "view": {
                "filename": avatar.filename.strip(),
                "mime_type": avatar.mime_type,
                "size_bytes": len(content),
                "sha256": sha256,
                "preview_path": (
                    f"/managed-distribution/telegram/profile-strategies/"
                    f"{strategy_id}/avatar"
                ),
            },
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    def _decode_avatar(self, record: dict) -> bytes | None:
        raw = record.get("avatar_content_base64")
        if not raw:
            return None
        try:
            return base64.b64decode(str(raw), validate=True)
        except (TypeError, ValueError) as exc:
            raise ManagedTelegramProfileStrategyError(
                "Stored managed Telegram avatar is invalid"
            ) from exc

    @staticmethod
    def _validate_image_signature(content: bytes, mime_type: str) -> None:
        valid = False
        if mime_type == "image/jpeg":
            valid = content.startswith(b"\xff\xd8\xff")
        elif mime_type == "image/png":
            valid = content.startswith(b"\x89PNG\r\n\x1a\n")
        elif mime_type == "image/webp":
            valid = (
                len(content) >= 12
                and content[:4] == b"RIFF"
                and content[8:12] == b"WEBP"
            )
        if not valid:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram avatar bytes do not match the declared image type"
            )

    @staticmethod
    def _split_display_name(display_name: str) -> tuple[str, str]:
        normalized = " ".join(display_name.split())
        first_name, separator, last_name = normalized.partition(" ")
        return first_name, (last_name if separator else "")

    def _matches_applied(
        self,
        profile: TelegramProfileSnapshot,
        *,
        before: TelegramProfileSnapshot,
        expected_bio: str,
        expected_first: str,
        expected_last: str,
        avatar_changed: bool,
    ) -> bool:
        if self._normal_text(profile.about) != self._normal_text(expected_bio):
            return False
        if profile.first_name != expected_first or profile.last_name != expected_last:
            return False
        if avatar_changed:
            if profile.avatar is None:
                return False
            if before.avatar is not None and profile.avatar.photo_id == before.avatar.photo_id:
                return False
        return True

    def _controlled_match(
        self,
        actual: TelegramProfileSnapshot,
        expected: TelegramProfileSnapshot,
        *,
        compare_avatar: bool,
    ) -> bool:
        if self._normal_text(actual.about) != self._normal_text(expected.about):
            return False
        if (
            actual.first_name != expected.first_name
            or actual.last_name != expected.last_name
        ):
            return False
        if compare_avatar:
            actual_photo_id = actual.avatar.photo_id if actual.avatar is not None else None
            expected_photo_id = expected.avatar.photo_id if expected.avatar is not None else None
            if actual_photo_id != expected_photo_id:
                return False
        return True

    @staticmethod
    def _normal_text(value: str) -> str:
        return " ".join(value.split())

    def _fingerprint(self, record: dict) -> str:
        avatar = record.get("avatar")
        material = {
            "assignment_id": str(record["assignment_id"]),
            "product_id": str(record["product_id"]),
            "managed_publisher_id": str(record["managed_publisher_id"]),
            "distribution_identity_id": str(record["distribution_identity_id"]),
            "profile_strategy_key": str(record["profile_strategy_key"]),
            "display_name": str(record["display_name"]),
            "bio": str(record["bio"]),
            "cta_value": str(record["cta_value"]),
            "avatar_sha256": (
                str(avatar["sha256"]) if isinstance(avatar, dict) else None
            ),
        }
        return hashlib.sha256(
            json.dumps(
                material,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def _payload(self, strategy_id: UUID) -> dict:
        payload = self._store.get(
            MANAGED_TELEGRAM_PROFILE_STRATEGY_NAMESPACE,
            str(strategy_id),
        )
        if payload is None:
            raise ManagedTelegramProfileStrategyError(
                "Managed Telegram profile strategy not found"
            )
        return payload

    def _persist(self, record: dict) -> None:
        self._store.put(
            MANAGED_TELEGRAM_PROFILE_STRATEGY_NAMESPACE,
            str(record["id"]),
            record,
        )

    @staticmethod
    def _view(record: dict) -> ManagedTelegramProfileStrategyView:
        safe = {
            key: value
            for key, value in record.items()
            if key not in {"avatar_content_base64", "before_profile", "after_profile"}
        }
        return ManagedTelegramProfileStrategyView.model_validate(safe)


managed_telegram_profile_strategy_service = ManagedTelegramProfileStrategyService()
