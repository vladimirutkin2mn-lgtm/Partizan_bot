from pathlib import Path

LEARNING_JS = Path("app/web/workspace.learning.v1.js").read_text(encoding="utf-8")


def _channel_choice_module() -> str:
    parts = LEARNING_JS.split("})();\n\n(() => {", maxsplit=1)
    assert len(parts) == 2
    return parts[1]


def test_channel_choice_is_presented_as_focus_not_execution_permission() -> None:
    js = _channel_choice_module()

    assert "Choose where Partizan should start." in js
    assert "without granting execution or spend permission" in js
    assert "This is a focus choice, not execution permission." in js
    assert "Choosing a channel does not allow execution, connect an account, or authorize spend." in js
    assert "Execution stays separate" in js


def test_research_can_recommend_but_only_customer_click_persists_selection() -> None:
    js = _channel_choice_module()

    assert "const inferredPlatform = (opportunity) =>" in js
    assert "Research lead" in js
    assert "data-channel-choice" in js
    assert "button.addEventListener('click', async () =>" in js
    assert "/channel-selection" in js
    assert "{ method: 'PUT', body: JSON.stringify({ platform }) }" in js
    assert js.count("method: 'PUT'") == 1
    assert "channelSnapshot = await requestJson(" in js


def test_off_channels_are_not_selectable_and_real_activity_ends_choice_flow() -> None:
    js = _channel_choice_module()

    assert "const disabled = channel.mode === 'OFF';" in js
    assert "Turn it back on before selecting it." in js
    assert "const measuredActivity = (channels)" in js
    assert "Number(channel.experiment_count || 0) > 0" in js
    assert "Number(channel.spend_usd || 0) > 0" in js
    assert "Number(channel.paid_customers || 0) > 0" in js
    assert "Number(channel.revenue_usd || 0) > 0" in js
    assert "if (!researchReady || measuredActivity(channelSnapshot))" in js


def test_channel_choice_follows_canonical_workspace_fanout_without_mutating_channel_controls() -> None:
    js = _channel_choice_module()

    assert "window.addEventListener('partizan:workspace-ready'" in js
    assert "loadChoice(true).catch(() => {})" in js
    assert "refreshQueued" in js
    assert "/channels" in js
    assert "method: 'POST'" not in js
    assert "method: 'DELETE'" not in js
    assert "/autopilot" not in js
    assert "confirm_autonomous_spend" not in js
