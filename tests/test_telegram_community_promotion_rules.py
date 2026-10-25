"""Only explicit moderator-authored native descriptions grant promotional eligibility."""
from datetime import UTC, datetime

import pytest

from app.telegram_community_promotion_rules import public_community_promotion_rules
from app.telegram_research import TelegramCommunitySnapshot, TelegramSurfaceKind


def _snap(about):
    return TelegramCommunitySnapshot(
        entity_id=9999, username="community", title="Community",
        kind=TelegramSurfaceKind.GROUP, url="https://t.me/community",
        about=about, source_checked_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("description", [
    "Самореклама разрешена. Пожалуйста, придерживайтесь тематики.",
    "Реклама разрешена в группе по теме.",
    "Advertising allowed for community members.",
    "Self-promotion is permitted. Avoid spam.",
])
def test_explicit_public_community_description_can_confirm_promotion(description):
    rule = public_community_promotion_rules(_snap(description))
    assert rule["status"] == "VERIFIED_ALLOWED"
    assert rule["allows_promotion"] is True
    assert rule["source_url"] == "https://t.me/community"
    assert rule["source_type"] == "TELEGRAM_NATIVE_COMMUNITY_ABOUT"


@pytest.mark.parametrize("description", [
    "Общайтесь и знакомьтесь здесь.",
    "Реклама запрещена",
    "Без рекламы и спама.",
    "No ads or self-promotion",
    "Ads allowed only in a separate channel. Ads prohibited here.",
    "",
])
def test_absent_conflicting_or_negative_rules_never_allow_promotion(description):
    rule = public_community_promotion_rules(_snap(description))
    assert rule["status"] == "UNVERIFIED"
    assert rule["allows_promotion"] is False
