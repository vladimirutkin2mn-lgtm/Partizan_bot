import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_workspace_loads_versioned_new_project_assets() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    css_match = re.search(
        r'(/workspace/assets/workspace\.projects\.v1\.css\?v=[a-f0-9]{12})',
        response.text,
    )
    js_match = re.search(
        r'(/workspace/assets/workspace\.projects\.v1\.js\?v=[a-f0-9]{12})',
        response.text,
    )
    assert css_match is not None
    assert js_match is not None
    assert css_match.group(1).split("?v=")[1] == js_match.group(1).split("?v=")[1]

    css = client.get(css_match.group(1))
    javascript = client.get(js_match.group(1))
    assert css.status_code == 200
    assert javascript.status_code == 200
    assert css.headers["cache-control"] == "no-store, max-age=0"
    assert javascript.headers["cache-control"] == "no-store, max-age=0"
    assert ".project-modal" in css.text
    assert ".project-details-card" in css.text
    assert ".project-danger-zone" in css.text
    assert "+ New project" in javascript.text
    assert "Project details" in javascript.text
    assert "Description" in javascript.text
    assert "Delete project" in javascript.text
    assert "method: 'DELETE'" in javascript.text
    assert "'/customer/account/projects'" in javascript.text
    assert "customer_token" not in javascript.text
