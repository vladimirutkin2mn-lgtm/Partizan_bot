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
    assert 'id="fund-form"' in workspace.text
    assert 'id="guardrail-form"' in workspace.text
    assert 'id="meta-connect"' in workspace.text
    assert "Connect now, use only when useful" in workspace.text
    assert "It does not authorize spend" in workspace.text
    assert 'id="pause-button"' in workspace.text
    assert 'id="resume-button"' in workspace.text
    assert 'id="metric-cac"' in workspace.text
    assert 'id="metric-customers"' in workspace.text
    assert 'id="autopilot-budget"' not in workspace.text
    assert "$149" not in workspace.text

    stylesheet = client.get("/workspace/assets/workspace.v1.css")
    assert stylesheet.status_code == 200
    assert ".metric-grid" in stylesheet.text
    assert ".workspace-grid" in stylesheet.text


def test_customer_browsers_use_separate_funnel_and_workspace_boundaries() -> None:
    start_source = START_JS.read_text(encoding="utf-8")
    workspace_source = WORKSPACE_JS.read_text(encoding="utf-8")

    assert "/v1/customer-projects/preview" in start_source
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
    )
    for path in expected_workspace_paths:
        assert path in workspace_source

    assert "$('meta-connect').disabled = !overview.product_id;" not in workspace_source
    assert "$('meta-connect').disabled = false;" in workspace_source
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
