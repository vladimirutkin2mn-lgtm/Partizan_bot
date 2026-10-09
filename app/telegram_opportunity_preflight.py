from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import get_settings
from app.telegram_client_publishing import customer_telegram_client_publish_service
from app.telegram_comment_access import preflight_comment_access
from app.telegram_community_restrictions import telegram_community_restriction_memory


@dataclass(frozen=True, slots=True)
class TelegramCommentTarget:
    handle: str
    post_id: int


@dataclass(frozen=True, slots=True)
class TelegramOpportunityPreflightResult:
    status: str
    handle: str | None = None
    post_id: int | None = None
    linked_chat_id: int | None = None
    reason: str | None = None

    @property
    def execution_ready(self) -> bool:
        return self.status == "READY"

    @property
    def recoverable(self) -> bool:
        return self.status in {"READY", "JOIN_REQUIRED"}

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"status": self.status}
        if self.handle is not None:
            payload["handle"] = self.handle
        if self.post_id is not None:
            payload["post_id"] = self.post_id
        if self.linked_chat_id is not None:
            payload["linked_chat_id"] = self.linked_chat_id
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


AccessChecker = Callable[[Any, Any], Awaitable[Any]]
SessionProvider = Callable[[UUID], str]
ClientFactory = Callable[[str], Any]
RestrictionChecker = Callable[[UUID, str], dict | None]


class TelegramOpportunityPreflightService:
    """Read-only publisher preflight for an exact Telegram channel post.

    The service never joins a linked group and never sends a message. A target is ready
    for autonomous COMMENT execution only when the exact post exists, has a discussion,
    the connected customer account already has write access to the linked discussion,
    and Partizan has no stronger prior evidence that Telegram rejected writes for this
    account/community pair.
    """

    def __init__(
        self,
        *,
        session_provider: SessionProvider | None = None,
        client_factory: ClientFactory | None = None,
        access_checker: AccessChecker | None = None,
        restriction_checker: RestrictionChecker | None = None,
    ) -> None:
        self._session_provider = (
            session_provider or customer_telegram_client_publish_service._active_session_internal
        )
        self._client_factory = client_factory or self._default_client
        self._access_checker = access_checker or preflight_comment_access
        self._restriction_checker = (
            restriction_checker or telegram_community_restriction_memory.get
        )

    async def check_comment_target(
        self,
        project_id: UUID,
        target_url: str,
    ) -> TelegramOpportunityPreflightResult:
        target = self.parse_comment_target(target_url)
        if target is None:
            return TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                reason="INVALID_TARGET_URL",
            )

        known_restriction = self._restriction_checker(project_id, target.handle)
        if known_restriction is not None:
            return TelegramOpportunityPreflightResult(
                status="NO_WRITE_ACCESS",
                handle=target.handle,
                post_id=target.post_id,
                reason=str(
                    known_restriction.get("reason")
                    or "KNOWN_COMMUNITY_WRITE_RESTRICTION"
                ),
            )

        try:
            session = self._session_provider(project_id)
            client = self._client_factory(session)
        except Exception as exc:
            return TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                handle=target.handle,
                post_id=target.post_id,
                reason=type(exc).__name__,
            )

        try:
            await client.connect()
            if not await client.is_user_authorized():
                return TelegramOpportunityPreflightResult(
                    status="PRECHECK_FAILED",
                    handle=target.handle,
                    post_id=target.post_id,
                    reason="SESSION_NOT_AUTHORIZED",
                )

            entity = await client.get_entity(f"@{target.handle}")
            message = await client.get_messages(entity, ids=target.post_id)
            if message is None or int(getattr(message, "id", 0) or 0) != target.post_id:
                return TelegramOpportunityPreflightResult(
                    status="PRECHECK_FAILED",
                    handle=target.handle,
                    post_id=target.post_id,
                    reason="TARGET_NOT_FOUND",
                )
            if getattr(message, "replies", None) is None:
                return TelegramOpportunityPreflightResult(
                    status="NO_DISCUSSION",
                    handle=target.handle,
                    post_id=target.post_id,
                    reason="TARGET_HAS_NO_DISCUSSION",
                )

            access = await self._access_checker(client, entity)
            return TelegramOpportunityPreflightResult(
                status=str(getattr(access, "status", "PRECHECK_FAILED")),
                handle=target.handle,
                post_id=target.post_id,
                linked_chat_id=(
                    int(getattr(access, "linked_chat_id", 0) or 0) or None
                ),
                reason=(
                    str(getattr(access, "error_type", "") or "") or None
                ),
            )
        except Exception as exc:
            return TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                handle=target.handle,
                post_id=target.post_id,
                reason=type(exc).__name__,
            )
        finally:
            try:
                await client.disconnect()
            except Exception:
                pass

    @staticmethod
    def parse_comment_target(target_url: str) -> TelegramCommentTarget | None:
        parts = urlsplit(str(target_url or "").strip())
        host = parts.netloc.lower().removeprefix("www.")
        if host not in {"t.me", "telegram.me"}:
            return None
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(segments) < 2:
            return None
        if segments[0] == "s" and len(segments) >= 3:
            segments = segments[1:]
        handle = segments[0].strip().lstrip("@")
        if not handle or handle.startswith("+"):
            return None
        try:
            post_id = int(segments[1])
        except (TypeError, ValueError):
            return None
        if post_id <= 0:
            return None
        return TelegramCommentTarget(handle=handle, post_id=post_id)

    @staticmethod
    def _default_client(session: str) -> TelegramClient:
        settings = get_settings()
        api_id = settings.telegram_client_publish_api_id
        api_hash = settings.telegram_client_publish_api_hash
        if api_id is None or api_hash is None:
            raise RuntimeError("Telegram client publishing credentials are not configured")
        return TelegramClient(
            StringSession(session),
            api_id,
            api_hash.get_secret_value(),
        )


telegram_opportunity_preflight_service = TelegramOpportunityPreflightService()
