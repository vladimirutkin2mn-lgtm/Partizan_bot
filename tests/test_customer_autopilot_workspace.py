from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
START_JS = ROOT / "app" / "web" / "start.v2.js"
WORKSPACE_JS = ROOT / "app" / "web" / "workspace.v1.js"

client = TestClient(app)


def test_autonomous_execution_controls_live_in_customer_workspace_not_start() -> None:
    start = client.get("/start")
    workspace = client.get("/workspace")

    assert start.status_code == 200
    assert "/start/assets/start.v2.css" in start.text
    assert "/start/assets/customer-account.v1.css" in start.text
    assert "/start/assets/start.autopilot.v1.css" not in start.text
    assert 'id="autonomous-button"' in start.text
    assert 'id="account-gate"' in start.text
    assert 'id="register-form"' in start.text
    assert 'id="login-form"' in start.text
    assert 'href="/workspace"' in start.text
    assert 'id="growth-balance-form"' not in start.text
    assert 'id="autopilot-config-form"' not in start.text
    assert 'id="meta-connect-button"' not in start.text
    assert 'id="autopilot-dashboard"' not in start.text

    assert workspace.status_code == 200
    assert "/workspace/assets/workspace.v1.css" in workspace.text
    assert 'id="workspace-login-form"' in workspace.text
    assert 'data-tab="overview"' in workspace.text
    assert 'data-tab="channels"' in workspace.text
    assert 'data-tab="activity"' in workspace.text
    assert 'data-tab="settings"' in workspace.text
    assert 'id="channels-table-body"' in workspace.text
    assert "Partizan recommends. You control the boundaries." in workspace.text
    assert "Research only" in workspace.text
    assert "Allow execution" in workspace.text
    assert "do not use" in workspace.text
    assert 'id="fund-form"' in workspace.text
    assert 'id="guardrail-form"' in workspace.text
    assert 'id="workspace-summary" class="lede hidden" aria-hidden="true"' in workspace.text
    assert 'id="meta-connect"' in workspace.text
    assert "Connecting an account grants access only" in workspace.text
    assert 'id="pause-button"' in workspace.text
    assert 'id="resume-button"' in workspace.text
    assert 'id="metric-cac"' in workspace.text
    assert 'id="metric-customers"' in workspace.text
    assert 'id="growth-balance-metric"' in workspace.text
    assert 'id="overview-fund-button"' in workspace.text
    assert 'id="overview-fund-label"' in workspace.text
    assert "Acquisition budget" in workspace.text
    assert "Approve only what the move needs" in workspace.text
    assert "One real opportunity researched" in workspace.text
    assert "Fund a recommended paid test when needed" in workspace.text
    assert "Tests & decisions" in workspace.text
    assert "AI customer acquisition system" in workspace.text
    for href in ("/privacy", "/terms", "/security", "/contact"):
        assert f'href="{href}"' in workspace.text
    assert "Let's find your first users." in workspace.text
    assert (
        "You should not have to choose a marketing channel or configure a campaign first."
        in workspace.text
    )
    assert 'id="activation-card"' in workspace.text
    assert 'id="activation-primary"' in workspace.text
    assert 'id="activation-preview"' in workspace.text
    assert 'id="activation-preview-opportunity"' in workspace.text
    assert 'id="activation-preview-directions"' in workspace.text
    assert 'id="activation-preview-label"' in workspace.text
    assert "Research evidence" in workspace.text
    assert "What's the most you'd pay for one new customer?" in workspace.text
    assert 'id="autopilot-budget"' not in workspace.text
    assert "$149" not in workspace.text
    assert "Creators</span>" not in workspace.text
    assert "Search</span>" not in workspace.text
    assert "Partners</span>" not in workspace.text

    stylesheet = client.get("/workspace/assets/workspace.v1.css")
    assert stylesheet.status_code == 200
    assert ".metric-grid-primary" in stylesheet.text
    assert ".growth-balance-metric" in stylesheet.text
    assert ".growth-balance-cta" in stylesheet.text
    assert ".growth-balance-benefit" in stylesheet.text
    assert ".workspace-tabs" in stylesheet.text
    assert ".channel-table" in stylesheet.text
    assert ".activation-card" in stylesheet.text
    assert ".activation-list" in stylesheet.text
    assert ".activation-preview-directions" in stylesheet.text
    assert ".activation-preview-opportunity" in stylesheet.text
    assert ".product-legal-footer" in stylesheet.text


def test_customer_browsers_use_separate_funnel_and_workspace_boundaries() -> None:
    start_source = START_JS.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_JS.read_text(encoding="utf-8")

    assert "/v1/customer-projects/preview" in start_source
    assert "/confirm-preview" in start_source
    assert "/customer/account/register" in start_source
    assert "/customer/account/login" in start_source
    assert "/customer/account/projects/claim" in start_source
    assert "/growth-balance/checkout" not in start_source
    assert "/autopilot/meta/connect" not in start_source
    assert "X-Partizan-Customer-Token" in start_source

    expected_workspace_paths = (
        "/growth-balance/checkout",
        "/growth-balance/verify",
        "/meta/connect",
        "/meta/options",
        "/meta/connection",
        "/autopilot/status",
        "/autopilot",
        "/channels",
    )
    for path in expected_workspace_paths:
        assert path in workspace_source

    assert "$('meta-connect').disabled = !overview.product_id;" not in workspace_source
    assert "$('meta-connect').disabled = false;" in workspace_source
    assert "channel-mode-select" in workspace_source
    assert "openFundingControls" in workspace_source
    assert "$('overview-fund-button').addEventListener('click', openFundingControls)" in workspace_source
    assert "$('overview-fund-label').textContent = 'Add funds'" in workspace_source
    assert "renderActivation" in workspace_source
    assert "renderActivationPreview" in workspace_source
    assert "data.preview_directions || []" in workspace_source
    assert "data.preview_opportunity || null" in workspace_source
    assert "Real public-web research · not a conversion claim" in workspace_source
    assert (
        "Connect Meta only if a recommended Instagram & Facebook action needs execution access."
        in workspace_source
    )
    assert "project.launch_unlocked" in workspace_source
    assert "activationAction === 'fund'" in workspace_source
    assert "activationAction === 'opportunity'" in workspace_source
    assert "Open this $0 move →" in workspace_source
    assert "activationAction === 'channels'" not in workspace_source
    assert "activationAction === 'integration'" not in workspace_source
    assert "autoChannel.platform !== 'INSTAGRAM' || Boolean(overview.meta.connected)" not in workspace_source
    assert "activationAction === 'limit'" not in workspace_source
    assert "See what Partizan found →" in workspace_source
    assert "amount.select()" in workspace_source
    assert "RESEARCH_ONLY" in workspace_source
    assert "/autopilot/checkout" not in workspace_source
    assert "/autopilot/verify" not in workspace_source
    assert "subscription_status" not in workspace_source
    assert "marketing_budget_usd" not in workspace_source
    assert "remaining_budget_usd" not in workspace_source
    assert "X-Partizan-Customer-Token" not in workspace_source
    assert "X-Partizan-Operator-Key" not in workspace_source
    assert "META_OAUTH_APP_SECRET" not in workspace_source
    assert "PROVIDER_SECRET_ENCRYPTION_KEY" not in workspace_source
    assert "access_token" not in workspace_source
    assert "/paid-campaign/meta/activate" not in workspace_source
    assert "/ops/autonomous-growth/sweep" not in workspace_source
