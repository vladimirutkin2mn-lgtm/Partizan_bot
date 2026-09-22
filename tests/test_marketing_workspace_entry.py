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
    assert 'href="/start?release=' in html
    assert 'href="/start"' not in html
    assert ">Analyze my product <span>↗</span></a>" in html
    assert "An always-learning way to grow" in html
    assert "Get your product in front of the right people." in html
    assert "Partizan finds your audience, tests ways to reach them, and learns what works." in html
    assert "Find my audience" in html
    assert "Free product analysis" in html
    assert "No card required" in html
    assert "Find your audience across" in html
    for channel in ("Meta", "Telegram", "Reddit", "Google Ads", "TikTok"):
        assert channel in html
    assert "One product. A clearer next move." in html
    assert "See where Partizan could take you." in html
    assert "Daily English" in html
    assert "Busy adults who want a daily English habit." in html
    assert "Five minutes of speaking. Right inside Telegram." in html
    assert "Telegram + Meta" in html
    assert "The hard part after you ship" in html
    assert "From your product to your next customers" in html
    assert "Starts with your product. Finds who it’s for." in html
    assert "Finds a place to start. Prepares the next test." in html
    assert "Every result informs what happens next." in html
    assert "Automation, on your terms" in html
    assert "Partizan does the work. You keep the controls." in html
    assert 'class="control-demo' in html
    assert "It keeps learning" in html
    assert "A first test is just the beginning." in html
    assert 'id="pricing"' in html
    assert "Start small. Learn as you go." in html
    assert "acquisition budget from $10" in html
    assert "$49 once" in html
    assert "10% of actual acquisition spend" in html
    assert 'id="faq"' in html
    assert "Good questions. Straight answers." in html
    assert "Your next move starts here" in html
    for href in ("/privacy", "/terms", "/security", "/contact"):
        assert f'href="{href}"' in html
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
    assert "const defaultBudget = 10;" in javascript.text
    assert "hero-scan-form" in javascript.text
    assert "query.set('product', productLink)" in javascript.text
    assert "hero-product-link" in javascript.text
    assert "startRelease" in javascript.text
    assert "query.set('release', startRelease)" in javascript.text
    assert 'a[href^="/start"]' in javascript.text
    assert "scenarios" in javascript.text
    assert "fee-range" in javascript.text
    assert "window.setInterval" not in javascript.text

    assert account_css.status_code == 200
    assert "text/css" in account_css.headers["content-type"]
    assert ".nav-actions" in account_css.text
    assert ".nav-account-link" in account_css.text

    landing_css = client.get("/site/assets/landing.v1.css")
    assert landing_css.status_code == 200
    for selector in (
        ".hero-form",
        ".channel-strip",
        ".workspace-demo",
        ".problem-grid",
        ".steps-stack",
        ".control-demo",
        ".channel-toggle",
        ".learning-card",
        ".pricing-layout",
        ".fee-card",
        ".faq-list",
        ".footer-links",
    ):
        assert selector in landing_css.text
    assert "--accent" in landing_css.text
    assert "--lime" in landing_css.text
    assert "@media(max-width:700px)" in landing_css.text
    assert "@media (max-width: 760px)" in account_css.text
