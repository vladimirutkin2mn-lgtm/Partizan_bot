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


def test_customer_start_makes_partizan_work_before_goal_and_budget() -> None:
    page = client.get("/start")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert "Step 1 · Start free · no card required" in page.text
    assert "Paste your product." in page.text
    assert "Product website" in page.text
    assert "I don't have a website" in page.text
    assert 'id="brief-fallback" class="brief-fallback hidden"' in page.text
    assert "Step 2 · Partizan understands it" in page.text
    assert "We think you built:" in page.text
    assert "Looks right" in page.text
    assert "Step 3 · Your outcome" in page.text
    assert "What would success look like?" in page.text
    assert '<option value="Get first users">First users</option>' in page.text
    assert "Step 4 · Your boundary" in page.text
    assert "How much can you comfortably test with?" in page.text
    assert 'data-budget="10"' in page.text
    assert 'data-budget="50"' in page.text
    assert 'data-budget="100"' in page.text
    assert 'data-budget="500"' in page.text
    assert "Find one real opportunity" in page.text
    assert "before asking you to fund anything" in page.text
    assert "Scan my product" in page.text
    assert "fake" not in page.text.lower()
    assert 'id="website" type="url" inputmode="url" placeholder=' in page.text
    assert 'id="website" type="url" inputmode="url" required' not in page.text

    preview_call = javascript.text.index("'/v1/customer-projects/preview'")
    goal_read = javascript.text.index("goal: $('goal').value")
    budget_read = javascript.text.index("budget_usd: Number($('budget').value)")
    confirm_call = javascript.text.index("/confirm-preview")
    assert preview_call < confirm_call
    assert goal_read > preview_call
    assert budget_read > preview_call
    assert "brief: brief || null" in javascript.text
    assert "website_url: website || null" in javascript.text
    assert "renderUnderstanding" in javascript.text
    assert "renderFreeOpportunity" in javascript.text
    assert "renderResearchPending" in javascript.text
    assert "renderResearchOutcome" in javascript.text
    assert "/preview-research" in javascript.text
    assert "Keep researching →" in javascript.text
    assert "Hypothesis" in javascript.text
    assert "Partizan will not invent an opportunity" in javascript.text
    assert "recommended_action" in javascript.text
    assert "item.provenance" in javascript.text
    assert "showBriefFallback" in javascript.text
    assert "const budgetPresets" in javascript.text
    assert "selectBudgetPreset" in javascript.text
    assert "initialWebsite" in javascript.text
    assert "$('website').value = initialWebsite" in javascript.text
    assert "masked_opportunities.map" not in javascript.text


def test_customer_start_keeps_workspace_primary_and_research_only_plan_secondary() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/customer-account.v1.css")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert "Continue with this move" in page.text
    assert "Research-only alternative" in page.text
    assert "Full Acquisition Plan — $49 once" in page.text
    assert "alternate research-only path" in page.text
    assert "start with the opportunity you already saw" in page.text
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
    assert "No acquisition budget was required to see it." in page.text
    assert 'id="preview-research-retry"' in page.text
    assert 'id="preview-title"' in page.text
    assert 'id="preview-eyebrow"' in page.text
    assert "Research evidence is not a proven acquisition result." in page.text
    start_css = client.get("/start/assets/start.v2.css")
    assert start_css.status_code == 200
    assert ".free-opportunity" in start_css.text
    assert ".understanding-card" in start_css.text


def test_customer_start_keeps_same_browser_recovery_for_pre_account_and_research_only() -> None:
    javascript = client.get("/start/assets/start.v2.js")

    assert javascript.status_code == 200
    assert "partizan.customer.preview." in javascript.text
    assert "resumeStoredProject" in javascript.text
    assert "localStorage.getItem(PROJECT_KEY)" in javascript.text
    assert "Your Acquisition Plan is restored" in javascript.text
    assert "accountOwnsProject" in javascript.text
    assert "window.location.replace(`/workspace?project=" in javascript.text
