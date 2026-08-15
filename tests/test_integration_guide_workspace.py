from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_integration_guide_assets_are_served() -> None:
    css = client.get("/app/assets/integration-guide.v1.css")
    javascript = client.get("/app/assets/integration-guide.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".integration-guide-card" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "integration-guide-card" in javascript.text


def test_guide_loads_after_readiness_before_autonomy() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert "/app/assets/integration-status.v1.js" in javascript
    assert "/app/assets/integration-guide.v1.css" in javascript
    assert "/app/assets/integration-guide.v1.js" in javascript
    assert "/app/assets/autonomy.v1.js" in javascript
    assert 'script.addEventListener("load", loadIntegrationGuideAssets)' in javascript
    assert 'script.addEventListener("load", loadAutonomyAssets)' in javascript


def test_guide_workspace_is_read_only_and_uses_live_operator_key() -> None:
    javascript = client.get("/app/assets/integration-guide.v1.js").text

    assert "#integration-operator-key" in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert "/integration-guide" in javascript
    assert 'fetch(`/v1/products/${id}/integration-guide`, { headers })' in javascript
    assert "navigator.clipboard.writeText" in javascript

    for forbidden in (
        'method: "POST"',
        'method: "DELETE"',
        "/distribution-events/verify",
        "/distribution-events`",
        "/distribution-event-key",
        "/execute",
        "/spend",
        "/paid-campaign/activate",
    ):
        assert forbidden not in javascript


def test_guide_workspace_never_persists_or_requests_plaintext_event_key() -> None:
    javascript = client.get("/app/assets/integration-guide.v1.js").text

    assert "sessionStorage.getItem" in javascript
    assert "sessionStorage.setItem" not in javascript
    assert "localStorage" not in javascript
    assert "payload.event_key" not in javascript
    assert 'payload["event_key"]' not in javascript
    assert "distribution-event-key" not in javascript


def test_guide_observer_only_mounts_missing_card() -> None:
    javascript = client.get("/app/assets/integration-guide.v1.js").text

    assert 'document.querySelector("#conversion-integration-panel")' in javascript
    assert '!document.querySelector("#integration-guide-card")' in javascript
