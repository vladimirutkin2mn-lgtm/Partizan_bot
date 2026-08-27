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


def test_customer_start_is_honest_about_hypotheses_and_optional_website() -> None:
    page = client.get("/start")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert "Initial acquisition hypotheses" in page.text
    assert "This is not deep research yet" in javascript.text
    assert "fake" not in page.text.lower()
    assert 'id="website" type="url" inputmode="url" placeholder=' in page.text
    assert 'id="website" type="url" inputmode="url" required' not in page.text
    assert "only needs a live destination before it sends paid traffic" in page.text
    assert "website_url: website || null" in javascript.text
    assert "masked_opportunities.map" not in javascript.text
    assert "Creator @••" not in page.text


def test_customer_start_sends_autonomous_customers_to_persistent_workspace() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/customer-account.v1.css")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert "10% of acquisition spend" in page.text
    assert "Create workspace" in page.text
    assert "Growth Balance" in page.text
    assert "Current work" in page.text
    assert "Results" in page.text
    assert "Integrations" in page.text
    assert "Guardrails" in page.text
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
    assert "Research</span><i>→</i><span>Test" in page.text
    assert "continuous AutoResearch are included" in page.text
    assert "Funding does not by itself authorize ad spend" in page.text
    assert "one deep research pass" in page.text
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
