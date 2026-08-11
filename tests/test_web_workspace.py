from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_redirects_to_dogfooding_workspace() -> None:
    response = client.get("/", follow_redirects=False)

    assert response.status_code in {302, 307}
    assert response.headers["location"] == "/app"


def test_workspace_shell_contains_core_growth_stages() -> None:
    response = client.get("/app")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    for anchor in (
        'id="product-form"',
        'id="stage-audience"',
        'id="stage-distribution"',
        'id="stage-experiments"',
        'id="generate-icps"',
        'id="discover-distribution"',
        'id="generate-plays"',
    ):
        assert anchor in html
    assert "/app/assets/partizan.v1.css" in html
    assert "/app/assets/partizan.v1.js" in html


def test_workspace_css_and_javascript_are_served() -> None:
    css = client.get("/app/assets/partizan.v1.css")
    javascript = client.get("/app/assets/partizan.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "--acid" in css.text
    assert ".progress-step" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "partizan.workspace.v1" in javascript.text
    for api_contract in (
        "/v1/products",
        "/icps/generate",
        "/distribution/discover",
        "/distribution-plays/generate",
    ):
        assert api_contract in javascript.text


def test_workspace_does_not_embed_sensitive_operator_or_provider_credentials() -> None:
    html = client.get("/app").text
    javascript = client.get("/app/assets/partizan.v1.js").text
    combined = html + javascript

    assert "OPERATOR_API_KEY" not in combined
    assert "X-Partizan-Operator-Key" not in combined
    assert "access_token" not in combined.lower()
    assert "META_ORACLE_ACCESS_TOKEN" not in combined
    assert "TIKTOK_ORACLE_ACCESS_TOKEN" not in combined


def test_unknown_workspace_asset_is_not_exposed() -> None:
    response = client.get("/app/assets/../../config.py")

    assert response.status_code == 404


def test_existing_health_endpoint_remains_available() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
