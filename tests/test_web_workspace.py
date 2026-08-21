from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_serves_marketing_site() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    html = response.text
    for anchor in (
        "<title>Partizan — AI that finds and acquires customers</title>",
        'href="/start"',
        'id="budget-range"',
        'id="how"',
        'id="channels"',
        'id="autonomy"',
        "/site/assets/landing.v1.css",
        "/site/assets/landing.v1.js",
    ):
        assert anchor in html
    assert 'href="/app"' not in html


def test_marketing_assets_are_allowlisted_and_served() -> None:
    css = client.get("/site/assets/landing.v1.css")
    javascript = client.get("/site/assets/landing.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "--lime" in css.text
    assert ".hero-console" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "budget-range" in javascript.text
    assert "IntersectionObserver" in javascript.text


def test_workspace_shell_contains_core_growth_and_execution_surfaces() -> None:
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
        'id="execution-drawer"',
        'id="prepare-execution"',
        'id="approve-execution"',
        'id="run-execution"',
        'id="operator-key"',
    ):
        assert anchor in html
    for asset in (
        "/app/assets/operator-auth.v1.js",
        "/app/assets/partizan.v1.css",
        "/app/assets/partizan.v1.js",
        "/app/assets/execution.v1.css",
        "/app/assets/execution.v2.js",
        "/app/assets/paid-control.v1.css",
        "/app/assets/paid-control.v1.js",
    ):
        assert asset in html
    assert html.index("/app/assets/operator-auth.v1.js") < html.index(
        "/app/assets/partizan.v1.js"
    )
    assert "/app/assets/execution.v1.js" not in html


def test_workspace_assets_and_live_api_contracts_are_served() -> None:
    css = client.get("/app/assets/partizan.v1.css")
    javascript = client.get("/app/assets/partizan.v1.js")
    operator_js = client.get("/app/assets/operator-auth.v1.js")
    operator_css = client.get("/app/assets/operator-auth.v1.css")
    execution_css = client.get("/app/assets/execution.v1.css")
    execution_js = client.get("/app/assets/execution.v1.js")
    execution_bootstrap = client.get("/app/assets/execution.v2.js")
    paid_control = client.get("/app/assets/paid-control.v1.js")

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

    assert operator_js.status_code == 200
    assert operator_css.status_code == 200
    assert 'const OPERATOR_HEADER = "X-Partizan-Operator-Key"' in operator_js.text
    assert 'const GLOBAL_INPUT_ID = "global-operator-key"' in operator_js.text
    assert 'const EXECUTION_INPUT_ID = "operator-key"' in operator_js.text
    assert 'url.pathname.startsWith("/v1/")' in operator_js.text
    assert "url.origin === window.location.origin" in operator_js.text
    assert "syncInputs(input, executionInput)" in operator_js.text
    assert "syncInputs(executionInput, input)" in operator_js.text
    assert "localStorage" not in operator_js.text
    assert "sessionStorage" not in operator_js.text
    assert ".global-operator-access" in operator_css.text

    assert execution_css.status_code == 200
    assert ".execution-drawer" in execution_css.text
    assert ".receipt-outcome.STAGED" in execution_css.text

    assert execution_js.status_code == 200
    assert "partizan.execution.v1" in execution_js.text
    for api_contract in (
        "/actions/auto-prepare",
        "/v1/distribution-actions/",
        "/approve",
        "/execute",
    ):
        assert api_contract in execution_js.text
    assert "PAUSED/DISABLE" in execution_js.text
    assert "Расход не запускается" in execution_js.text

    assert execution_bootstrap.status_code == 200
    assert "/app/assets/execution.v1.js" in execution_bootstrap.text
    assert "/app/assets/results.v1.js" in execution_bootstrap.text

    assert paid_control.status_code == 200
    assert "/v1/ops/paid-control/lifecycle/" in paid_control.text


def test_operator_key_is_runtime_only_and_not_browser_persisted() -> None:
    html = client.get("/app").text
    base_js = client.get("/app/assets/partizan.v1.js").text
    auth_js = client.get("/app/assets/operator-auth.v1.js").text
    execution_js = client.get("/app/assets/execution.v1.js").text
    combined = html + base_js + auth_js + execution_js

    assert "OPERATOR_API_KEY=" not in combined
    assert "META_ORACLE_ACCESS_TOKEN" not in combined
    assert "TIKTOK_ORACLE_ACCESS_TOKEN" not in combined
    assert "access_token=" not in combined.lower()

    assert 'const OPERATOR_HEADER = "X-Partizan-Operator-Key"' in auth_js
    assert "global-operator-key" in auth_js
    assert "localStorage" not in auth_js
    assert "sessionStorage" not in auth_js
    assert 'const OPERATOR_HEADER = "X-Partizan-Operator-Key"' in execution_js
    assert 'let operatorKey = ""' in execution_js
    assert "operatorKey:" not in execution_js
    assert "state.operatorKey" not in execution_js
    assert "execution.operatorKey" not in execution_js
    assert "localStorage" not in execution_js
    assert "JSON.stringify({\n        productId: execution.productId" in execution_js


def test_workspace_operator_bootstrap_covers_internal_api_only() -> None:
    javascript = client.get("/app/assets/operator-auth.v1.js").text

    assert "window.fetch = function partizanAuthenticatedFetch" in javascript
    assert 'url.pathname.startsWith("/v1/")' in javascript
    assert "url.origin === window.location.origin" in javascript
    assert "if (!isInternalApi(input)) return nativeFetch(input, init);" in javascript
    assert "headers.set(OPERATOR_HEADER, key)" in javascript
    assert "syncInputs(input, executionInput)" in javascript
    assert "syncInputs(executionInput, input)" in javascript


def test_execution_retry_ui_respects_backend_reconciliation_boundary() -> None:
    javascript = client.get("/app/assets/execution.v1.js").text

    assert 'new Set(["FAILED", "UNAVAILABLE", "IN_PROGRESS"])' in javascript
    assert "partial_provider_ids" in javascript
    assert "requires_reconciliation" in javascript
    assert "STAGED" not in javascript.split("RETRYABLE_OUTCOMES", 1)[1].split(";", 1)[0]


def test_unknown_workspace_asset_is_not_exposed() -> None:
    response = client.get("/app/assets/../../config.py")

    assert response.status_code == 404


def test_unknown_marketing_asset_is_not_exposed() -> None:
    response = client.get("/site/assets/../../config.py")

    assert response.status_code == 404


def test_existing_health_endpoint_remains_available() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
