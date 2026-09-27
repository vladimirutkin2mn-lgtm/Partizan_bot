from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import BaseModel, Field

from app.distribution_analytics_schemas import DistributionAnalyticsEventCreate
from app.distribution_analytics_service import distribution_analytics_service
from app.runtime_store import RuntimeStateStore, get_runtime_store
from app.telegram_client_publishing import (
    CustomerTelegramClientPublishError,
    customer_telegram_client_publish_service,
)
from app.telegram_profile_conversion_pack import (
    TelegramProfileCTAType,
    TelegramProfilePackStatus,
    TelegramProfilePackUpdateRequest,
    telegram_profile_conversion_pack_service,
)

TELEGRAM_NATIVE_ATTRIBUTION_NAMESPACE = "telegram_native_attribution"


class TelegramNativeAttributionError(RuntimeError):
    pass


class TelegramNativeAttributionStatus(StrEnum):
    PROVISIONING = "PROVISIONING"
    READY = "READY"
    REVOKED = "REVOKED"
    FAILED = "FAILED"


class TelegramNativeAttributionKind(StrEnum):
    CHANNEL_INVITE = "CHANNEL_INVITE"


class TelegramNativeAttributionView(BaseModel):
    id: UUID
    project_id: UUID
    product_id: UUID
    pack_id: UUID
    action_id: UUID
    experiment_id: UUID
    kind: TelegramNativeAttributionKind
    status: TelegramNativeAttributionStatus
    channel_username: str
    source_url: str
    native_url: str
    join_count: int = Field(default=0, ge=0)
    attributed_join_count: int = Field(default=0, ge=0)
    requested_count: int = Field(default=0, ge=0)
    last_delta_joins: int = Field(default=0, ge=0)
    analytics_pending: bool = False
    last_event_id: UUID | None = None
    last_error: str | None = None
    created_at: datetime
    updated_at: datetime
    last_synced_at: datetime | None = None


class TelegramNativeAttributionService:
    def __init__(
        self,
        *,
        store: RuntimeStateStore | None = None,
        pack_service=None,
        publish_service=None,
        analytics_service=None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._packs = pack_service or telegram_profile_conversion_pack_service
        self._publish = publish_service or customer_telegram_client_publish_service
        self._analytics = analytics_service or distribution_analytics_service

    async def provision_channel_invite(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramNativeAttributionView:
        existing = self._for_pack(project_id, customer_token, pack_id)
        if existing is not None and existing.status == TelegramNativeAttributionStatus.READY:
            return existing

        pack = self._packs.get(project_id, customer_token, pack_id)
        if pack.status != TelegramProfilePackStatus.DRAFT:
            raise TelegramNativeAttributionError(
                "Native Telegram attribution must be provisioned before profile-pack approval"
            )
        if pack.cta_type != TelegramProfileCTAType.TELEGRAM_PUBLIC_LINK:
            raise TelegramNativeAttributionError(
                "Channel invite provisioning requires a public Telegram channel CTA"
            )
        channel_username = self._public_channel_username(pack.cta_value)
        attribution_id = existing.id if existing is not None else uuid4()
        now = datetime.now(UTC)
        base = {
            "id": str(attribution_id),
            "project_id": str(project_id),
            "product_id": str(pack.product_id),
            "pack_id": str(pack.id),
            "action_id": str(pack.action_id),
            "experiment_id": str(pack.experiment_id),
            "kind": TelegramNativeAttributionKind.CHANNEL_INVITE.value,
            "status": TelegramNativeAttributionStatus.PROVISIONING.value,
            "channel_username": channel_username,
            "source_url": pack.cta_value,
            "native_url": existing.native_url if existing is not None else "",
            "join_count": existing.join_count if existing is not None else 0,
            "attributed_join_count": (
                existing.attributed_join_count if existing is not None else 0
            ),
            "requested_count": existing.requested_count if existing is not None else 0,
            "last_delta_joins": 0,
            "analytics_pending": (
                existing.analytics_pending if existing is not None else False
            ),
            "last_event_id": (
                str(existing.last_event_id)
                if existing is not None and existing.last_event_id is not None
                else None
            ),
            "last_error": None,
            "created_at": (
                existing.created_at.isoformat() if existing is not None else now.isoformat()
            ),
            "updated_at": now.isoformat(),
            "last_synced_at": (
                existing.last_synced_at.isoformat()
                if existing is not None and existing.last_synced_at is not None
                else None
            ),
        }
        self._persist(base)

        try:
            invite = await self._publish.create_channel_invite_internal(
                project_id,
                channel_username,
                title=f"Partizan {pack.experiment_id.hex[:8]}",
            )
            bio = pack.bio.replace(pack.cta_value, invite.link, 1)
            if pack.cta_value not in pack.bio:
                raise TelegramNativeAttributionError(
                    "Profile pack bio no longer contains its reviewed CTA"
                )
            updated_pack = self._packs.update_draft(
                project_id,
                customer_token,
                pack.id,
                TelegramProfilePackUpdateRequest(
                    name=pack.name,
                    display_name=pack.display_name,
                    bio=bio,
                    cta_type=TelegramProfileCTAType.TELEGRAM_CHANNEL_INVITE,
                    cta_value=invite.link,
                    keep_existing_avatar=pack.avatar is not None,
                    story_enabled=False,
                ),
            )
            ready = {
                **base,
                "status": (
                    TelegramNativeAttributionStatus.REVOKED.value
                    if invite.revoked
                    else TelegramNativeAttributionStatus.READY.value
                ),
                "native_url": invite.link,
                "join_count": invite.usage,
                "requested_count": invite.requested,
                "updated_at": datetime.now(UTC).isoformat(),
                "profile_pack_fingerprint": updated_pack.fingerprint,
            }
            self._persist(ready)
            return self._view(ready)
        except (CustomerTelegramClientPublishError, ValueError, RuntimeError) as exc:
            failed = {
                **base,
                "status": TelegramNativeAttributionStatus.FAILED.value,
                "last_error": f"{type(exc).__name__}: {exc}"[:1000],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            self._persist(failed)
            if isinstance(exc, TelegramNativeAttributionError):
                raise
            raise TelegramNativeAttributionError(
                "Could not provision Telegram channel invite attribution"
            ) from exc

    async def sync(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramNativeAttributionView:
        current = self.get(project_id, customer_token, pack_id)
        if current.status not in {
            TelegramNativeAttributionStatus.READY,
            TelegramNativeAttributionStatus.REVOKED,
        }:
            raise TelegramNativeAttributionError(
                "Telegram native attribution is not ready to sync"
            )
        try:
            invite = await self._publish.channel_invite_internal(
                project_id,
                current.channel_username,
                current.native_url,
            )
        except CustomerTelegramClientPublishError as exc:
            raise TelegramNativeAttributionError(str(exc)) from exc

        now = datetime.now(UTC)
        join_count = max(current.join_count, invite.usage)
        delta = max(0, join_count - current.join_count)
        attributed_join_count = current.attributed_join_count
        last_event_id = current.last_event_id
        analytics_pending = join_count > attributed_join_count
        last_error = None

        if join_count > attributed_join_count:
            event_id = uuid5(
                NAMESPACE_URL,
                f"partizan:telegram-join:{current.id}:{join_count}",
            )
            try:
                self._analytics.ingest_event(
                    DistributionAnalyticsEventCreate(
                        event_id=event_id,
                        event_type="JOIN",
                        experiment_id=current.experiment_id,
                        action_id=current.action_id,
                        properties={
                            "count": join_count,
                            "delta": join_count - attributed_join_count,
                            "source": "telegram_channel_invite",
                            "profile_pack_id": str(current.pack_id),
                            "native_attribution_id": str(current.id),
                        },
                    )
                )
                attributed_join_count = join_count
                last_event_id = event_id
                analytics_pending = False
            except (KeyError, ValueError) as exc:
                last_error = f"Analytics pending: {exc}"[:1000]

        updated = {
            **self._payload(current.id),
            "status": (
                TelegramNativeAttributionStatus.REVOKED.value
                if invite.revoked
                else TelegramNativeAttributionStatus.READY.value
            ),
            "join_count": join_count,
            "attributed_join_count": attributed_join_count,
            "requested_count": max(current.requested_count, invite.requested),
            "last_delta_joins": delta,
            "analytics_pending": analytics_pending,
            "last_event_id": str(last_event_id) if last_event_id is not None else None,
            "last_error": last_error,
            "updated_at": now.isoformat(),
            "last_synced_at": now.isoformat(),
        }
        self._persist(updated)
        return self._view(updated)

    def get(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramNativeAttributionView:
        result = self._for_pack(project_id, customer_token, pack_id)
        if result is None:
            raise TelegramNativeAttributionError(
                "Telegram native attribution is not provisioned for this profile pack"
            )
        return result

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(TELEGRAM_NATIVE_ATTRIBUTION_NAMESPACE)

    def _for_pack(
        self,
        project_id: UUID,
        customer_token: str,
        pack_id: UUID,
    ) -> TelegramNativeAttributionView | None:
        pack = self._packs.get(project_id, customer_token, pack_id)
        matches: list[TelegramNativeAttributionView] = []
        for payload in self._store.list_namespace(TELEGRAM_NATIVE_ATTRIBUTION_NAMESPACE):
            if (
                str(payload.get("project_id") or "") == str(project_id)
                and str(payload.get("pack_id") or "") == str(pack.id)
            ):
                try:
                    matches.append(self._view(payload))
                except ValueError:
                    continue
        matches.sort(key=lambda item: (item.updated_at, str(item.id)), reverse=True)
        return matches[0] if matches else None

    def _public_channel_username(self, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            raise TelegramNativeAttributionError(
                "Telegram channel attribution requires an http(s) public link"
            )
        if (parts.hostname or "").lower() not in {
            "t.me",
            "www.t.me",
            "telegram.me",
            "www.telegram.me",
        }:
            raise TelegramNativeAttributionError(
                "Telegram channel attribution requires a Telegram public link"
            )
        path = parts.path.strip("/")
        if not path or "/" in path or path.startswith("+") or path.startswith("joinchat"):
            raise TelegramNativeAttributionError(
                "Telegram channel attribution requires a public @username link"
            )
        if parts.query or parts.fragment:
            raise TelegramNativeAttributionError(
                "Telegram channel attribution requires a plain public channel link"
            )
        return path

    def _payload(self, attribution_id: UUID) -> dict:
        payload = self._store.get(
            TELEGRAM_NATIVE_ATTRIBUTION_NAMESPACE,
            str(attribution_id),
        )
        if payload is None:
            raise TelegramNativeAttributionError("Telegram native attribution not found")
        return payload

    def _persist(self, payload: dict) -> None:
        self._store.put(
            TELEGRAM_NATIVE_ATTRIBUTION_NAMESPACE,
            str(payload["id"]),
            payload,
        )

    def _view(self, payload: dict) -> TelegramNativeAttributionView:
        return TelegramNativeAttributionView.model_validate(payload)


telegram_native_attribution_service = TelegramNativeAttributionService()
