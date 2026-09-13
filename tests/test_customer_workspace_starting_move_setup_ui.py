from pathlib import Path

LEARNING_JS = Path("app/web/workspace.learning.v1.js").read_text(encoding="utf-8")


def _channel_choice_module() -> str:
    parts = LEARNING_JS.split("})();\n\n(() => {", maxsplit=1)
    assert len(parts) == 2
    return parts[1]


def test_accepted_draft_loads_read_only_setup_plan() -> None:
    js = _channel_choice_module()

    assert "let startingMoveSetup = null;" in js
    assert "/starting-move/setup" in js
    assert "Accepted draft → setup" in js
    assert "Execution permission:</strong> Not granted by this plan." in js
    assert "Open Channels →" in js
    assert "READY_FOR_HANDOFF: 'Ready for handoff'" in js
    assert "NEEDS_SETUP: 'Setup needed'" in js
    assert "UNAVAILABLE: 'Automation unavailable'" in js


def test_setup_plan_is_fetched_after_acceptance_and_workspace_refresh() -> None:
    js = _channel_choice_module()

    assert "startingMoveSetup = await requestJson(" in js
    assert "[workspaceSnapshot, channelSnapshot, startingMove, startingMoveDraft, startingMoveSetup]" in js
    assert "starting-move/draft/accept" in js
    assert "starting-move/setup" in js
    assert "startingMoveSetup = null;" in js


def test_setup_plan_adds_no_execution_or_permission_mutations() -> None:
    js = _channel_choice_module()

    assert "/distribution-actions" not in js
    assert "/approve" not in js
    assert "/execute" not in js
    assert "/publish" not in js
    assert "confirm_autonomous_spend" not in js
    assert "starting-move/setup`,\n          { method:" not in js
    assert "starting-move/setup`, { method:" not in js
