from html.parser import HTMLParser

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


class Links(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.destinations: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.destinations.append(dict(attrs).get("href") or "")


def test_marketing_header_exposes_direct_customer_workspace_entry() -> None:
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
    assert 'id="nav-account-link"' in html
    assert 'href="/workspace"' in html
    assert ">Sign in</a>" in html
    assert "Get your product" in html
    assert "the right people." in html
    assert 'id="hero-scan-form"' in html
    assert 'id="hero-product-link"' in html
    assert 'id="example"' in html
    assert 'id="how-it-works"' in html
    assert 'id="channels"' in html
    assert 'id="pricing"' in html
    assert "No monthly subscription" in html
    assert "10% of actual acquisition spend" in html
    assert "Just want a research plan? $49 once" in html
    assert "Illustrative example" in html
    assert "not customer results or live recommendations" in html
    assert "Account connection and funding never authorize publication by themselves" in html
    assert "Design preview" not in html
    assert "Partizan_handoff.zip" not in html
    links = Links()
    links.feed(html)
    starts = [url for url in links.destinations if url.startswith("/start")]
    assert len(starts) == 5
    assert all("release=" in url for url in starts)
    assert any("mode=describe" in url for url in starts)
    assert "/app" not in links.destinations
    for href in ("/privacy", "/terms", "/security", "/contact"):
        assert href in links.destinations


def test_marketing_account_entry_detects_existing_customer_session_fail_safe() -> None:
    javascript = client.get("/site/assets/landing.v1.js")
    assert javascript.status_code == 200
    for contract in (
        "/customer/account/me", "credentials: 'same-origin'", "cache: 'no-store'",
        "Open workspace", "nav-account-link", "const defaultBudget = 10;",
        "hero-scan-form", "hero-product-link", "query.set('product', productLink)",
        "query.set('release', startRelease)", 'a[href^="/start"]',
    ):
        assert contract in javascript.text
    assert "window.setInterval" not in javascript.text
    assert "/publish" not in javascript.text
    assert "/execute" not in javascript.text
    css = client.get("/site/assets/landing.v1.css")
    assert css.status_code == 200
    for selector in (".hero-form", ".showcase-section", ".workspace-demo", ".kind-tabs", ".price-calculator"):
        assert selector in css.text
    adapter = client.get("/site/assets/landing.account.v1.css")
    assert adapter.status_code == 200
    assert ".nav-account-link" in adapter.text
    assert "text/css" in adapter.headers["content-type"]
