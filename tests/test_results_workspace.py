from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_app_serves_paid_enabled_workspace_shell() -> None:
    response = client.get("/app")

    assert response.status_code == 200
    html = response.text
    assert "/app/assets/paid-control.v1.css" in html
    assert "/app/assets/execution.v2.js" in html
    assert "/app/assets/paid-control.v1.js" in html
    assert "/app/assets/execution.v1.js" not in html


def test_v2_bootstrap_and_paid_control_assets_are_actually_served() -> None:
    for asset_name in (
        "execution.v2.js",
        "paid-control.v1.css",
        "paid-control.v1.js",
        "results.v1.css",
        "results.v1.js",
    ):
        response = client.get(f"/app/assets/{asset_name}")
        assert response.status_code == 200


def test_execution_v2_bootstraps_legacy_execution_and_results_modules() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert "/app/assets/execution.v1.js" in javascript
    assert "/app/assets/results.v1.css" in javascript
    assert "/app/assets/results.v1.js" in javascript
    assert "reopenAfterPaidActivation" in javascript


def test_results_workspace_uses_distribution_native_learning_contracts() -> None:
    javascript = client.get("/app/assets/results.v1.js").text

    for api_contract in (
        "/distribution-analytics",
        "/distribution-learning",
        "/distribution-portfolio?max_items=4",
        "/v1/distribution-experiments/",
        "/growth-decision",
    ):
        assert api_contract in javascript

    assert "SCALE · масштабировать" in javascript
    assert "CONTINUE · продолжить" in javascript
    assert "MODIFY · изменить" in javascript
    assert "STOP · остановить" in javascript
    assert "Provider campaigns не изменялись" in javascript


def test_results_stage_renders_only_observed_metrics_and_target_comparison() -> None:
    javascript = client.get("/app/assets/results.v1.js").text

    for metric in (
        "visits",
        "signups",
        "activated_users",
        "paid_users",
        "revenue",
        "blended_cac",
        "blended_roas",
    ):
        assert metric in javascript
    assert "max_cac" in javascript
    assert "persisted distribution analytics" in javascript
    assert "demo" not in javascript.lower()


def test_results_workspace_cannot_execute_or_activate_provider_campaigns() -> None:
    javascript = client.get("/app/assets/results.v1.js").text

    forbidden_write_paths = (
        "/paid-campaign/activate",
        "/paid-campaign/activation-authorizations",
        "/paid-campaign/meta/pause",
        "/paid-campaign/tiktok/pause",
        "/distribution-actions/",
        "/execute",
        "/approve",
        "/spend",
    )
    for path in forbidden_write_paths:
        assert path not in javascript

    assert "prepare → approve → STAGED → exact-budget authorization → activation" in javascript
    assert "SCALE здесь означает рекомендацию" in javascript


def test_results_workspace_does_not_persist_operator_or_provider_secrets() -> None:
    bootstrap = client.get("/app/assets/execution.v2.js").text
    results = client.get("/app/assets/results.v1.js").text
    combined = bootstrap + results

    assert "X-Partizan-Operator-Key" not in results
    assert "operatorKey" not in results
    assert "access_token" not in results.lower()
    assert "localStorage" not in combined
    assert "META_ORACLE_ACCESS_TOKEN" not in combined
    assert "TIKTOK_ORACLE_ACCESS_TOKEN" not in combined


def test_results_navigation_is_injected_as_fifth_stage() -> None:
    javascript = client.get("/app/assets/results.v1.js").text

    assert 'button.id = "results-step"' in javascript
    assert 'section.id = "stage-results"' in javascript
    assert 'button.dataset.step = "results"' in javascript
    assert 'node("span", "", "05")' in javascript
    assert 'node("strong", "", "Результаты")' in javascript
