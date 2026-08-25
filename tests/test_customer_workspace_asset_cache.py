import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_customer_workspace_versions_css_and_javascript_by_content() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0"

    patterns = [
        r'/workspace/assets/workspace\.v1\.css\?v=([a-f0-9]{12})',
        r'/workspace/assets/workspace\.v1\.js\?v=([a-f0-9]{12})',
        r'/workspace/assets/workspace\.channels\.v1\.css\?v=([a-f0-9]{12})',
        r'/workspace/assets/workspace\.channels\.v1\.js\?v=([a-f0-9]{12})',
    ]
    revisions = []
    for pattern in patterns:
        match = re.search(pattern, response.text)
        assert match is not None
        revisions.append(match.group(1))

    assert len(set(revisions)) == 1


def test_customer_workspace_assets_are_never_served_from_stale_browser_cache() -> None:
    page = client.get("/workspace")
    css_url = re.search(
        r'(/workspace/assets/workspace\.v1\.css\?v=[a-f0-9]{12})', page.text
    ).group(1)
    js_url = re.search(
        r'(/workspace/assets/workspace\.v1\.js\?v=[a-f0-9]{12})', page.text
    ).group(1)
    channel_css_url = re.search(
        r'(/workspace/assets/workspace\.channels\.v1\.css\?v=[a-f0-9]{12})', page.text
    ).group(1)
    channel_js_url = re.search(
        r'(/workspace/assets/workspace\.channels\.v1\.js\?v=[a-f0-9]{12})', page.text
    ).group(1)

    css = client.get(css_url)
    javascript = client.get(js_url)
    channel_css = client.get(channel_css_url)
    channel_javascript = client.get(channel_js_url)

    for asset in (css, javascript, channel_css, channel_javascript):
        assert asset.status_code == 200
        assert asset.headers["cache-control"] == "no-store, max-age=0"

    assert "text/css" in css.headers["content-type"]
    assert ".workspace-tabs" in css.text
    assert ".channel-table" in css.text
    assert "javascript" in javascript.headers["content-type"]
    assert "document.querySelectorAll('.tab-button')" in javascript.text
    assert "data-open-tab" in javascript.text

    assert "text/css" in channel_css.headers["content-type"]
    assert ".channel-toggle-control" in channel_css.text
    assert ".channel-connect-button" in channel_css.text
    assert "javascript" in channel_javascript.headers["content-type"]
    assert "channel-toggle" in channel_javascript.text
    assert "channel-connect-button" in channel_javascript.text
