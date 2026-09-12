import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_workspace_loads_versioned_new_project_assets() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    css_match = re.search(
        r'(/workspace/assets/workspace\.projects\.v1\.css\?v=[a-f0-9]{12})',
        response.text,
    )
    js_match = re.search(
        r'(/workspace/assets/workspace\.projects\.v1\.js\?v=[a-f0-9]{12})',
        response.text,
    )
    assert css_match is not None
    assert js_match is not None
    assert css_match.group(1).split("?v=")[1] == js_match.group(1).split("?v=")[1]

    css = client.get(css_match.group(1))
    javascript = client.get(js_match.group(1))
    assert css.status_code == 200
    assert javascript.status_code == 200
    assert css.headers["cache-control"] == "no-store, max-age=0"
    assert javascript.headers["cache-control"] == "no-store, max-age=0"
    assert ".project-modal" in css.text
    assert ".project-details-card" in css.text
    assert ".project-danger-zone" in css.text
    assert ".distribution-channel-grid" in css.text
    assert ".distribution-channel-card.recommended" in css.text
    assert "+ New project" in javascript.text
    assert "Project details" in javascript.text
    assert "Description" in javascript.text
    assert "Delete project" in javascript.text
    assert "method: 'DELETE'" in javascript.text
    assert "'/customer/account/projects'" in javascript.text
    assert "activation-inline-primary" in javascript.text
    assert "new MutationObserver(syncRecommendedMoveCta)" in javascript.text
    assert "Understand your product" in javascript.text
    assert "Find where customers are" in javascript.text
    assert "Choose how you want to reach them" in javascript.text
    assert "Partizan recommends" in javascript.text
    assert "Setup appears only after you choose" in javascript.text
    assert "data-journey-channel-button" in javascript.text
    assert '#channel-snapshot [data-platform=' in javascript.text
    assert '.tab-button[data-tab="overview"]' in javascript.text
    assert '.tab-button[data-tab="activity"]' in javascript.text
    assert "channel-mode-select" not in javascript.text
    assert "customer_token" not in javascript.text


def test_distribution_choice_replaces_the_six_step_activation_ladder() -> None:
    js = Path("app/web/workspace.projects.v1.js").read_text()

    assert "progress.textContent = researchComplete ? '2 of 3' : '1 of 3';" in js
    assert "Research comes first. Now choose the channel you want to use." in js
    assert "money, an account or permission only after that choice" in js
    assert "No acquisition funding required now" in js
    assert "No account or acquisition budget needs to be connected" in js
    assert "Automatic execution is not available yet" in js
    assert "Partizan will not ask you to connect a random platform without evidence." in js
    assert "manualResearchPath" in js
    assert "!item.channel.autonomous_execution_available" in js
    assert "Open ${item.label} opportunity" in js
    assert "Connect ${item.label}" in js
    assert "focusOverviewChannelControl" in js
    assert ".channel-connect-button, .channel-toggle" in js
    assert "openChannelChoice" in js
    assert "channel-mode-select" not in js
    assert '.tab-button[data-tab="channels"]' not in js


def test_meta_connect_ui_fails_closed_when_customer_oauth_is_unavailable() -> None:
    js = Path("app/web/workspace.channels.v1.js").read_text()

    assert "!channel.autonomous_execution_available" in js
    assert "Meta customer connection is temporarily unavailable" in js
    assert "Meta activation pending" in js
    assert "meta.execution_blocker" in js
    assert "syncMetaSettingsControl(channels)" in js


def test_recommended_move_step_still_contains_real_research_evidence() -> None:
    html = Path("app/web/workspace.v1.html").read_text()
    js = Path("app/web/workspace.v1.js").read_text()

    assert 'id="activation-channel-title"' in html
    assert 'id="activation-channel-copy"' in html
    assert "previewOpportunity.recommended_action" in js
    assert "previewOpportunity.signal_to_watch" in js
    assert "data.preview_opportunity" in js
