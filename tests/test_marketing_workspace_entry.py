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
    assert ">Start free <span>↗</span></a>" in html
    assert "Tell Partizan what you sell." in html
    assert "It finds where your customers are — and tests what works." in html
    assert "No card required." in html
    assert "You have $1,000." in html
    assert "This is math, not a Partizan forecast." in html
    assert "See what Partizan actually finds." in html
    assert "r/Freelancers" in html
    assert "Gabrielle Talks Money" in html
    assert "Research evidence is not conversion evidence." in html
    assert "Find → Test → Learn." in html
    assert 'id="pricing"' in html
    assert 'id="safety"' in html
    assert "Simple pricing." in html
    assert "No monthly fee" in html
    assert "Account connection available; paid spend stays gated by the production spend rail." in html
    assert "Research only today." in html
    assert "Ask above $200" not in html
    assert "Customers at $24 CAC" not in html
    assert "~41" not in html
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
    assert "const defaultBudget = 1000;" in javascript.text
    assert "customer-count" not in javascript.text
    assert "customersAtExampleCac" not in javascript.text

    assert account_css.status_code == 200
    assert "text/css" in account_css.headers["content-type"]
    assert ".nav-actions" in account_css.text
    assert ".nav-account-link" in account_css.text

    landing_css = client.get("/site/assets/landing.v1.css")
    assert landing_css.status_code == 200
    assert ".budget-story-grid" in landing_css.text
    assert ".research-proof-grid" in landing_css.text
    assert ".research-opportunity" in landing_css.text
    assert ".pricing-grid" in landing_css.text
    assert ".capability-matrix" in landing_css.text
    assert "@media (max-width: 760px)" in account_css.text
