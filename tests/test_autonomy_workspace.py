from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_autonomy_assets_are_served() -> None:
    css = client.get("/app/assets/autonomy.v1.css")
    javascript = client.get("/app/assets/autonomy.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".autonomy-panel" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "autonomy-panel" in javascript.text


def test_execution_bootstrap_loads_autonomy_after_results_and_integration() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert "/app/assets/results.v1.js" in javascript
    assert "/app/assets/integration.v1.js" in javascript
    assert "/app/assets/autonomy.v1.css" in javascript
    assert "/app/assets/autonomy.v1.js" in javascript
    assert 'script.addEventListener("load", loadIntegrationAssets)' in javascript
    assert 'script.addEventListener("load", loadAutonomyAssets)' in javascript


def test_autonomy_workspace_uses_mandate_overview_and_bounded_sweep_contracts() -> None:
    javascript = client.get("/app/assets/autonomy.v1.js").text

    for contract in (
        "/autonomy-overview?timeline_limit=30",
        "/growth-mandate",
        "/growth-mandate/status",
        "/v1/ops/autonomous-growth/sweep?product_id=",
    ):
        assert contract in javascript

    for field in (
        "total_budget_cap",
        "target_max_cac",
        "max_autonomous_spend_per_experiment",
        "max_autonomous_spend_per_day",
        "max_concurrent_running_experiments",
        "allowed_platforms",
        "allowed_actions",
        "autonomous_prepare",
        "autonomous_approve",
        "autonomous_paid_activation",
        "approval_threshold",
    ):
        assert field in javascript


def test_autonomy_workspace_has_founder_kill_switch_and_budget_visibility() -> None:
    javascript = client.get("/app/assets/autonomy.v1.js").text

    assert 'button("ПАУЗА", "pause"' in javascript
    assert 'button("Возобновить", "resume"' in javascript
    assert 'body: { status }' in javascript
    assert "remaining_total_budget" in javascript
    assert "remaining_daily_budget" in javascript
    assert "observed_total_spend" in javascript
    assert "observed_daily_spend" in javascript
    assert "reserved_running_paid_budget" in javascript
    assert "Сейчас работает" in javascript
    assert "Ждёт подтверждения" in javascript
    assert "Последние решения Partizan" in javascript


def test_operator_key_is_page_memory_only_and_cleared_on_navigation() -> None:
    javascript = client.get("/app/assets/autonomy.v1.js").text

    assert 'let operatorKey = ""' in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert 'operatorKey = ""' in javascript
    assert 'progress.dataset.step !== "results"' in javascript
    assert "clearSecret()" in javascript

    assert "localStorage" not in javascript
    assert "sessionStorage.setItem" not in javascript
    assert "sessionStorage.removeItem" not in javascript
    assert "dataset.operatorKey" not in javascript
    assert "operatorKey:" not in javascript


def test_autonomy_workspace_cannot_directly_mutate_paid_provider_state_or_budget() -> None:
    javascript = client.get("/app/assets/autonomy.v1.js").text

    for forbidden in (
        "/paid-campaign/activate",
        "/paid-campaign/activation-authorizations",
        "/paid-campaign/meta/pause",
        "/paid-campaign/tiktok/pause",
        "/paid-campaign/meta/sync",
        "/paid-campaign/tiktok/sync",
        "/spend",
        "/restart",
        "/re-enable",
    ):
        assert forbidden not in javascript

    assert "Сам запускает paid spend в пределах exact budget" in javascript
    assert "worker проверяет его перед prepare, approve и каждым стартом paid spend" in javascript


def test_autonomy_workspace_validates_paid_delegation_dependency_client_side() -> None:
    javascript = client.get("/app/assets/autonomy.v1.js").text

    assert "if (paid && !approve)" in javascript
    assert "Автономный paid activation требует разрешить автономное подтверждение действий" in javascript
