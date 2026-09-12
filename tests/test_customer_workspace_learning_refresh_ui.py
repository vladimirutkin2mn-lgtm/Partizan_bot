from pathlib import Path

WORKSPACE_HTML = Path("app/web/workspace.v1.html").read_text(encoding="utf-8")
WORKSPACE_JS = Path("app/web/workspace.v1.js").read_text(encoding="utf-8")


def test_customer_workspace_canonical_renderer_owns_learning_history() -> None:
    activity_block = WORKSPACE_JS.split("const renderActivity = (overview) =>", 1)[1]
    activity_block = activity_block.split("const channelModeLabel =", 1)[0]

    assert "overview.running_experiments" in activity_block
    assert "overview.waiting_experiments" in activity_block
    assert "overview.recent_decisions" in activity_block
    assert "$('experiments').innerHTML" in activity_block
    assert "$('decisions').innerHTML" in activity_block
    assert "renderActivity(overview);" in WORKSPACE_JS


def test_learning_history_does_not_fetch_a_second_workspace_snapshot() -> None:
    assert "const refreshLearning = async" not in WORKSPACE_HTML
    assert "/customer/workspace/${encodeURIComponent(projectId)}" not in WORKSPACE_HTML
    assert "const renderLearning = (data) =>" not in WORKSPACE_HTML
