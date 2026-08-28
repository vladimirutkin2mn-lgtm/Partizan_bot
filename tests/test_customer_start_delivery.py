from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_customer_start_is_no_store_and_references_custom_goal_assets() -> None:
    response = client.get("/start")

    assert response.status_code == 200
    assert "no-store" in response.headers["cache-control"]
    assert response.headers["pragma"] == "no-cache"
    assert '/start/assets/goal-dropdown.v1.css' in response.text
    assert '/start/assets/goal-dropdown.v1.js' in response.text
    assert '/start/assets/customer-account.v1.css' in response.text
    assert '<select id="goal">' in response.text
    assert "AI customer acquisition system" in response.text
    for href in ("/privacy", "/terms", "/security", "/contact"):
        assert f'href="{href}"' in response.text


def test_custom_goal_assets_are_allowlisted_and_served() -> None:
    css = client.get("/start/assets/goal-dropdown.v1.css")
    javascript = client.get("/start/assets/goal-dropdown.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".goal-menu" in css.text
    assert ".goal-option" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "goal-trigger" in javascript.text
    assert "aria-selected" in javascript.text


def test_customer_start_is_website_first_and_honest_about_the_free_scan() -> None:
    page = client.get("/start")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert "Paste your website" in page.text
    assert "I don't have a website" in page.text
    assert 'id="brief-fallback" class="brief-fallback hidden"' in page.text
    assert "How much can you comfortably test with?" in page.text
    assert "What matters most?" in page.text
    assert 'data-budget="10"' in page.text
    assert 'data-budget="50"' in page.text
    assert 'data-budget="100"' in page.text
    assert 'data-budget="500"' in page.text
    assert "The best first move may cost $0." in page.text
    assert "Scan my product" in page.text
    assert "The free scan is a fast hypothesis" in page.text
    assert "fake" not in page.text.lower()
    assert 'id="website" type="url" inputmode="url" placeholder=' in page.text
    assert 'id="website" type="url" inputmode="url" required' not in page.text
    assert "brief: brief || null" in javascript.text
    assert "website_url: website || null" in javascript.text
    assert "showBriefFallback" in javascript.text
    assert "const budgetPresets" in javascript.text
    assert "selectBudgetPreset" in javascript.text
    assert "initialWebsite" in javascript.text
    assert "$('website').value = initialWebsite" in javascript.text
    assert "masked_opportunities.map" not in javascript.text
    assert "Creator @••" not in page.text


def test_customer_start_sends_autonomous_customers_to_persistent_workspace() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/customer-account.v1.css")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert "10% of acquisition spend" in page.text
    assert "Create workspace" in page.text
    assert "Keep going" in page.text
    assert "Continuous learning" in page.text
    assert "Recommended next move" in page.text
    assert "Budget controls" in page.text
    assert 'id="register-form"' in page.text
    assert 'id="login-form"' in page.text
    assert 'href="/workspace"' in page.text
    assert 'id="growth-balance-form"' not in page.text
    assert 'id="execution-access-step"' not in page.text
    assert "Channels Partizan can use" not in page.text

    assert css.status_code == 200
    assert ".account-gate" in css.text
    assert ".autonomous-choice" in css.text
    assert javascript.status_code == 200
    assert "/customer/account/register" in javascript.text
    assert "/customer/account/login" in javascript.text
    assert "/customer/account/projects/claim" in javascript.text
    assert "redirectWorkspace" in javascript.text

    assert "Continuous learning" in page.text
    assert "Find</span><i>→</i><span>Try" in page.text
    assert "Initial market research and continuous learning are included" in page.text
    assert "Adding money does not by itself authorize paid advertising" in page.text
    assert "one full market-research pass" in page.text
    start_css = client.get("/start/assets/start.v2.css")
    assert start_css.status_code == 200
    assert ".autoresearch-included" in start_css.text
    assert ".autoresearch-loop" in start_css.text


def test_customer_start_keeps_same_browser_recovery_for_pre_account_and_research_only() -> None:
    javascript = client.get("/start/assets/start.v2.js")

    assert javascript.status_code == 200
    assert "partizan.customer.preview." in javascript.text
    assert "resumeStoredProject" in javascript.text
    assert "localStorage.getItem(PROJECT_KEY)" in javascript.text
    assert "Your Acquisition Plan is restored" in javascript.text
    assert "accountOwnsProject" in javascript.text
    assert "window.location.replace(`/workspace?project=" in javascript.text
