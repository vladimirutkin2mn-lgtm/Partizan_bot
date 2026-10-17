from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import app.telegram_client_publishing_impl as publishing_impl
from app.distribution_types import DistributionActionType
from app.telegram_client_publishing_impl import (
    TelegramClientPublishTransportError,
    TelegramPublishTarget,
    TelethonClientPublishTransport,
)


class FakeChannel:
    def __init__(self):
        self.id = 42


class FakeTelegramClient:
    def __init__(self, *, latest_id: int | None = None, read_error: bool = False):
        self.latest_id = latest_id
        self.read_error = read_error
        self.read_count = 0
        self.send_count = 0
        self.connected = False

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return True

    async def get_entity(self, username):
        assert username == "@community"
        return FakeChannel()

    async def get_messages(self, entity, *, limit):
        assert isinstance(entity, FakeChannel)
        assert limit == 1
        self.read_count += 1
        if self.read_error:
            raise RuntimeError("read unavailable")
        if self.latest_id is None:
            return []
        return [SimpleNamespace(id=self.latest_id)]

    async def send_message(self, entity, text, **kwargs):
        assert isinstance(entity, FakeChannel)
        assert text == "prepared message"
        self.send_count += 1
        return SimpleNamespace(
            id=999,
            date=datetime.now(UTC),
            peer_id=SimpleNamespace(channel_id=42),
        )


def _transport(monkeypatch, client):
    monkeypatch.setattr(publishing_impl.telegram_types, "Channel", FakeChannel)
    transport = TelethonClientPublishTransport()
    monkeypatch.setattr(transport, "_client", lambda session: client)
    return transport


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_type",
    [DistributionActionType.COMMENT, DistributionActionType.REPLY],
)
async def test_publisher_refuses_stale_message_without_sending(monkeypatch, action_type):
    client = FakeTelegramClient(latest_id=148)
    transport = _transport(monkeypatch, client)
    with pytest.raises(
        TelegramClientPublishTransportError, match="TARGET_NO_LONGER_LATEST"
    ):
        await transport.publish(
            session="unused",
            target=TelegramPublishTarget("community", 147),
            action_type=action_type,
            text="prepared message",
        )
    assert client.read_count == 1
    assert client.send_count == 0
    assert client.connected is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action_type",
    [DistributionActionType.COMMENT, DistributionActionType.REPLY],
)
async def test_publisher_sends_only_when_draft_still_targets_latest(monkeypatch, action_type):
    client = FakeTelegramClient(latest_id=147)
    transport = _transport(monkeypatch, client)
    receipt = await transport.publish(
        session="unused",
        target=TelegramPublishTarget("community", 147),
        action_type=action_type,
        text="prepared message",
    )
    assert receipt.message_id == 999
    assert client.read_count == 1
    assert client.send_count == 1
    assert client.connected is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("latest_id", "read_error", "expected_error"),
    [
        (None, False, "LATEST_MESSAGE_UNKNOWN"),
        (147, True, "LATEST_MESSAGE_CHECK_FAILED"),
    ],
)
async def test_publisher_fails_closed_if_latest_cannot_be_verified(
    monkeypatch, latest_id, read_error, expected_error
):
    client = FakeTelegramClient(latest_id=latest_id, read_error=read_error)
    transport = _transport(monkeypatch, client)
    with pytest.raises(TelegramClientPublishTransportError, match=expected_error):
        await transport.publish(
            session="unused",
            target=TelegramPublishTarget("community", 147),
            action_type=DistributionActionType.COMMENT,
            text="prepared message",
        )
    assert client.send_count == 0


@pytest.mark.asyncio
async def test_standalone_group_post_never_requires_a_latest_message(monkeypatch):
    client = FakeTelegramClient(read_error=True)
    transport = _transport(monkeypatch, client)
    receipt = await transport.publish(
        session="unused",
        target=TelegramPublishTarget("community", None),
        action_type=DistributionActionType.STANDALONE_POST,
        text="prepared message",
    )
    assert receipt.message_id == 999
    assert client.read_count == 0
    assert client.send_count == 1
