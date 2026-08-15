from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_integration_readiness_assets_are_served() -> None:
    css = client.get("/app/assets/integration-status.v1.css")
    javascript = client.get("/app/assets/integration-status.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".integration-readiness-card" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "integration-readiness-card" in javascript.text


def test_readiness_loads_after_conversion_integration_before_autonomy() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert "/app/assets/integration.v1.js" in javascript
    assert "/app/assets/integration-status.v1.css" in javascript
    assert "/app/assets/integration-status.v1.js" in javascript
    assert 'script.addEventListener("load", loadIntegrationStatusAssets)' in javascript
    assert 'script.addEventListener("load", loadAutonomyAssets)' in javascript
    assert "else {\n      loadIntegrationStatusAssets();\n    }" in javascript
    assert "else {\n      loadAutonomyAssets();\n    }" in javascript


def test_readiness_workspace_is_read_only_and_reuses_live_operator_key() -> None:
    javascript = client.get("/app/assets/integration-status.v1.js").text

    assert "#integration-operator-key" in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert "/integration-status" in javascript
    assert 'fetch(`/v1/products/${id}/integration-status`, { headers })' in javascript
    assert "observed_event_types" in javascript
    assert 'for (const eventType of ["VISIT", "SIGNUP", "ACTIVATED", "PAID"])' in javascript

    for forbidden in (
        'method: "POST"',
        'method: "DELETE"',
        "/distribution-events",
        "/distribution-event-key",
        "/execute",
        "/spend",
        "/paid-campaign/activate",
    ):
        assert forbidden not in javascript


def test_readiness_workspace_does_not_persist_operator_credentials() -> None:
    javascript = client.get("/app/assets/integration-status.v1.js").text

    assert "sessionStorage.getItem" in javascript
    assert "sessionStorage.setItem" not in javascript
    assert "localStorage" not in javascript
    assert "dataset.operatorKey" not in javascript
    assert 'payload["event_key"]' not in javascript
    assert "payload.event_key" not in javascript


def test_readiness_observer_only_mounts_missing_card() -> None:
    javascript = client.get("/app/assets/integration-status.v1.js").text

    assert 'document.querySelector("#conversion-integration-panel")' in javascript
    assert '!document.querySelector("#integration-readiness-card")' in javascript
