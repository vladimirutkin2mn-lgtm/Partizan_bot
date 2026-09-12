from pathlib import Path

WORKSPACE_CHANNELS = Path("app/web/workspace.channels.v1.js")
RESULTS = Path("app/web/results.v1.js")


def test_customer_community_actions_require_explicit_review_and_publish() -> None:
    source = WORKSPACE_CHANNELS.read_text()

    assert "/community-actions" in source
    assert "data-review-community-action" in source
    assert "confirm_publish: true" in source
    assert "expected_target_url" in source
    assert "expected_content_text" in source
    assert "/actions/${encodeURIComponent(action.action_id)}/publish" in source
    assert "/actions/${encodeURIComponent(action.action_id)}/observe" in source
    assert "/policy/confirm" not in source
    assert "confirm_policy_resolution: true" not in source


def test_refresh_is_read_only_for_community_actions() -> None:
    source = WORKSPACE_CHANNELS.read_text()
    refresh_body = source.split(
        "const refreshCommunityActions = async () => {", 1
    )[1].split("const ensureCommunityActionModal", 1)[0]

    assert "/community-actions" in refresh_body
    assert "/publish" not in refresh_body
    assert "/observe" not in refresh_body


def test_results_show_community_execution_context() -> None:
    source = RESULTS.read_text()

    for marker in (
        "PUBLISHER_MODE_LABELS",
        "item.publisher_mode",
        "item.action.action_type",
        "item.replies",
        "item.removals",
        "Target / thread:",
    ):
        assert marker in source
