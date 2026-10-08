from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any

from telethon.errors import UserNotParticipantError
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    GetParticipantRequest,
    JoinChannelRequest,
)

_READY = "READY"
_JOIN_REQUIRED = "JOIN_REQUIRED"
_NO_WRITE_ACCESS = "NO_WRITE_ACCESS"
_NO_DISCUSSION = "NO_DISCUSSION"
_PRECHECK_FAILED = "PRECHECK_FAILED"
_JOIN_FAILED = "JOIN_FAILED"
_SAFE_RPC_MESSAGE = re.compile(r"^[A-Z0-9_]{2,120}$")


@dataclass(frozen=True)
class TelegramCommentAccess:
    status: str
    linked_chat_id: int | None = None
    joined: bool = False
    error_type: str | None = None
    rpc_code: int | None = None
    rpc_message: str | None = None
    _linked_entity: Any | None = None

    @property
    def ready(self) -> bool:
        return self.status == _READY

    def public_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "status": self.status,
            "linked_chat_id": self.linked_chat_id,
            "joined": self.joined,
        }
        if self.error_type:
            payload["error_type"] = self.error_type
        if self.rpc_code is not None:
            payload["rpc_code"] = self.rpc_code
        if self.rpc_message:
            payload["rpc_message"] = self.rpc_message
        return payload


def safe_telegram_rpc_error(exc: Exception) -> dict[str, object]:
    payload: dict[str, object] = {"error_type": type(exc).__name__}
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        payload["rpc_code"] = code
    message = getattr(exc, "message", None)
    if isinstance(message, str) and _SAFE_RPC_MESSAGE.fullmatch(message):
        payload["rpc_message"] = message
    return payload


def _error_access(status: str, exc: Exception, *, linked_chat_id: int | None = None) -> TelegramCommentAccess:
    safe = safe_telegram_rpc_error(exc)
    return TelegramCommentAccess(
        status=status,
        linked_chat_id=linked_chat_id,
        error_type=str(safe.get("error_type") or "TelegramError"),
        rpc_code=safe.get("rpc_code") if isinstance(safe.get("rpc_code"), int) else None,
        rpc_message=str(safe["rpc_message"]) if safe.get("rpc_message") else None,
    )


def _send_messages_blocked(entity: Any, participant: Any) -> bool:
    participant_name = type(participant).__name__
    if participant_name in {"ChannelParticipantCreator", "ChannelParticipantAdmin"}:
        return False
    participant_rights = getattr(participant, "banned_rights", None)
    if bool(getattr(participant_rights, "send_messages", False)):
        return True
    default_rights = getattr(entity, "default_banned_rights", None)
    return bool(getattr(default_rights, "send_messages", False))


async def preflight_comment_access(client: Any, channel: Any) -> TelegramCommentAccess:
    """Read-only check for linked-discussion membership and obvious write restrictions."""
    try:
        full = await client(GetFullChannelRequest(channel))
    except Exception as exc:
        return _error_access(_PRECHECK_FAILED, exc)

    linked_chat_id = int(getattr(getattr(full, "full_chat", None), "linked_chat_id", 0) or 0)
    if linked_chat_id <= 0:
        return TelegramCommentAccess(status=_NO_DISCUSSION)

    linked_entity = next(
        (
            chat
            for chat in (getattr(full, "chats", None) or [])
            if int(getattr(chat, "id", 0) or 0) == linked_chat_id
        ),
        None,
    )
    if linked_entity is None:
        try:
            linked_entity = await client.get_entity(linked_chat_id)
        except Exception as exc:
            return _error_access(_PRECHECK_FAILED, exc, linked_chat_id=linked_chat_id)

    try:
        participant_result = await client(GetParticipantRequest(linked_entity, "me"))
    except UserNotParticipantError:
        return TelegramCommentAccess(
            status=_JOIN_REQUIRED,
            linked_chat_id=linked_chat_id,
            _linked_entity=linked_entity,
        )
    except Exception as exc:
        return _error_access(_PRECHECK_FAILED, exc, linked_chat_id=linked_chat_id)

    participant = getattr(participant_result, "participant", participant_result)
    if _send_messages_blocked(linked_entity, participant):
        return TelegramCommentAccess(
            status=_NO_WRITE_ACCESS,
            linked_chat_id=linked_chat_id,
            _linked_entity=linked_entity,
        )
    return TelegramCommentAccess(
        status=_READY,
        linked_chat_id=linked_chat_id,
        _linked_entity=linked_entity,
    )


async def ensure_comment_access(
    client: Any,
    channel: Any,
    *,
    allow_join: bool,
) -> TelegramCommentAccess:
    """Preflight access and, only when explicitly allowed, join the linked discussion once."""
    access = await preflight_comment_access(client, channel)
    if access.status != _JOIN_REQUIRED or not allow_join:
        return access

    try:
        await client(JoinChannelRequest(access._linked_entity))
    except Exception as exc:
        return _error_access(_JOIN_FAILED, exc, linked_chat_id=access.linked_chat_id)

    rechecked = await preflight_comment_access(client, channel)
    return replace(rechecked, joined=True)
