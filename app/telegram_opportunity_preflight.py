from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.functions.channels import GetParticipantRequest

from app.config import get_settings
from app.telegram_client_publishing import customer_telegram_client_publish_service
from app.telegram_comment_access import preflight_comment_access
from app.telegram_community_restrictions import telegram_community_restriction_memory
from app.telegram_participation_target import TelegramParticipationMode


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
    action_target_mode: str | None = None

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
        if self.action_target_mode is not None:
            payload["action_target_mode"] = self.action_target_mode
        return payload


AccessChecker = Callable[[Any, Any], Awaitable[Any]]
SessionProvider = Callable[[UUID], str]
ClientFactory = Callable[[str], Any]
RestrictionChecker = Callable[[UUID, str], dict | None]


class TelegramOpportunityPreflightService:
    """Read-only publisher preflight for explicit Telegram participation actions.

    This service never joins or sends. CHANNEL_COMMENT preserves the linked-discussion
    membership check. GROUP_REPLY verifies the exact message and current group access.
    STANDALONE_POST verifies the public group and current account write access without
    requiring an artificial recent-message target.
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

    async def check_target(
        self,
        project_id: UUID,
        target_url: str,
        action_target_mode: str,
    ) -> TelegramOpportunityPreflightResult:
        mode = str(action_target_mode or "").upper()
        if mode == TelegramParticipationMode.CHANNEL_COMMENT.value:
            return await self.check_comment_target(project_id, target_url)
        if mode in {
            TelegramParticipationMode.GROUP_REPLY.value,
            TelegramParticipationMode.STANDALONE_POST.value,
        }:
            return await self._check_group_target(project_id, target_url, mode)
        return TelegramOpportunityPreflightResult(
            status="PRECHECK_FAILED",
            reason="UNSUPPORTED_ACTION_TARGET_MODE",
            action_target_mode=mode or None,
        )

    async def check_comment_target(
        self,
        project_id: UUID,
        target_url: str,
    ) -> TelegramOpportunityPreflightResult:
        mode = TelegramParticipationMode.CHANNEL_COMMENT.value
        target = self.parse_comment_target(target_url)
        if target is None:
            return TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                reason="INVALID_TARGET_URL",
                action_target_mode=mode,
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
                action_target_mode=mode,
            )

        client, error = self._client_for_project(project_id, target.handle, target.post_id, mode)
        if error is not None:
            return error
        assert client is not None

        try:
            await client.connect()
            if not await client.is_user_authorized():
                return TelegramOpportunityPreflightResult(
                    status="PRECHECK_FAILED",
                    handle=target.handle,
                    post_id=target.post_id,
                    reason="SESSION_NOT_AUTHORIZED",
                    action_target_mode=mode,
                )

            entity = await client.get_entity(f"@{target.handle}")
            message = await client.get_messages(entity, ids=target.post_id)
            if message is None or int(getattr(message, "id", 0) or 0) != target.post_id:
                return TelegramOpportunityPreflightResult(
                    status="PRECHECK_FAILED",
                    handle=target.handle,
                    post_id=target.post_id,
                    reason="TARGET_NOT_FOUND",
                    action_target_mode=mode,
                )
            if getattr(message, "replies", None) is None:
                return TelegramOpportunityPreflightResult(
                    status="NO_DISCUSSION",
                    handle=target.handle,
                    post_id=target.post_id,
                    reason="TARGET_HAS_NO_DISCUSSION",
                    action_target_mode=mode,
                )

            access = await self._access_checker(client, entity)
            return TelegramOpportunityPreflightResult(
                status=str(getattr(access, "status", "PRECHECK_FAILED")),
                handle=target.handle,
                post_id=target.post_id,
                linked_chat_id=(
                    int(getattr(access, "linked_chat_id", 0) or 0) or None
                ),
                reason=(str(getattr(access, "error_type", "") or "") or None),
                action_target_mode=mode,
            )
        except Exception as exc:
            return TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                handle=target.handle,
                post_id=target.post_id,
                reason=type(exc).__name__,
                action_target_mode=mode,
            )
        finally:
            await self._disconnect_safely(client)

    async def _check_group_target(
        self,
        project_id: UUID,
        target_url: str,
        mode: str,
    ) -> TelegramOpportunityPreflightResult:
        require_message = mode == TelegramParticipationMode.GROUP_REPLY.value
        parsed = self._parse_public_target(target_url, require_message=require_message)
        if parsed is None:
            return TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                reason="INVALID_TARGET_URL",
                action_target_mode=mode,
            )
        handle, post_id = parsed

        known_restriction = self._restriction_checker(project_id, handle)
        if known_restriction is not None:
            return TelegramOpportunityPreflightResult(
                status="NO_WRITE_ACCESS",
                handle=handle,
                post_id=post_id,
                reason=str(
                    known_restriction.get("reason")
                    or "KNOWN_COMMUNITY_WRITE_RESTRICTION"
                ),
                action_target_mode=mode,
            )

        client, error = self._client_for_project(project_id, handle, post_id, mode)
        if error is not None:
            return error
        assert client is not None

        try:
            await client.connect()
            if not await client.is_user_authorized():
                return TelegramOpportunityPreflightResult(
                    status="PRECHECK_FAILED",
                    handle=handle,
                    post_id=post_id,
                    reason="SESSION_NOT_AUTHORIZED",
                    action_target_mode=mode,
                )
            entity = await client.get_entity(f"@{handle}")
            if not bool(getattr(entity, "megagroup", False)):
                return TelegramOpportunityPreflightResult(
                    status="PRECHECK_FAILED",
                    handle=handle,
                    post_id=post_id,
                    reason="TARGET_IS_NOT_GROUP",
                    action_target_mode=mode,
                )

            if require_message:
                message = await client.get_messages(entity, ids=post_id)
                if message is None or int(getattr(message, "id", 0) or 0) != post_id:
                    return TelegramOpportunityPreflightResult(
                        status="PRECHECK_FAILED",
                        handle=handle,
                        post_id=post_id,
                        reason="TARGET_NOT_FOUND",
                        action_target_mode=mode,
                    )

            status, reason = await self._group_write_status(client, entity)
            return TelegramOpportunityPreflightResult(
                status=status,
                handle=handle,
                post_id=post_id,
                reason=reason,
                action_target_mode=mode,
            )
        except Exception as exc:
            return TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                handle=handle,
                post_id=post_id,
                reason=type(exc).__name__,
                action_target_mode=mode,
            )
        finally:
            await self._disconnect_safely(client)

    async def _group_write_status(self, client: Any, entity: Any) -> tuple[str, str | None]:
        try:
            participant_result = await client(GetParticipantRequest(entity, "me"))
        except Exception as exc:
            error_type = type(exc).__name__
            if error_type == "UserNotParticipantError":
                return "JOIN_REQUIRED", "USER_NOT_PARTICIPANT"
            if error_type in {"ChatWriteForbiddenError", "UserBannedInChannelError"}:
                return "NO_WRITE_ACCESS", error_type
            return "PRECHECK_FAILED", error_type

        participant = getattr(participant_result, "participant", participant_result)
        participant_name = type(participant).__name__
        if participant_name not in {"ChannelParticipantCreator", "ChannelParticipantAdmin"}:
            participant_rights = getattr(participant, "banned_rights", None)
            if bool(getattr(participant_rights, "send_messages", False)):
                return "NO_WRITE_ACCESS", "PARTICIPANT_SEND_MESSAGES_BLOCKED"
            default_rights = getattr(entity, "default_banned_rights", None)
            if bool(getattr(default_rights, "send_messages", False)):
                return "NO_WRITE_ACCESS", "DEFAULT_SEND_MESSAGES_BLOCKED"
        return "READY", None

    def _client_for_project(
        self,
        project_id: UUID,
        handle: str,
        post_id: int | None,
        mode: str,
    ) -> tuple[Any | None, TelegramOpportunityPreflightResult | None]:
        try:
            session = self._session_provider(project_id)
            client = self._client_factory(session)
        except Exception as exc:
            return None, TelegramOpportunityPreflightResult(
                status="PRECHECK_FAILED",
                handle=handle,
                post_id=post_id,
                reason=type(exc).__name__,
                action_target_mode=mode,
            )
        return client, None

    @staticmethod
    async def _disconnect_safely(client: Any) -> None:
        try:
            await client.disconnect()
        except Exception:
            pass

    @staticmethod
    def parse_comment_target(target_url: str) -> TelegramCommentTarget | None:
        parsed = TelegramOpportunityPreflightService._parse_public_target(
            target_url,
            require_message=True,
        )
        if parsed is None:
            return None
        handle, post_id = parsed
        if post_id is None:
            return None
        return TelegramCommentTarget(handle=handle, post_id=post_id)

    @staticmethod
    def _parse_public_target(
        target_url: str,
        *,
        require_message: bool,
    ) -> tuple[str, int | None] | None:
        parts = urlsplit(str(target_url or "").strip())
        host = parts.netloc.lower().removeprefix("www.")
        if host not in {"t.me", "telegram.me"}:
            return None
        segments = [segment for segment in parts.path.split("/") if segment]
        if segments and segments[0] == "s":
            segments = segments[1:]
        if not segments:
            return None
        handle = segments[0].strip().lstrip("@")
        if not handle or handle.startswith("+") or handle.casefold() == "joinchat":
            return None
        if require_message:
            if len(segments) != 2:
                return None
            try:
                post_id = int(segments[1])
            except (TypeError, ValueError):
                return None
            if post_id <= 0:
                return None
            return handle, post_id
        if len(segments) != 1:
            return None
        return handle, None

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
