import re

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_customer_workspace_versions_css_and_javascript_by_content() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"

    css_match = re.search(r'/workspace/assets/workspace\.v1\.css\?v=([a-f0-9]{12})', response.text)
    js_match = re.search(r'/workspace/assets/workspace\.v1\.js\?v=([a-f0-9]{12})', response.text)

    assert css_match is not None
    assert js_match is not None
    assert css_match.group(1) == js_match.group(1)


def test_customer_workspace_assets_are_never_served_from_stale_browser_cache() -> None:
    page = client.get("/workspace")
    css_url = re.search(
        r'(/workspace/assets/workspace\.v1\.css\?v=[a-f0-9]{12})', page.text
    ).group(1)
    js_url = re.search(
        r'(/workspace/assets/workspace\.v1\.js\?v=[a-f0-9]{12})', page.text
    ).group(1)

    css = client.get(css_url)
    javascript = client.get(js_url)

    assert css.status_code == 200
    assert css.headers["cache-control"] == "no-store, max-age=0"
    assert "text/css" in css.headers["content-type"]
    assert ".workspace-tabs" in css.text
    assert ".channel-table" in css.text

    assert javascript.status_code == 200
    assert javascript.headers["cache-control"] == "no-store, max-age=0"
    assert "javascript" in javascript.headers["content-type"]
    assert "document.querySelectorAll('.tab-button')" in javascript.text
    assert "data-open-tab" in javascript.text
