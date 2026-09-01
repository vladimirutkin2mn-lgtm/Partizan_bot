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
    assert "You built the product." in html
    assert "For solo founders and small teams who built a product but do not know marketing yet" in html
    assert "Show Partizan what you built." in html
    assert "A website, app, bot or even a short description is enough to start." in html
    assert "Start free · No card required" in html
    assert "Start with what you have." in html
    assert "Partizan may tell you not to run ads yet." in html
    assert "Maybe you do not need Meta Ads." in html
    assert "$10</span><span>$50</span><span>$100</span><span>$500" in html
    assert "$1,000</span>" not in html
    assert "Sometimes the best first move costs $0." in html
    assert "See what Partizan actually finds." in html
    assert "r/Freelancers" in html
    assert "Gabrielle Talks Money" in html
    assert "Research evidence is not conversion evidence." in html
    assert "Find → Try → Learn." in html
    assert 'class="loop-compact"' in html
    assert 'class="founder-problem-layout"' in html
    assert 'class="budget-story-layout"' in html
    assert 'class="channel-grid"' in html
    assert 'class="capability-details"' in html
    assert 'class="safety-trust' in html
    assert 'class="channel-stage' not in html
    assert 'class="control-demo' not in html
    assert 'id="product-demo"' not in html
    assert "Animation shows decision flow only." not in html
    assert "Research across" in html
    assert 'id="pricing"' in html
    assert 'id="safety"' in html
    assert "Start free. Then choose how far Partizan should go." in html
    assert "From $10" in html
    assert "Prefer research only?" in html
    assert "One real opportunity before funding" in html
    assert (
        "Account connection is available; paid actions still require the account, "
        "budget and permission to be ready."
    ) in html
    assert "Research only today." in html
    assert "Ask above $200" not in html
    assert "Customers at $24 CAC" not in html
    assert "~41" not in html
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
    assert "customer-count" not in javascript.text
    assert "customersAtExampleCac" not in javascript.text
    assert "const demoSteps" not in javascript.text
    assert "window.setInterval" not in javascript.text

    assert account_css.status_code == 200
    assert "text/css" in account_css.headers["content-type"]
    assert ".nav-actions" in account_css.text
    assert ".nav-account-link" in account_css.text

    landing_css = client.get("/site/assets/landing.v1.css")
    assert landing_css.status_code == 200
    assert ".anti-waste-panel" in landing_css.text
    assert ".budget-story-layout" in landing_css.text
    assert ".hero-scan-form" in landing_css.text
    assert ".builder-flow" in landing_css.text
    assert ".problem-points" in landing_css.text
    assert ".loop-compact" in landing_css.text
    assert ".channel-grid" in landing_css.text
    assert ".safety-trust" in landing_css.text
    assert "--density-tier-1" in landing_css.text
    assert "--density-tier-2" in landing_css.text
    assert "--density-tier-3" in landing_css.text
    assert ".research-proof-grid" in landing_css.text
    assert ".research-opportunity" in landing_css.text
    assert ".pricing-grid" in landing_css.text
    assert ".capability-matrix" in landing_css.text
    assert ".pricing-grid-two" in landing_css.text
    assert ".footer-links" in landing_css.text
    assert "@media (max-width: 760px)" in account_css.text
