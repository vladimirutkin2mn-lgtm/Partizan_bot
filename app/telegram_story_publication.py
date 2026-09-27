from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    CustomerTelegramClientPublishError,
    customer_telegram_client_publish_service,
)
from app.telegram_profile_conversion_pack import (
    TelegramProfilePackStatus,
    telegram_profile_conversion_pack_service,
)
from app.telegram_story_signal import (
    TelegramStorySignalAttachRequest,
    TelegramStorySignalError,
    telegram_story_signal_service,
)

TELEGRAM_STORY_PUBLICATION_NAMESPACE = "telegram_story_publication"

_MAX_STORY_IMAGE_BYTES = 10 * 1024 * 1024
_MAX_STORY_CAPTION_LENGTH = 1024
_ALLOWED_STORY_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})


class TelegramStoryPublicationError(RuntimeError):
    pass


class TelegramStoryPublicationStatus(StrEnum):
    DRAFT = "DRAFT"
    READY = "READY"
    PUBLISHED = "PUBLISHED"
    DELETED = "DELETED"
    FAILED = "FAILED"


class TelegramStoryImageInput(BaseModel):
    filename: str = Field(default="story.jpg", min_length=1, max_length=120)
    mime_type: str = Field(min_length=1, max_length=80)
    content_base64: str = Field(min_length=4)

    @field_validator("mime_type")
    @classmethod
    def normalize_mime_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in _ALLOWED_STORY_IMAGE_MIME_TYPES:
            raise ValueError("Story image must be JPEG, PNG or WebP")
        return normalized


class TelegramStoryImageView(BaseModel):
    filename: str
    mime_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    preview_path: str


class TelegramStoryPublicationCreateRequest(BaseModel):
    caption: str = Field(default="", max_length=_MAX_STORY_CAPTION_LENGTH)
    image: TelegramStoryImageInput
    noforwards: bool = False

    @field_validator("caption")
    @classmethod
    def normalize_caption(cls, value: str) -> str:
        return value.strip()


class TelegramStoryPublicationApprovalRequest(BaseModel):
    confirm: bool = False
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class TelegramStoryPublicationPublishRequest(BaseModel):
    confirm_publish: bool = False
    expected_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")


class TelegramStoryPublicationDeleteRequest(BaseModel):
    confirm_delete: bool = False


class TelegramStoryPublicationView(BaseModel):
    id: UUID
    project_id: UUID
    product_id: UUID
    pack_id: UUID
    action_id: UUID
    experiment_id: UUID
    profile_pack_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    caption: str
    image: TelegramStoryImageView
    noforwards: bool = False
    period_seconds: int = 86400
    status: TelegramStoryPublicationStatus
    fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    approved_fingerprint: str | None = None
    approved_at: datetime | None = None
    story_id: int | None = Field(default=None, gt=0)
    published_at: datetime | None = None
    deleted_at: datetime | None = None
    view_count: int = Field(default=0, ge=0)
    forwards_count: int = Field(default=0, ge=0)
    reactions_count: int = Field(default=0, ge=0)
    last_observed_at: datetime | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime


class TelegramStoryPublicationService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        pack_service=None,
        publish_service=None,
        story_signal_service=None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._packs = pack_service or telegram_profile_conversion_pack_service
        self._publish = publish_service or customer_telegram_client_publish_service
        self._signals = story_signal_service or telegram_story_signal_service

    def create(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
        payload: TelegramStoryPublicationCreateRequest,
    ) -> TelegramStoryPublicationView:
        pack = self._packs.get(project_id, customer_token, pack_id)
        if pack.status not in {
            TelegramProfilePackStatus.READY,
            TelegramProfilePackStatus.APPLIED,
        }:
            raise TelegramStoryPublicationError(
                "Review the Telegram profile pack before preparing a story"
            )
        existing = self._for_pack(project_id, pack.id)
        if existing is not None and existing.status != TelegramStoryPublicationStatus.DELETED:
            return existing

        publication_id = uuid4()
        image = self._image_record(project_id, publication_id, payload.image)
        now = datetime.now(UTC)
        record = {
            "id": str(publication_id),
            "project_id": str(project_id),
            "product_id": str(pack.product_id),
            "pack_id": str(pack.id),
            "action_id": str(pack.action_id),
            "experiment_id": str(pack.experiment_id),
            "profile_pack_fingerprint": pack.fingerprint,
            "caption": payload.caption,
            "image": image["view"],
            "image_content_base64": image["content_base64"],
            "noforwards": payload.noforwards,
            "period_seconds": 86400,
            "status": TelegramStoryPublicationStatus.DRAFT.value,
            "approved_fingerprint": None,
            "approved_at": None,
            "story_id": None,
            "published_at": None,
            "deleted_at": None,
            "view_count": 0,
            "forwards_count": 0,
            "reactions_count": 0,
            "last_observed_at": None,
            "last_error": None,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat(),
        }
        record["fingerprint"] = self._fingerprint(record)
        self._persist(record)
        return self._view(record)

    def approve(
        self,
        project_id: UUID,
        customer_token: str,
        publication_id: UUID,
        payload: TelegramStoryPublicationApprovalRequest,
    ) -> TelegramStoryPublicationView:
        if not payload.confirm:
            raise TelegramStoryPublicationError("Explicit story approval is required")
        current = self._payload_for_project(
            project_id,
            customer_token,
            publication_id,
        )
        if current["status"] not in {
            TelegramStoryPublicationStatus.DRAFT.value,
            TelegramStoryPublicationStatus.READY.value,
        }:
            raise TelegramStoryPublicationError("Only a story draft can be approved")
        if payload.expected_fingerprint != current["fingerprint"]:
            raise TelegramStoryPublicationError(
                "Telegram story changed; refresh and review it again"
            )
        self._assert_pack_fingerprint(current, customer_token)
        now = datetime.now(UTC)
        updated = {
            **current,
            "status": TelegramStoryPublicationStatus.READY.value,
            "approved_fingerprint": current["fingerprint"],
            "approved_at": now.isoformat(),
            "last_error": None,
            "updated_at": now.isoformat(),
        }
        self._persist(updated)
        return self._view(updated)

    async def publish(
        self,
        project_id: UUID,
        customer_token: str,
        publication_id: UUID,
        payload: TelegramStoryPublicationPublishRequest,
    ) -> TelegramStoryPublicationView:
        if not payload.confirm_publish:
            raise TelegramStoryPublicationError(
                "Explicit Telegram story publish confirmation is required"
            )
        current = self._payload_for_project(
            project_id,
            customer_token,
            publication_id,
        )
        if current["status"] == TelegramStoryPublicationStatus.PUBLISHED.value:
            return self._view(current)
        if current["status"] != TelegramStoryPublicationStatus.READY.value:
            raise TelegramStoryPublicationError(
                "Telegram story must be approved before publishing"
            )
        if (
            payload.expected_fingerprint != current["fingerprint"]
            or current.get("approved_fingerprint") != current["fingerprint"]
        ):
            raise TelegramStoryPublicationError(
                "Approved Telegram story changed; refresh and approve it again"
            )

        pack = self._assert_pack_fingerprint(current, customer_token)
        if pack.status != TelegramProfilePackStatus.APPLIED:
            raise TelegramStoryPublicationError(
                "Apply the reviewed Telegram profile pack before publishing its story"
            )

        image_content = self._decode_image(current)
        try:
            result = await self._publish.publish_story_internal(
                project_id,
                content=image_content,
                filename=str(current["image"]["filename"]),
                caption=str(current["caption"]),
                period_seconds=int(current["period_seconds"]),
                noforwards=bool(current["noforwards"]),
            )
        except CustomerTelegramClientPublishError as exc:
            failed = {
                **current,
                "status": TelegramStoryPublicationStatus.FAILED.value,
                "last_error": str(exc)[:1000],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._persist(failed)
            raise TelegramStoryPublicationError(str(exc)) from exc

        now = datetime.now(UTC)
        published = {
            **current,
            "status": TelegramStoryPublicationStatus.PUBLISHED.value,
            "story_id": result.story_id,
            "published_at": result.published_at.isoformat(),
            "last_error": None,
            "updated_at": now.isoformat(),
        }
        self._persist(published)

        try:
            self._signals.attach(
                project_id,
                customer_token,
                UUID(str(current["pack_id"])),
                TelegramStorySignalAttachRequest(
                    story_id=result.story_id,
                    confirm_attach=True,
                ),
            )
        except TelegramStorySignalError as exc:
            published = {
                **published,
                "last_error": (
                    "Story published, but proxy-signal attachment failed: "
                    f"{str(exc)[:800]}"
                ),
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._persist(published)

        return self._view(published)

    async def observe(
        self,
        project_id: UUID,
        customer_token: str,
        publication_id: UUID,
    ) -> TelegramStoryPublicationView:
        current = self._payload_for_project(
            project_id,
            customer_token,
            publication_id,
        )
        if current["status"] != TelegramStoryPublicationStatus.PUBLISHED.value:
            raise TelegramStoryPublicationError(
                "Only a published Telegram story can be observed"
            )
        story_id = int(current.get("story_id") or 0)
        if story_id <= 0:
            raise TelegramStoryPublicationError(
                "Published Telegram story has no confirmed story id"
            )
        try:
            snapshot = await self._publish.story_views_internal(
                project_id,
                story_id,
            )
        except CustomerTelegramClientPublishError as exc:
            failed = {
                **current,
                "last_error": str(exc)[:1000],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._persist(failed)
            raise TelegramStoryPublicationError(str(exc)) from exc

        try:
            self._signals.record_views_internal(
                project_id,
                UUID(str(current["pack_id"])),
                story_id=story_id,
                cumulative_views=snapshot.views_count,
            )
        except TelegramStorySignalError as exc:
            signal_error = str(exc)[:800]
        else:
            signal_error = None

        now = datetime.now(UTC)
        updated = {
            **current,
            "view_count": max(int(current.get("view_count") or 0), snapshot.views_count),
            "forwards_count": max(
                int(current.get("forwards_count") or 0),
                snapshot.forwards_count,
            ),
            "reactions_count": max(
                int(current.get("reactions_count") or 0),
                snapshot.reactions_count,
            ),
            "last_observed_at": now.isoformat(),
            "last_error": (
                f"Story observed, but proxy analytics failed: {signal_error}"
                if signal_error
                else None
            ),
            "updated_at": now.isoformat(),
        }
        self._persist(updated)
        return self._view(updated)

    async def delete(
        self,
        project_id: UUID,
        customer_token: str,
        publication_id: UUID,
        payload: TelegramStoryPublicationDeleteRequest,
    ) -> TelegramStoryPublicationView:
        if not payload.confirm_delete:
            raise TelegramStoryPublicationError(
                "Explicit Telegram story delete confirmation is required"
            )
        current = self._payload_for_project(
            project_id,
            customer_token,
            publication_id,
        )
        if current["status"] == TelegramStoryPublicationStatus.DELETED.value:
            return self._view(current)
        if current["status"] != TelegramStoryPublicationStatus.PUBLISHED.value:
            raise TelegramStoryPublicationError(
                "Only a published Telegram story can be deleted"
            )
        story_id = int(current.get("story_id") or 0)
        if story_id <= 0:
            raise TelegramStoryPublicationError(
                "Published Telegram story has no confirmed story id"
            )

        try:
            observed = await self.observe(
                project_id,
                customer_token,
                publication_id,
            )
        except TelegramStoryPublicationError:
            observed = self._view(current)

        try:
            await self._publish.delete_story_internal(project_id, story_id)
        except CustomerTelegramClientPublishError as exc:
            failed = {
                **self._payload(publication_id),
                "last_error": str(exc)[:1000],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._persist(failed)
            raise TelegramStoryPublicationError(str(exc)) from exc

        try:
            self._signals.record_views_internal(
                project_id,
                UUID(str(current["pack_id"])),
                story_id=story_id,
                cumulative_views=observed.view_count,
                expired=True,
            )
        except TelegramStorySignalError:
            pass

        now = datetime.now(UTC)
        deleted = {
            **self._payload(publication_id),
            "status": TelegramStoryPublicationStatus.DELETED.value,
            "deleted_at": now.isoformat(),
            "last_error": None,
            "updated_at": now.isoformat(),
        }
        self._persist(deleted)
        return self._view(deleted)

    def get(
        self,
        project_id: UUID,
        customer_token: str,
        publication_id: UUID,
    ) -> TelegramStoryPublicationView:
        return self._view(
            self._payload_for_project(project_id, customer_token, publication_id)
        )

    def for_pack(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramStoryPublicationView | None:
        pack = self._packs.get(project_id, customer_token, pack_id)
        return self._for_pack(project_id, pack.id)

    def image_bytes(
        self,
        project_id: UUID,
        customer_token: str,
        publication_id: UUID,
    ) -> tuple[bytes, str, str]:
        current = self._payload_for_project(
            project_id,
            customer_token,
            publication_id,
        )
        image = current.get("image")
        if not isinstance(image, dict):
            raise TelegramStoryPublicationError("Telegram story image metadata is missing")
        return (
            self._decode_image(current),
            str(image["mime_type"]),
            str(image["filename"]),
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TELEGRAM_STORY_PUBLICATION_NAMESPACE)

    def _payload_for_project(
        self,
        project_id: UUID,
        customer_token: str,
        publication_id: UUID,
    ) -> dict:
        payload = self._store.get(
            TELEGRAM_STORY_PUBLICATION_NAMESPACE,
            str(publication_id),
        )
        if payload is None:
            raise TelegramStoryPublicationError("Telegram story publication not found")
        if str(payload.get("project_id") or "") != str(project_id):
            raise TelegramStoryPublicationError(
                "Telegram story publication belongs to another project"
            )
        self._packs.get(
            project_id,
            customer_token,
            UUID(str(payload["pack_id"])),
        )
        return payload

    def _assert_pack_fingerprint(self, payload: dict, customer_token: str):
        project_id = UUID(str(payload["project_id"]))
        pack = self._packs.get(
            project_id,
            customer_token,
            UUID(str(payload["pack_id"])),
        )
        if pack.fingerprint != str(payload["profile_pack_fingerprint"]):
            raise TelegramStoryPublicationError(
                "Telegram profile pack changed after this story was prepared; create a new story"
            )
        if (
            pack.action_id != UUID(str(payload["action_id"]))
            or pack.experiment_id != UUID(str(payload["experiment_id"]))
        ):
            raise TelegramStoryPublicationError(
                "Telegram story no longer matches its profile experiment"
            )
        return pack

    def _for_pack(
        self,
        project_id: UUID,
        pack_id: UUID,
    ) -> TelegramStoryPublicationView | None:
        matches: list[TelegramStoryPublicationView] = []
        for payload in self._store.list_namespace(TELEGRAM_STORY_PUBLICATION_NAMESPACE):
            if (
                str(payload.get("project_id") or "") == str(project_id)
                and str(payload.get("pack_id") or "") == str(pack_id)
            ):
                try:
                    matches.append(self._view(payload))
                except ValueError:
                    continue
        matches.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return matches[0] if matches else None

    def _image_record(
        self,
        project_id: UUID,
        publication_id: UUID,
        image: TelegramStoryImageInput,
    ) -> dict:
        try:
            content = base64.b64decode(image.content_base64, validate=True)
        except (TypeError, ValueError) as exc:
            raise TelegramStoryPublicationError(
                "Telegram story image is not valid base64"
            ) from exc
        if not content:
            raise TelegramStoryPublicationError("Telegram story image is empty")
        if len(content) > _MAX_STORY_IMAGE_BYTES:
            raise TelegramStoryPublicationError(
                f"Telegram story image exceeds {_MAX_STORY_IMAGE_BYTES} bytes"
            )
        self._validate_image_signature(content, image.mime_type)
        sha256 = hashlib.sha256(content).hexdigest()
        return {
            "view": {
                "filename": image.filename.strip(),
                "mime_type": image.mime_type,
                "size_bytes": len(content),
                "sha256": sha256,
                "preview_path": (
                    f"/customer/workspace/{project_id}/telegram/story-publications/"
                    f"{publication_id}/image"
                ),
            },
            "content_base64": base64.b64encode(content).decode("ascii"),
        }

    def _decode_image(self, payload: dict) -> bytes:
        raw = payload.get("image_content_base64")
        if not raw:
            raise TelegramStoryPublicationError("Stored Telegram story image is missing")
        try:
            return base64.b64decode(str(raw), validate=True)
        except (TypeError, ValueError) as exc:
            raise TelegramStoryPublicationError(
                "Stored Telegram story image is invalid"
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
            raise TelegramStoryPublicationError(
                "Story image bytes do not match the declared image type"
            )

    @staticmethod
    def _fingerprint(payload: dict) -> str:
        image = payload["image"]
        data = {
            "project_id": str(payload["project_id"]),
            "product_id": str(payload["product_id"]),
            "pack_id": str(payload["pack_id"]),
            "action_id": str(payload["action_id"]),
            "experiment_id": str(payload["experiment_id"]),
            "profile_pack_fingerprint": str(payload["profile_pack_fingerprint"]),
            "caption": str(payload["caption"]),
            "image_sha256": str(image["sha256"]),
            "noforwards": bool(payload["noforwards"]),
            "period_seconds": int(payload["period_seconds"]),
        }
        return hashlib.sha256(
            json.dumps(
                data,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()

    def _persist(self, payload: dict) -> None:
        self._store.put(
            TELEGRAM_STORY_PUBLICATION_NAMESPACE,
            str(payload["id"]),
            payload,
        )

    @staticmethod
    def _view(payload: dict) -> TelegramStoryPublicationView:
        safe = {
            key: value
            for key, value in payload.items()
            if key != "image_content_base64"
        }
        return TelegramStoryPublicationView.model_validate(safe)


telegram_story_publication_service = TelegramStoryPublicationService()
