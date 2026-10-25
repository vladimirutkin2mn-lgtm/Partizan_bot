"""No-write regression tests for historical ban versus fresh Telegram rights."""
from __future__ import annotations

from uuid import uuid4

import pytest

from app.femdom_asfera_live_access_recheck import audit
from app.femdom_research_comment_preview import _PRODUCT, _PROJECT
from app.telegram_opportunity_preflight import TelegramOpportunityPreflightResult


class Checker:
    def __init__(self, status: str, reason: str | None = None):
        self.result = TelegramOpportunityPreflightResult(
            status=status, reason=reason, handle="a_sfera", post_id=2117,
        )
        self.calls = []

    async def check_comment_target(self, project_id, url):
        self.calls.append((project_id, url))
        return self.result


@pytest.mark.asyncio
async def test_fresh_ready_does_not_override_historical_ban_or_allow_send():
    cached = Checker("NO_WRITE_ACCESS", "ACCOUNT_BANNED_IN_COMMUNITY")
    native = Checker("READY")
    roots = []

    async def verify(project_id, url, checker):
        roots.append((project_id, url, checker))
        return {"status": "DISCUSSION_ROOT_VERIFIED"}

    result = await audit(
        restriction_reader=lambda *_: {
            "reason": "ACCOUNT_BANNED_IN_COMMUNITY",
            "source_action_id": "old-failed-send",
            "secret_reference": "NEVER_LOG",
        },
        standard_checker=cached, native_checker=native,
        discussion_checker=verify,
    )
    assert result["status"] == "ASFERA_READONLY_RECHECK_COMPLETE"
    assert result["historical_ban_conflicts_with_fresh_read"] is True
    assert result["fresh_route_verified"] is True
    assert result["stored_restriction"]["source_action_id"] == "old-failed-send"
    assert "NEVER_LOG" not in str(result)
    assert result["send_allowed"] is False
    assert result["telegram_sends"] == 0
    assert result["telegram_joins"] == 0
    assert result["restriction_memory_cleared"] is False
    assert result["publisher_authorization_changed"] is False
    assert result["publication_authorized"] is False
    assert len(roots) == 1
    assert roots[0][2] is native


@pytest.mark.asyncio
async def test_fresh_no_write_does_not_derive_rights_from_visible_composer():
    cached = Checker("NO_WRITE_ACCESS", "ACCOUNT_BANNED_IN_COMMUNITY")
    native = Checker("NO_WRITE_ACCESS", "UserBannedInChannelError")

    async def should_not_be_called(*_):
        raise AssertionError("No discussion check on denied native access")

    result = await audit(
        restriction_reader=lambda *_: {"reason": "ACCOUNT_BANNED_IN_COMMUNITY"},
        standard_checker=cached, native_checker=native,
        discussion_checker=should_not_be_called,
    )
    assert result["historical_ban_conflicts_with_fresh_read"] is False
    assert result["fresh_route_verified"] is False
    assert result["fresh_native_preflight"]["reason"] == "UserBannedInChannelError"
    assert result["send_allowed"] is False


@pytest.mark.asyncio
async def test_join_required_stays_readonly_and_blocked():
    result = await audit(
        restriction_reader=lambda *_: None,
        standard_checker=Checker("JOIN_REQUIRED"),
        native_checker=Checker("JOIN_REQUIRED"),
    )
    assert result["stored_restriction"]["present"] is False
    assert result["fresh_native_preflight"]["status"] == "JOIN_REQUIRED"
    assert result["fresh_route_verified"] is False
    assert result["telegram_joins"] == 0


@pytest.mark.asyncio
async def test_wrong_product_project_refused_before_provider_access():
    with pytest.raises(ValueError, match="Outside approved"):
        await audit(product_id=uuid4())
    with pytest.raises(ValueError, match="Outside approved"):
        await audit(project_id=uuid4())
    assert _PROJECT != _PRODUCT
