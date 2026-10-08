from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.telegram_opportunity_preflight import TelegramOpportunityPreflightService


class FakeClient:
    def __init__(self, *, message, authorized: bool = True) -> None:
        self.message = message
        self.authorized = authorized
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.connected = False

    async def is_user_authorized(self) -> bool:
        return self.authorized

    async def get_entity(self, handle: str):
        return SimpleNamespace(id=123, username=handle.lstrip("@"))

    async def get_messages(self, entity, *, ids: int):
        del entity, ids
        return self.message


@pytest.mark.asyncio
async def test_preflight_rejects_exact_post_without_discussion() -> None:
    client = FakeClient(message=SimpleNamespace(id=794, replies=None))
    service = TelegramOpportunityPreflightService(
        session_provider=lambda project_id: "session",
        client_factory=lambda session: client,
    )

    result = await service.check_comment_target(
        uuid4(),
        "https://t.me/Bizarre_afisha/794",
    )

    assert result.status == "NO_DISCUSSION"
    assert result.reason == "TARGET_HAS_NO_DISCUSSION"
    assert result.execution_ready is False
    assert client.connected is False


@pytest.mark.asyncio
async def test_preflight_keeps_join_required_visible_but_not_execution_ready() -> None:
    client = FakeClient(message=SimpleNamespace(id=523, replies=SimpleNamespace(replies=4)))

    async def access_checker(client_arg, entity):
        del client_arg, entity
        return SimpleNamespace(
            status="JOIN_REQUIRED",
            linked_chat_id=555,
            error_type=None,
        )

    service = TelegramOpportunityPreflightService(
        session_provider=lambda project_id: "session",
        client_factory=lambda session: client,
        access_checker=access_checker,
    )

    result = await service.check_comment_target(
        uuid4(),
        "https://t.me/freepeopleschedule/523",
    )

    assert result.status == "JOIN_REQUIRED"
    assert result.linked_chat_id == 555
    assert result.recoverable is True
    assert result.execution_ready is False


def test_parse_requires_exact_message_target() -> None:
    service = TelegramOpportunityPreflightService(
        session_provider=lambda project_id: "session",
        client_factory=lambda session: None,
    )

    assert service.parse_comment_target("https://t.me/Bizarre_afisha/794") is not None
    assert service.parse_comment_target("https://t.me/Bizarre_afisha") is None
    assert service.parse_comment_target("https://example.com/Bizarre_afisha/794") is None
