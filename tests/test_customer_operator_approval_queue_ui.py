from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_operator_shell_exposes_request_bound_customer_approval_queue() -> None:
    javascript = client.get("/app/assets/operator-auth.v1.js")
    stylesheet = client.get("/app/assets/operator-auth.v1.css")

    assert javascript.status_code == 200
    assert stylesheet.status_code == 200

    js = javascript.text
    css = stylesheet.text

    for contract in (
        'const APPROVAL_QUEUE_ID = "customer-approval-queue"',
        'trigger.textContent = "Customer approvals"',
        'fetch("/v1/customer-execution-requests")',
        "/approve-action",
        'method: "POST"',
        "JSON.stringify({ confirm_approval: true })",
        'item.status === "PUBLISH_CONFIRMED"',
        'item.status === "OPERATOR_APPROVED"',
        "request.customer_publish_confirmation_fingerprint",
        "request.customer_publish_confirmed_at",
        "request.distribution_action_id",
        'createFact("Locked target", request.source_url, { url: true })',
        'createExactBlock("Exact context", request.context_text)',
        'createExactBlock("Exact content", request.content_text)',
        "Execution remains separate",
    ):
        assert contract in js

    assert ".operator-approval-drawer" in css
    assert ".operator-approval-card.is-pending" in css
    assert ".operator-approval-fingerprint" in css


def test_customer_approval_queue_cannot_edit_or_execute_locked_action() -> None:
    javascript = client.get("/app/assets/operator-auth.v1.js").text

    assert "/v1/distribution-actions/" not in javascript
    assert "/execute" not in javascript
    assert 'method: "PATCH"' not in javascript
    assert 'method: "PUT"' not in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage" not in javascript

    assert 'url.pathname.startsWith("/v1/")' in javascript
    assert 'headers.set(OPERATOR_HEADER, key)' in javascript
    assert "window.confirm(" in javascript
    assert "Backend повторно сверит request/action binding" in javascript


def test_customer_approval_queue_is_part_of_versioned_operator_asset() -> None:
    response = client.get("/app")

    assert response.status_code == 200
    revision = response.headers["x-partizan-app-revision"]
    html = response.text
    assert f"/app/assets/operator-auth.v1.js?v={revision}" in html
    assert "no-store" in response.headers["cache-control"]
