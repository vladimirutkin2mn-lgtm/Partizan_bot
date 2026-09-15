from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

import app.reddit_client_publishing as reddit_module
import app.telegram_client_publishing as telegram_module
from app.reddit_client_publishing import (
    CustomerRedditClientPublishError,
    CustomerRedditClientPublishService,
    RedditClientPublishOutcome,
    RedditClientPublishReceipt,
    RedditPublishRequest,
)
from app.telegram_client_publishing import (
    CustomerTelegramClientPublishError,
    CustomerTelegramClientPublishService,
    TelegramClientPublishOutcome,
    TelegramClientPublishReceipt,
    TelegramPublishRequest,
)


def _unbound_action():
    return SimpleNamespace(operational_metadata={})


@pytest.mark.asyncio
async def test_reddit_ambiguous_failed_publish_cannot_retry(monkeypatch) -> None:
    action_id = uuid4()
    service = CustomerRedditClientPublishService()
    receipt = RedditClientPublishReceipt(
        action_id=action_id,
        outcome=RedditClientPublishOutcome.FAILED,
        message="Remote result was not confirmed.",
        metadata={"error_code": "API_REQUEST_FAILED"},
        created_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        reddit_module._impl.distribution_execution_service,
        "get_action",
        lambda _action_id: _unbound_action(),
    )
    monkeypatch.setattr(service, "get_receipt", lambda _action_id: receipt)

    with pytest.raises(CustomerRedditClientPublishError, match="unknown.*reconcile"):
        await service.publish(
            uuid4(),
            "customer-token",
            action_id,
            RedditPublishRequest(confirm_publish=True, retry=True),
        )


@pytest.mark.asyncio
async def test_telegram_ambiguous_failed_publish_cannot_retry(monkeypatch) -> None:
    action_id = uuid4()
    service = CustomerTelegramClientPublishService()
    receipt = TelegramClientPublishReceipt(
        action_id=action_id,
        outcome=TelegramClientPublishOutcome.FAILED,
        message="Remote result was not confirmed.",
        metadata={"error_code": "PUBLISH_FAILED"},
        created_at=datetime.now(UTC),
    )
    monkeypatch.setattr(
        telegram_module._impl.distribution_execution_service,
        "get_action",
        lambda _action_id: _unbound_action(),
    )
    monkeypatch.setattr(service, "get_receipt", lambda _action_id: receipt)

    with pytest.raises(CustomerTelegramClientPublishError, match="unknown.*reconcile"):
        await service.publish(
            uuid4(),
            "customer-token",
            action_id,
            TelegramPublishRequest(retry=True),
        )


def test_explicit_provider_rejections_remain_retry_safe() -> None:
    reddit_service = CustomerRedditClientPublishService()
    reddit_receipt = RedditClientPublishReceipt(
        action_id=uuid4(),
        outcome=RedditClientPublishOutcome.FAILED,
        message="Provider rejected the request.",
        metadata={"error_code": "API_REJECTED"},
        created_at=datetime.now(UTC),
    )
    telegram_service = CustomerTelegramClientPublishService()
    telegram_receipt = TelegramClientPublishReceipt(
        action_id=uuid4(),
        outcome=TelegramClientPublishOutcome.FAILED,
        message="Provider rejected the request.",
        metadata={"error_code": "WRITE_FORBIDDEN"},
        created_at=datetime.now(UTC),
    )

    assert reddit_service._failed_publish_result_is_ambiguous(reddit_receipt) is False
    assert telegram_service._failed_publish_result_is_ambiguous(telegram_receipt) is False


def test_unexpected_provider_exceptions_are_ambiguous() -> None:
    reddit_service = CustomerRedditClientPublishService()
    reddit_receipt = RedditClientPublishReceipt(
        action_id=uuid4(),
        outcome=RedditClientPublishOutcome.FAILED,
        message="Remote result was not confirmed.",
        metadata={"provider_error_type": "ReadTimeout"},
        created_at=datetime.now(UTC),
    )
    telegram_service = CustomerTelegramClientPublishService()
    telegram_receipt = TelegramClientPublishReceipt(
        action_id=uuid4(),
        outcome=TelegramClientPublishOutcome.FAILED,
        message="Remote result was not confirmed.",
        metadata={"provider_error_type": "ConnectionResetError"},
        created_at=datetime.now(UTC),
    )

    assert reddit_service._failed_publish_result_is_ambiguous(reddit_receipt) is True
    assert telegram_service._failed_publish_result_is_ambiguous(telegram_receipt) is True
