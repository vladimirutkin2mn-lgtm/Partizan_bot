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
