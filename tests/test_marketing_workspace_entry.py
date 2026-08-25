from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_marketing_header_exposes_direct_customer_workspace_entry() -> None:
    response = client.get("/")

    assert response.status_code == 200
    html = response.text
    assert 'id="nav-account-link"' in html
    assert 'href="/workspace"' in html
    assert ">Sign in</a>" in html
    assert 'href="/start"' in html
    assert "/site/assets/landing.account.v1.css" in html


def test_marketing_account_entry_detects_existing_customer_session_fail_safe() -> None:
    javascript = client.get("/site/assets/landing.v1.js")
    account_css = client.get("/site/assets/landing.account.v1.css")

    assert javascript.status_code == 200
    assert "/customer/account/me" in javascript.text
    assert "credentials: 'same-origin'" in javascript.text
    assert "cache: 'no-store'" in javascript.text
    assert "Open workspace" in javascript.text
    assert "nav-account-link" in javascript.text

    assert account_css.status_code == 200
    assert "text/css" in account_css.headers["content-type"]
    assert ".nav-actions" in account_css.text
    assert ".nav-account-link" in account_css.text
    assert "@media (max-width: 760px)" in account_css.text
