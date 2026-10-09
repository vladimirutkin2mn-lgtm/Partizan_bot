from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest
from telethon.errors import UserNotParticipantError
from telethon.tl.functions.channels import (
    GetFullChannelRequest,
    GetParticipantRequest,
    JoinChannelRequest,
)

from app import telegram_client_publishing_impl
from app.telegram_comment_access import ensure_comment_access, preflight_comment_access


class FakeClient:
    def __init__(
        self,
        *,
        member: bool,
        send_blocked: bool = False,
        join_error: Exception | None = None,
        participant_error: Exception | None = None,
    ):
        self.member = member
        self.send_blocked = send_blocked
        self.join_error = join_error
        self.participant_error = participant_error
        self.join_calls = 0
        self.participant_checks = 0
        self.linked = SimpleNamespace(
            id=222,
            default_banned_rights=SimpleNamespace(send_messages=send_blocked),
        )

    async def __call__(self, request):
        if isinstance(request, GetFullChannelRequest):
            return SimpleNamespace(
                full_chat=SimpleNamespace(linked_chat_id=222),
                chats=[self.linked],
            )
        if isinstance(request, GetParticipantRequest):
            self.participant_checks += 1
            if self.participant_error is not None:
                raise self.participant_error
            if not self.member:
                raise UserNotParticipantError(request)
            return SimpleNamespace(
                participant=SimpleNamespace(banned_rights=None),
            )
        if isinstance(request, JoinChannelRequest):
            self.join_calls += 1
            if self.join_error is not None:
                raise self.join_error
            self.member = True
            return SimpleNamespace()
        raise AssertionError(f"unexpected request: {type(request).__name__}")

    async def get_entity(self, entity_id):
        assert entity_id == 222
        return self.linked


@pytest.mark.asyncio
async def test_preflight_ready_does_not_join():
    client = FakeClient(member=True)

    access = await ensure_comment_access(client, SimpleNamespace(id=111), allow_join=True)

    assert access.status == "READY"
    assert access.linked_chat_id == 222
    assert access.joined is False
    assert client.join_calls == 0


@pytest.mark.asyncio
async def test_join_required_is_joined_once_and_rechecked():
    client = FakeClient(member=False)

    access = await ensure_comment_access(client, SimpleNamespace(id=111), allow_join=True)

    assert access.status == "READY"
    assert access.joined is True
    assert client.join_calls == 1
    assert client.participant_checks == 2


@pytest.mark.asyncio
async def test_join_required_without_authorization_has_no_side_effect():
    client = FakeClient(member=False)

    access = await ensure_comment_access(client, SimpleNamespace(id=111), allow_join=False)

    assert access.status == "JOIN_REQUIRED"
    assert access.joined is False
    assert client.join_calls == 0


@pytest.mark.asyncio
async def test_join_failure_blocks_publish_path():
    client = FakeClient(member=False, join_error=RuntimeError("private details must not leak"))

    access = await ensure_comment_access(client, SimpleNamespace(id=111), allow_join=True)

    assert access.status == "JOIN_FAILED"
    assert access.error_type == "RuntimeError"
    assert access.rpc_message is None
    assert client.join_calls == 1


@pytest.mark.asyncio
async def test_default_send_ban_is_no_write_access():
    client = FakeClient(member=True, send_blocked=True)

    access = await preflight_comment_access(client, SimpleNamespace(id=111))

    assert access.status == "NO_WRITE_ACCESS"


@pytest.mark.asyncio
async def test_telegram_account_ban_error_is_no_write_access():
    banned_error_type = type("UserBannedInChannelError", (Exception,), {})
    client = FakeClient(member=True, participant_error=banned_error_type("blocked"))

    access = await preflight_comment_access(client, SimpleNamespace(id=111))

    assert access.status == "NO_WRITE_ACCESS"
    assert access.error_type == "UserBannedInChannelError"


def test_generic_customer_transport_never_joins_groups_implicitly():
    source = inspect.getsource(telegram_client_publishing_impl)
    assert "JoinChannelRequest" not in source
