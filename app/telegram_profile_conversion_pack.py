from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from app.customer_funnel import customer_funnel_service
from app.distribution_execution_service import distribution_execution_service
from app.distribution_types import DistributionActionStatus, DistributionPlatform
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import customer_telegram_client_publish_service
from app.telegram_profile_conversion import (
    TelegramProfileMutationError,
    telegram_profile_conversion_service,
)

TELEGRAM_PROFILE_PACK_NAMESPACE = "telegram_profile_conversion_pack"

_MAX_BIO_LENGTH = 70
_MAX_AVATAR_BYTES = 5 * 1024 * 1024
_ALLOWED_AVATAR_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class TelegramProfilePackError(RuntimeError):
    pass


class TelegramProfilePackMode(StrEnum):
    CUSTOMER_OWNED = "CUSTOMER_OWNED"
    PARTIZAN_OWNED = "PARTIZAN_OWNED"


class TelegramProfilePackStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    APPLIED = "APPLIED"
    ROLLED_BACK = "ROLLED_BACK"
    ARCHIVED = "ARCHIVED"


class TelegramProfileCTAType(StrEnum):
    TELEGRAM_CHANNEL_INVITE = "TELEGRAM_CHANNEL_INVITE"
    TELEGRAM_PUBLIC_LINK = "TELEGRAM_PUBLIC_LINK"
    TELEGRAM_BOT_START = "TELEGRAM_BOT_START"
    TRACKED_REDIRECT = "TRACKED_REDIRECT"
    EXTERNAL_URL = "EXTERNAL_URL"


class TelegramProfileAvatarInput(BaseModel):
    filename: str = Field(default="avatar.jpg", min_length=1, max_length=120)
    mime_type: str = Field(min_length=1, max_length=80)
    content_base64: str = Field(min_length=4)

    @field_validator("mime_type")
    @classmethod
    def validate_mime_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_AVATAR_MIME_TYPES:
            raise ValueError("Avatar must be JPEG, PNG or WebP")
        return normalized


class TelegramProfileAvatarView(BaseModel):
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview_path: str


class TelegramProfilePackCreateRequest(BaseModel):
    action_id: UUID
    name: str = Field(min_length=1, max_length=120)
    mode: TelegramProfilePackMode = TelegramProfilePackMode.CUSTOMER_OWNED
    display_name: str | None = Field(default=None, min_length=1, max_length=129)
    bio: str = Field(min_length=1, max_length=_MAX_BIO_LENGTH)
    cta_type: TelegramProfileCTAType
    cta_value: str = Field(min_length=5, max_length=500)
    avatar: TelegramProfileAvatarInput | None = None
    story_enabled: bool = False

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
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
    def normalize_cta(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_cta_is_visible(self) -> TelegramProfilePackCreateRequest:
        if self.cta_value not in self.bio:
            raise ValueError("Telegram bio must contain the exact CTA value")
        return self


class TelegramProfilePackUpdateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    display_name: str | None = Field(default=None, min_length=1, max_length=129)
    bio: str = Field(min_length=1, max_length=_MAX_BIO_LENGTH)
    cta_type: TelegramProfileCTAType
    cta_value: str = Field(min_length=5, max_length=500)
    avatar: TelegramProfileAvatarInput | None = None
    keep_existing_avatar: bool = False
    story_enabled: bool = False

    @field_validator("display_name")
    @classmethod
    def normalize_display_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("Telegram display name is required")
        return normalized

    @field_validator("bio")
    @classmethod
    def normalize_bio(cls, value: str) -> str:
        return value.strip()

    @field_validator("cta_value")
    @classmethod
    def normalize_cta(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_update(self) -> TelegramProfilePackUpdateRequest:
        if not self.bio:
            raise ValueError("Telegram bio is required")
        if self.cta_value not in self.bio:
            raise ValueError("Telegram bio must contain the exact CTA value")
        if self.avatar is not None and self.keep_existing_avatar:
            raise ValueError("Choose either a new avatar or keep_existing_avatar")
        return self


class TelegramProfilePackApprovalRequest(BaseModel):
    confirm: bool = False
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class TelegramProfilePackApplyRequest(BaseModel):
    confirm_apply: bool = False
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class TelegramProfilePackRollbackRequest(BaseModel):
    confirm_rollback: bool = False


class TelegramProfilePackView(BaseModel):
    id: UUID
    project_id: UUID
    product_id: UUID
    action_id: UUID
    experiment_id: UUID
    campaign_id: UUID | None = None
    action_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: TelegramProfilePackMode
    name: str
    display_name: str | None = None
    bio: str
    cta_type: TelegramProfileCTAType
    cta_value: str
    avatar: TelegramProfileAvatarView | None = None
    story_enabled: bool = False
    status: TelegramProfilePackStatus
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_fingerprint: str | None = None
    approved_at: datetime | None = None
    mutation_id: UUID | None = None
    applied_at: datetime | None = None
    rolled_back_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class TelegramProfilePreviewState(BaseModel):
    display_name: str | None = None
    bio: str
    avatar_present: bool
    avatar_preview_path: str | None = None


class TelegramProfilePackPreview(BaseModel):
    pack: TelegramProfilePackView
    current: TelegramProfilePreviewState
    proposed: TelegramProfilePreviewState
    changed_fields: list[str] = Field(default_factory=list)
    requires_confirmation: bool = True


class TelegramProfileConversionPackService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        funnel_service=None,
        execution_service=None,
        publish_service=None,
        mutation_service=None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._funnel = funnel_service or customer_funnel_service
        self._execution = execution_service or distribution_execution_service
        self._publish = publish_service or customer_telegram_client_publish_service
        self._mutations = mutation_service or telegram_profile_conversion_service

    def create(
        self,
        project_id: UUID,
        customer_token: str,
        payload: TelegramProfilePackCreateRequest,
    ) -> TelegramProfilePackView:
        _, action, experiment = self._validated_scope(
            project_id,
            customer_token,
            payload.action_id,
        )
        if payload.mode != TelegramProfilePackMode.CUSTOMER_OWNED:
            raise TelegramProfilePackError(
                "Partizan-managed profile packs are reserved for the managed-account rollout"
            )
        if payload.story_enabled:
            raise TelegramProfilePackError(
                "Telegram stories are reserved for the stories rollout"
            )
        self._validate_cta(payload.cta_type, payload.cta_value)
        avatar_record = self._avatar_record(
            project_id,
            uuid4(),
            payload.avatar,
        )
        pack_id = UUID(str(avatar_record.pop("_pack_id")))
        now = datetime.now(UTC)
        pack = {
            "id": str(pack_id),
            "project_id": str(project_id),
            "product_id": str(experiment.product_id),
            "action_id": str(action.id),
            "experiment_id": str(experiment.id),
            "campaign_id": str(action.campaign_slot_id) if action.campaign_slot_id else None,
            "action_fingerprint": self._action_fingerprint(action),
            "mode": payload.mode.value,
            "name": payload.name.strip(),
            "display_name": payload.display_name,
            "bio": payload.bio,
            "cta_type": payload.cta_type.value,
            "cta_value": payload.cta_value,
            "avatar": avatar_record.get("view"),
            "avatar_content_base64": avatar_record.get("content_base64"),
            "story_enabled": payload.story_enabled,
            "status": TelegramProfilePackStatus.DRAFT.value,
            "approved_fingerprint": None,
            "approved_at": None,
            "mutation_id": None,
            "applied_at": None,
            "rolled_back_at": None,
            "last_error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        pack["fingerprint"] = self._fingerprint(pack)
        self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), pack)
        return self._view(pack)

    def update_draft(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
        payload: TelegramProfilePackUpdateRequest,
    ) -> TelegramProfilePackView:
        current = self._payload_for_project(project_id, customer_token, pack_id)
        if current["status"] not in {
            TelegramProfilePackStatus.DRAFT.value,
            TelegramProfilePackStatus.READY.value,
        }:
            raise TelegramProfilePackError(
                "Only DRAFT or READY Telegram profile packs can be edited"
            )
        if payload.story_enabled:
            raise TelegramProfilePackError(
                "Telegram stories are reserved for the stories rollout"
            )
        self._validate_cta(payload.cta_type, payload.cta_value)
        self._assert_pack_still_matches_action(current)

        avatar_view = current.get("avatar") if payload.keep_existing_avatar else None
        avatar_content = (
            current.get("avatar_content_base64") if payload.keep_existing_avatar else None
        )
        if payload.avatar is not None:
            avatar_record = self._avatar_record(project_id, pack_id, payload.avatar)
            avatar_view = avatar_record.get("view")
            avatar_content = avatar_record.get("content_base64")

        updated = {
            **current,
            "name": payload.name.strip(),
            "display_name": payload.display_name,
            "bio": payload.bio,
            "cta_type": payload.cta_type.value,
            "cta_value": payload.cta_value,
            "avatar": avatar_view,
            "avatar_content_base64": avatar_content,
            "story_enabled": payload.story_enabled,
            "status": TelegramProfilePackStatus.DRAFT.value,
            "approved_fingerprint": None,
            "approved_at": None,
            "last_error": None,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        updated["fingerprint"] = self._fingerprint(updated)
        self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), updated)
        return self._view(updated)

    def approve(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
        payload: TelegramProfilePackApprovalRequest,
    ) -> TelegramProfilePackView:
        if not payload.confirm:
            raise TelegramProfilePackError("Explicit profile-pack approval is required")
        current = self._payload_for_project(project_id, customer_token, pack_id)
        if current["status"] not in {
            TelegramProfilePackStatus.DRAFT.value,
            TelegramProfilePackStatus.READY.value,
        }:
            raise TelegramProfilePackError(
                "Only a draft Telegram profile pack can be approved"
            )
        if payload.expected_fingerprint != current["fingerprint"]:
            raise TelegramProfilePackError(
                "Telegram profile pack changed; refresh and review it again"
            )
        self._assert_pack_still_matches_action(current)
        now = datetime.now(UTC)
        updated = {
            **current,
            "status": TelegramProfilePackStatus.READY.value,
            "approved_fingerprint": current["fingerprint"],
            "approved_at": now.isoformat(),
            "last_error": None,
            "updated_at": now.isoformat(),
        }
        self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), updated)
        return self._view(updated)

    async def preview(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramProfilePackPreview:
        current = self._payload_for_project(project_id, customer_token, pack_id)
        self._assert_pack_still_matches_action(current)
        live = await self._publish.profile_internal(project_id)
        pack = self._view(current)
        changed_fields: list[str] = []
        if self._normal_text(live.about) != self._normal_text(pack.bio):
            changed_fields.append("bio")
        if pack.display_name is not None and live.display_name != pack.display_name:
            changed_fields.append("display_name")
        if pack.avatar is not None:
            changed_fields.append("avatar")

        return TelegramProfilePackPreview(
            pack=pack,
            current=TelegramProfilePreviewState(
                display_name=live.display_name,
                bio=live.about,
                avatar_present=live.avatar is not None,
            ),
            proposed=TelegramProfilePreviewState(
                display_name=pack.display_name or live.display_name,
                bio=pack.bio,
                avatar_present=(pack.avatar is not None or live.avatar is not None),
                avatar_preview_path=(
                    pack.avatar.preview_path if pack.avatar is not None else None
                ),
            ),
            changed_fields=changed_fields,
            requires_confirmation=pack.status != TelegramProfilePackStatus.READY,
        )

    async def apply(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
        payload: TelegramProfilePackApplyRequest,
    ) -> TelegramProfilePackView:
        if not payload.confirm_apply:
            raise TelegramProfilePackError("Explicit profile-pack apply confirmation is required")
        current = self._payload_for_project(project_id, customer_token, pack_id)
        if current["status"] == TelegramProfilePackStatus.APPLIED.value:
            return self._view(current)
        if current["status"] != TelegramProfilePackStatus.READY.value:
            raise TelegramProfilePackError("Telegram profile pack must be approved before apply")
        if (
            payload.expected_fingerprint != current["fingerprint"]
            or current.get("approved_fingerprint") != current["fingerprint"]
        ):
            raise TelegramProfilePackError(
                "Approved Telegram profile pack changed; refresh and approve it again"
            )
        self._assert_pack_still_matches_action(current)
        action = self._execution.get_action(UUID(str(current["action_id"])))
        if action.status != DistributionActionStatus.APPROVED:
            raise TelegramProfilePackError(
                "Telegram action must be APPROVED before applying its profile pack"
            )
        self._assert_no_other_applied_pack(current)

        avatar_content = self._decode_avatar_payload(current)
        try:
            mutation = await self._mutations.apply_internal(
                project_id,
                about=str(current["bio"]),
                display_name=(
                    str(current["display_name"])
                    if current.get("display_name") is not None
                    else None
                ),
                avatar_content=avatar_content,
                avatar_filename=(
                    str(current["avatar"]["filename"])
                    if current.get("avatar")
                    else "avatar.jpg"
                ),
                campaign_id=(
                    UUID(str(current["campaign_id"]))
                    if current.get("campaign_id")
                    else None
                ),
                experiment_id=UUID(str(current["experiment_id"])),
            )
        except TelegramProfileMutationError as exc:
            failed = {
                **current,
                "last_error": str(exc)[:1000],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), failed)
            raise TelegramProfilePackError(str(exc)) from exc

        now = datetime.now(UTC)
        updated = {
            **current,
            "status": TelegramProfilePackStatus.APPLIED.value,
            "mutation_id": str(mutation.mutation_id),
            "applied_at": now.isoformat(),
            "last_error": None,
            "updated_at": now.isoformat(),
        }
        self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), updated)
        return self._view(updated)

    async def rollback(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
        payload: TelegramProfilePackRollbackRequest,
    ) -> TelegramProfilePackView:
        if not payload.confirm_rollback:
            raise TelegramProfilePackError("Explicit profile rollback confirmation is required")
        current = self._payload_for_project(project_id, customer_token, pack_id)
        if current["status"] == TelegramProfilePackStatus.ROLLED_BACK.value:
            return self._view(current)
        if current["status"] != TelegramProfilePackStatus.APPLIED.value:
            raise TelegramProfilePackError("Only an APPLIED Telegram profile pack can be rolled back")
        mutation_id_raw = current.get("mutation_id")
        if not mutation_id_raw:
            raise TelegramProfilePackError("Applied Telegram profile pack has no mutation record")

        try:
            await self._mutations.rollback_internal(project_id, UUID(str(mutation_id_raw)))
        except TelegramProfileMutationError as exc:
            failed = {
                **current,
                "last_error": str(exc)[:1000],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), failed)
            raise TelegramProfilePackError(str(exc)) from exc

        now = datetime.now(UTC)
        updated = {
            **current,
            "status": TelegramProfilePackStatus.ROLLED_BACK.value,
            "rolled_back_at": now.isoformat(),
            "last_error": None,
            "updated_at": now.isoformat(),
        }
        self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), updated)
        return self._view(updated)

    def archive(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramProfilePackView:
        current = self._payload_for_project(project_id, customer_token, pack_id)
        if current["status"] == TelegramProfilePackStatus.APPLIED.value:
            raise TelegramProfilePackError("Roll back an applied profile pack before archiving it")
        updated = {
            **current,
            "status": TelegramProfilePackStatus.ARCHIVED.value,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._store.put(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id), updated)
        return self._view(updated)

    def get(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramProfilePackView:
        return self._view(self._payload_for_project(project_id, customer_token, pack_id))

    def list(
        self,
        project_id: UUID,
        customer_token: str,
    ) -> list[TelegramProfilePackView]:
        self._funnel.get_project_payload(project_id, customer_token)
        result: list[TelegramProfilePackView] = []
        for payload in self._store.list_namespace(TELEGRAM_PROFILE_PACK_NAMESPACE):
            if str(payload.get("project_id") or "") != str(project_id):
                continue
            try:
                result.append(self._view(payload))
            except ValueError:
                continue
        result.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return result

    def for_action(
        self,
        project_id: UUID,
        customer_token: str,
        action_id: UUID,
    ) -> TelegramProfilePackView | None:
        packs = [
            pack
            for pack in self.list(project_id, customer_token)
            if pack.action_id == action_id
            and pack.status != TelegramProfilePackStatus.ARCHIVED
        ]
        return packs[0] if packs else None

    def avatar_bytes(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> tuple[bytes, str, str]:
        current = self._payload_for_project(project_id, customer_token, pack_id)
        avatar = current.get("avatar")
        content_b64 = current.get("avatar_content_base64")
        if not isinstance(avatar, dict) or not content_b64:
            raise TelegramProfilePackError("Telegram profile pack has no avatar")
        try:
            content = base64.b64decode(str(content_b64), validate=True)
        except (ValueError, TypeError) as exc:
            raise TelegramProfilePackError("Stored Telegram avatar is invalid") from exc
        return content, str(avatar["mime_type"]), str(avatar["filename"])

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TELEGRAM_PROFILE_PACK_NAMESPACE)

    def _validated_scope(self, project_id: UUID, customer_token: str, action_id: UUID):
        project = self._funnel.get_project_payload(project_id, customer_token)
        try:
            action = self._execution.get_action(action_id)
        except KeyError as exc:
            raise TelegramProfilePackError("Telegram distribution action not found") from exc
        if action.platform != DistributionPlatform.TELEGRAM:
            raise TelegramProfilePackError("Profile packs can only be bound to Telegram actions")
        if action.status not in {
            DistributionActionStatus.PREPARED,
            DistributionActionStatus.APPROVED,
        }:
            raise TelegramProfilePackError(
                "Profile pack requires a PREPARED or APPROVED Telegram action"
            )
        if action.experiment_id is None:
            raise TelegramProfilePackError("Telegram action has no experiment")
        try:
            experiment = self._execution.get_experiment(action.experiment_id)
        except KeyError as exc:
            raise TelegramProfilePackError("Telegram experiment not found") from exc
        if str(project.get("product_id") or "") != str(experiment.product_id):
            raise TelegramProfilePackError(
                "Telegram action does not belong to this customer project"
            )
        return project, action, experiment

    def _payload_for_project(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> dict:
        self._funnel.get_project_payload(project_id, customer_token)
        payload = self._store.get(TELEGRAM_PROFILE_PACK_NAMESPACE, str(pack_id))
        if payload is None:
            raise TelegramProfilePackError("Telegram profile pack not found")
        if str(payload.get("project_id") or "") != str(project_id):
            raise TelegramProfilePackError("Telegram profile pack belongs to another project")
        return payload

    def _assert_pack_still_matches_action(self, payload: dict) -> None:
        try:
            action = self._execution.get_action(UUID(str(payload["action_id"])))
            experiment = self._execution.get_experiment(UUID(str(payload["experiment_id"])))
        except (KeyError, ValueError) as exc:
            raise TelegramProfilePackError(
                "Telegram profile pack action or experiment no longer exists"
            ) from exc
        if (
            action.platform != DistributionPlatform.TELEGRAM
            or action.experiment_id != experiment.id
            or str(experiment.product_id) != str(payload["product_id"])
        ):
            raise TelegramProfilePackError(
                "Telegram profile pack no longer matches its distribution action"
            )
        if action.status not in {
            DistributionActionStatus.PREPARED,
            DistributionActionStatus.APPROVED,
        }:
            raise TelegramProfilePackError(
                "Telegram profile pack action is no longer eligible"
            )
        if self._action_fingerprint(action) != str(payload.get("action_fingerprint") or ""):
            raise TelegramProfilePackError(
                "Telegram action changed after this profile pack was created; create a new pack"
            )

    def _assert_no_other_applied_pack(self, payload: dict) -> None:
        for other in self._store.list_namespace(TELEGRAM_PROFILE_PACK_NAMESPACE):
            if str(other.get("id") or "") == str(payload.get("id") or ""):
                continue
            if (
                str(other.get("project_id") or "") == str(payload.get("project_id") or "")
                and str(other.get("action_id") or "") == str(payload.get("action_id") or "")
                and other.get("status") == TelegramProfilePackStatus.APPLIED.value
            ):
                raise TelegramProfilePackError(
                    "Another Telegram profile pack is already applied for this action"
                )

    def _avatar_record(
        self,
        project_id: UUID,
        pack_id: UUID,
        avatar: TelegramProfileAvatarInput | None,
    ) -> dict:
        if avatar is None:
            return {"_pack_id": str(pack_id), "view": None, "content_base64": None}
        try:
            content = base64.b64decode(avatar.content_base64, validate=True)
        except (ValueError, TypeError) as exc:
            raise TelegramProfilePackError("Avatar content is not valid base64") from exc
        if not content:
            raise TelegramProfilePackError("Avatar content is empty")
        if len(content) > _MAX_AVATAR_BYTES:
            raise TelegramProfilePackError(
                f"Avatar exceeds the {_MAX_AVATAR_BYTES}-byte profile-pack limit"
            )
        self._validate_avatar_signature(content, avatar.mime_type)
        sha256 = hashlib.sha256(content).hexdigest()
        return {
            "_pack_id": str(pack_id),
            "view": {
                "filename": avatar.filename.strip(),
                "mime_type": avatar.mime_type,
                "size_bytes": len(content),
                "sha256": sha256,
                "preview_path": (
                    f"/customer/workspace/{project_id}/telegram/profile-packs/{pack_id}/avatar"
                ),
            },
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    def _decode_avatar_payload(self, payload: dict) -> bytes | None:
        content_b64 = payload.get("avatar_content_base64")
        if not content_b64:
            return None
        try:
            return base64.b64decode(str(content_b64), validate=True)
        except (ValueError, TypeError) as exc:
            raise TelegramProfilePackError("Stored Telegram avatar is invalid") from exc

    def _validate_avatar_signature(self, content: bytes, mime_type: str) -> None:
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
            raise TelegramProfilePackError("Avatar bytes do not match the declared image type")

    def _validate_cta(self, cta_type: TelegramProfileCTAType, value: str) -> None:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise TelegramProfilePackError("Profile CTA must be an absolute http(s) URL")
        host = (parts.hostname or "").lower()
        if cta_type in {
            TelegramProfileCTAType.TELEGRAM_CHANNEL_INVITE,
            TelegramProfileCTAType.TELEGRAM_PUBLIC_LINK,
            TelegramProfileCTAType.TELEGRAM_BOT_START,
        } and host not in {"t.me", "telegram.me", "www.t.me", "www.telegram.me"}:
            raise TelegramProfilePackError(
                "Telegram-native CTA types must point to a Telegram link"
            )
        if cta_type == TelegramProfileCTAType.TELEGRAM_CHANNEL_INVITE:
            path = parts.path.strip("/")
            if not path.startswith("+") and not path.startswith("joinchat/"):
                raise TelegramProfilePackError(
                    "Telegram channel invite CTA must use an invite link"
                )
        if cta_type == TelegramProfileCTAType.TELEGRAM_BOT_START:
            query = dict(
                pair.split("=", 1) if "=" in pair else (pair, "")
                for pair in parts.query.split("&")
                if pair
            )
            if not query.get("start"):
                raise TelegramProfilePackError(
                    "Telegram bot CTA must include a start parameter"
                )

    def _action_fingerprint(self, action) -> str:
        payload = {
            "action_id": str(action.id),
            "experiment_id": str(action.experiment_id or ""),
            "action_type": action.action_type.value,
            "target_url": str(action.target_url or ""),
            "content_text": str(action.content_text or ""),
        }
        return hashlib.sha256(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def _fingerprint(self, payload: dict) -> str:
        avatar = payload.get("avatar") if isinstance(payload.get("avatar"), dict) else None
        fingerprint_payload = {
            "project_id": str(payload["project_id"]),
            "product_id": str(payload["product_id"]),
            "action_id": str(payload["action_id"]),
            "experiment_id": str(payload["experiment_id"]),
            "campaign_id": str(payload.get("campaign_id") or ""),
            "action_fingerprint": str(payload["action_fingerprint"]),
            "mode": str(payload["mode"]),
            "name": str(payload["name"]),
            "display_name": payload.get("display_name"),
            "bio": str(payload["bio"]),
            "cta_type": str(payload["cta_type"]),
            "cta_value": str(payload["cta_value"]),
            "avatar_sha256": avatar.get("sha256") if avatar else None,
            "story_enabled": bool(payload.get("story_enabled")),
        }
        return hashlib.sha256(
            json.dumps(
                fingerprint_payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def _view(self, payload: dict) -> TelegramProfilePackView:
        safe = {key: value for key, value in payload.items() if key != "avatar_content_base64"}
        return TelegramProfilePackView.model_validate(safe)

    def _normal_text(self, value: str) -> str:
        return " ".join(value.split())


telegram_profile_conversion_pack_service = TelegramProfileConversionPackService()
