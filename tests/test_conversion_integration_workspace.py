from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_conversion_integration_assets_are_served() -> None:
    css = client.get("/app/assets/integration.v1.css")
    javascript = client.get("/app/assets/integration.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".conversion-integration" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "conversion-integration-panel" in javascript.text


def test_execution_bootstrap_loads_integration_only_after_results() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert "/app/assets/results.v1.js" in javascript
    assert "/app/assets/integration.v1.css" in javascript
    assert "/app/assets/integration.v1.js" in javascript
    assert 'script.addEventListener("load", loadIntegrationAssets)' in javascript


def test_integration_workspace_uses_only_event_key_management_contracts() -> None:
    javascript = client.get("/app/assets/integration.v1.js").text

    assert "/distribution-event-key" in javascript
    assert "/distribution-events" in javascript
    assert 'method: "POST"' in javascript
    assert 'method: "DELETE"' in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert "X-Partizan-Event-Key" in javascript

    for forbidden in (
        "/paid-campaign/activate",
        "/paid-campaign/meta/pause",
        "/paid-campaign/tiktok/pause",
        "/distribution-analytics/events",
        "/growth-decision",
        "/execute",
        "/spend",
    ):
        assert forbidden not in javascript


def test_event_key_plaintext_and_operator_key_are_page_memory_only() -> None:
    javascript = client.get("/app/assets/integration.v1.js").text

    assert 'let operatorKey = ""' in javascript
    assert 'let plaintextKey = ""' in javascript
    assert "created.event_key" in javascript
    assert "navigator.clipboard.writeText(plaintextKey)" in javascript
    assert 'plaintextKey = ""' in javascript
    assert 'operatorKey = ""' in javascript

    assert "localStorage" not in javascript
    assert "sessionStorage.setItem" not in javascript
    assert "sessionStorage.removeItem" not in javascript
    assert "dataset.eventKey" not in javascript
    assert "dataset.operatorKey" not in javascript
    assert "operatorKey:" not in javascript
    assert "plaintextKey:" not in javascript


def test_integration_requires_status_check_before_create_or_rotate() -> None:
    javascript = client.get("/app/assets/integration.v1.js").text

    assert "let statusLoaded = false" in javascript
    assert '"Сначала обнови статус"' in javascript
    assert "busy || !statusLoaded" in javascript
    assert "if (!id || busy || !statusLoaded) return" in javascript
    assert "statusLoaded = true" in javascript
    assert "Ротация немедленно инвалидирует предыдущий Event Key" in javascript


def test_plaintext_one_time_warning_and_clear_action_are_present() -> None:
    javascript = client.get("/app/assets/integration.v1.js").text

    assert "Сохрани сейчас — повторно Partizan этот ключ не покажет" in javascript
    assert 'button("Скопировать ключ", "copy-key"' in javascript
    assert 'button("Я сохранил ключ", "clear-key"' in javascript
    assert "Plaintext очищен из интерфейса" in javascript
    assert "secret manager / server environment" in javascript
    assert "Не вставляй его в браузерный JavaScript" in javascript


def test_server_example_references_environment_secret_not_plaintext() -> None:
    javascript = client.get("/app/assets/integration.v1.js").text

    assert "# SERVER-SIDE Python example" in javascript
    assert 'os.environ[\\"PARTIZAN_EVENT_KEY\\"]' in javascript
    assert "saved_ptz_experiment" in javascript
    assert "SIGNUP | ACTIVATED | PAID" in javascript
    assert "PARTIZAN_PUBLIC_BASE_URL" in javascript
    assert "/r/{referral_token}" in javascript


def test_navigation_away_clears_volatile_secrets() -> None:
    javascript = client.get("/app/assets/integration.v1.js").text

    assert 'progress.dataset.step !== "results"' in javascript
    assert "clearSecrets()" in javascript
    assert 'operator.value = ""' in javascript
    assert 'value.textContent = ""' in javascript
