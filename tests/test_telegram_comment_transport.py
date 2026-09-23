from app.distribution_types import DistributionActionType
from app.telegram_client_publishing import (
    TelegramPublishTarget,
    TelethonClientPublishTransport,
)


def test_telethon_transport_uses_comment_to_for_channel_comments() -> None:
    transport = TelethonClientPublishTransport()
    target = TelegramPublishTarget(
        username="relationship_channel",
        reply_to_message_id=321,
    )

    assert transport._send_message_thread_kwargs(
        DistributionActionType.COMMENT,
        target,
    ) == {"comment_to": 321}
    assert (
        transport._executed_url(DistributionActionType.COMMENT, target, 88)
        == "https://t.me/relationship_channel/321?comment=88"
    )


def test_telethon_transport_keeps_reply_to_for_group_replies() -> None:
    transport = TelethonClientPublishTransport()
    target = TelegramPublishTarget(
        username="relationship_group",
        reply_to_message_id=654,
    )

    assert transport._send_message_thread_kwargs(
        DistributionActionType.REPLY,
        target,
    ) == {"reply_to": 654}
    assert (
        transport._executed_url(DistributionActionType.REPLY, target, 99)
        == "https://t.me/relationship_group/99"
    )
