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


def test_customer_start_separates_research_surfaces_from_execution_access() -> None:
    page = client.get("/start")
    css = client.get("/start/assets/start.v2.css")

    assert page.status_code == 200
    assert "Where Partizan can find customers" in page.text
    assert "Execution ecosystems" in page.text
    assert "Public-web research" in page.text
    assert "Creators &amp; influencers" in page.text or "Creators & influencers" in page.text
    assert "Newsletters &amp; podcasts" in page.text or "Newsletters & podcasts" in page.text
    assert "Partnerships &amp; affiliates" in page.text or "Partnerships & affiliates" in page.text
    assert "Google Search &amp; SEO" in page.text or "Google Search & SEO" in page.text
    assert "Directories &amp; niche sites" in page.text or "Directories & niche sites" in page.text
    assert "Discord, forums &amp; groups" in page.text or "Discord, forums & groups" in page.text
    assert "Research is not execution." in page.text
    assert "Channels Partizan can use" not in page.text

    assert css.status_code == 200
    assert ".research-channel-grid" in css.text
    assert ".channel-boundary-note" in css.text
