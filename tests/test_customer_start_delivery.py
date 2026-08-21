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


def test_customer_start_separates_research_scope_from_execution_access() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/start.v2.css")
    javascript = client.get("/start/assets/start.v2.js")

    assert page.status_code == 200
    assert "Where Partizan can research" in page.text
    assert "Research is not execution." in page.text
    assert "Channels Partizan can use" not in page.text
    for surface in (
        "Creators",
        "Newsletters",
        "Podcasts",
        "Partnerships",
        "Search & SEO",
        "Directories",
        "Discord & forums",
    ):
        assert surface in page.text
    assert 'id="execution-access-step" class="setup-step channels-step hidden"' in page.text
    assert "First-class paid execution" in page.text
    assert "Instagram & Facebook" in page.text

    assert css.status_code == 200
    assert ".research-scope-pills" in css.text
    assert ".selected-access-card" in css.text
    assert ".channel-boundary-note" in css.text
    assert "classList.toggle('hidden', !researchReady)" in javascript.text


def test_customer_start_can_restore_same_browser_project() -> None:
    javascript = client.get("/start/assets/start.v2.js")

    assert javascript.status_code == 200
    assert "partizan.customer.preview." in javascript.text
    assert "resumeStoredProject" in javascript.text
    assert "localStorage.getItem(PROJECT_KEY)" in javascript.text
    assert "Welcome back. Your Partizan project is restored." in javascript.text
