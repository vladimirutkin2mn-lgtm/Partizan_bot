from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parent.parent
START_JS = ROOT / "app" / "web" / "start.v2.js"

client = TestClient(app)


def test_start_page_exposes_growth_balance_autopilot_setup_and_dashboard() -> None:
    response = client.get("/start")

    assert response.status_code == 200
    assert "/start/assets/start.autopilot.v1.css" in response.text
    assert "/start/assets/start.v2.css" in response.text
    assert 'id="autopilot-subscribe-button"' in response.text
    assert 'id="growth-balance-form"' in response.text
    assert 'id="autopilot-config-form"' in response.text
    assert 'id="meta-connect-button"' in response.text
    assert 'id="autopilot-dashboard"' in response.text
    assert 'id="autopilot-pause-button"' in response.text
    assert 'id="autopilot-budget"' not in response.text

    stylesheet = client.get("/start/assets/start.autopilot.v1.css")
    assert stylesheet.status_code == 200
    assert ".autopilot-dashboard" in stylesheet.text


def test_customer_browser_calls_only_customer_autopilot_boundary() -> None:
    source = START_JS.read_text(encoding="utf-8")

    expected_paths = (
        "/autopilot/checkout",
        "/autopilot/verify",
        "/growth-balance/checkout",
        "/growth-balance/verify",
        "/autopilot/meta/connect",
        "/autopilot/meta/options",
        "/autopilot/meta/connection",
        "/autopilot/status",
    )
    for path in expected_paths:
        assert path in source

    assert "marketing_budget_usd" not in source
    assert "remaining_budget_usd" not in source
    assert "X-Partizan-Customer-Token" in source
    assert "X-Partizan-Operator-Key" not in source
    assert "META_OAUTH_APP_SECRET" not in source
    assert "PROVIDER_SECRET_ENCRYPTION_KEY" not in source
    assert "access_token" not in source
    assert "/paid-campaign/meta/activate" not in source
    assert "/ops/autonomous-growth/sweep" not in source
