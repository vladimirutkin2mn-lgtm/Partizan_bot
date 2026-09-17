from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_workspace_loads_meta_oauth_error_diagnostics_asset() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    assert "/workspace/assets/workspace.meta-oauth-errors.v1.js?v=" in response.text


def test_meta_oauth_error_asset_exposes_only_safe_diagnostic_fields() -> None:
    response = client.get("/workspace/assets/workspace.meta-oauth-errors.v1.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    javascript = response.text
    for token in (
        "meta_error",
        "meta_reason",
        "meta_code",
        "access_denied",
        "no_manageable_ad_accounts",
        "no_promotable_pages",
        "oauth_state_invalid",
        "meta_api_rejected",
    ):
        assert token in javascript
    assert "access_token" not in javascript
    assert "error_description" not in javascript
