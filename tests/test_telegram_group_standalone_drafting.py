"""A group message needs community context, not the last stranger's post."""

from types import SimpleNamespace

import pytest

from app.action_drafting import ActionTargetSelector
from app.distribution_types import DistributionActionType, DistributionPlatform


def _opportunity(*, confirmed=True, mode="STANDALONE_POST"):
    return SimpleNamespace(
        title="A group to discuss FemDom",
        url="https://t.me/example_group",
        platform=DistributionPlatform.TELEGRAM,
        metadata={
            "native_research_status": "VERIFIED",
            "community_fit_pass": confirmed,
            "about": "Обсуждение событий и знакомства в нашем сообществе",
            "action_target_mode": mode,
            "action_target_url": "https://t.me/example_group",
            "surface_capabilities": {"standalone_post": "AVAILABLE"},
            "recent_context": [{
                "message_id": 123,
                "text": "Личное сообщение последнего участника, не повод отвечать",
                "url": "https://t.me/example_group/123",
            }],
        },
    )


def test_telegram_group_standalone_context_is_not_latest_member_message():
    selected = ActionTargetSelector().select(
        SimpleNamespace(action_type=DistributionActionType.STANDALONE_POST),
        _opportunity(),
    )
    assert selected.url == "https://t.me/example_group"
    assert selected.source == "telegram_native.group_description"
    assert "Обсуждение событий" in selected.context_text
    assert "Личное сообщение" not in selected.context_text
    assert "example_group/123" not in selected.context_text


@pytest.mark.parametrize(
    ("confirmed", "mode"),
    [(False, "STANDALONE_POST"), (True, "GROUP_REPLY")],
)
def test_no_group_standalone_without_verified_audience_or_mode(confirmed, mode):
    with pytest.raises(ValueError, match="verified audience"):
        ActionTargetSelector().select(
            SimpleNamespace(action_type=DistributionActionType.STANDALONE_POST),
            _opportunity(confirmed=confirmed, mode=mode),
        )


def test_no_standalone_from_stale_message_link_as_group_target():
    opportunity = _opportunity()
    opportunity.metadata["action_target_url"] = "https://t.me/example_group/123"
    with pytest.raises(ValueError, match="exact group URL"):
        ActionTargetSelector().select(
            SimpleNamespace(action_type=DistributionActionType.STANDALONE_POST),
            opportunity,
        )
