from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_founder_outreach_workspace_assets_are_served() -> None:
    css = client.get("/app/assets/outreach.v1.css")
    javascript = client.get("/app/assets/outreach.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".outreach-panel" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "Founder Outreach" in javascript.text
    assert "outreach-panel" in javascript.text


def test_execution_bootstrap_loads_outreach_after_publishing() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert 'script.src = versionedAsset("/app/assets/publishing.v1.js")' in javascript
    assert 'script.addEventListener("load", loadOutreachAssets)' in javascript
    assert 'script.src = versionedAsset("/app/assets/outreach.v1.js")' in javascript
    assert "/app/assets/outreach.v1.css" in javascript
    assert "else {\n      loadOutreachAssets();\n    }" in javascript


def test_founder_outreach_workspace_uses_exact_review_contracts() -> None:
    javascript = client.get("/app/assets/outreach.v1.js").text

    for contract in (
        "/outreach-targets`",
        "/outreach/sender-readiness",
        "/outreach-policy`",
        "/briefs`",
        "/send-attempt`",
        "/distribution-experiments/${brief.experiment_id}/analytics`",
        "/suppress`",
        "/reject`",
        "/review`",
    ):
        assert contract in javascript

    for field in (
        "contact_evidence",
        "provenance_type",
        "relevance_rationale",
        "icp_overlap_rationale",
        "message_subject",
        "message_body_without_link",
        "tracking_url",
        "max_followups",
        "automatic_send_enabled",
        "RECONCILIATION_REQUIRED",
        "activated_users",
        "paid_users",
    ):
        assert field in javascript


def test_founder_outreach_workspace_never_sends_from_browser() -> None:
    javascript = client.get("/app/assets/outreach.v1.js").text

    assert "/send-authorizations" not in javascript
    assert "/outreach-send-authorizations" not in javascript
    attempt_view = javascript.split("/send-attempt")[1].split("/distribution-experiments")[0]
    assert 'method: "POST" })' not in attempt_view
    assert "Отправка из браузера намеренно отключена" in javascript
    assert "Review workspace не запускает отправку" in javascript


def test_founder_outreach_workspace_reuses_live_operator_key_without_persisting_it() -> None:
    javascript = client.get("/app/assets/outreach.v1.js").text

    assert '#autonomy-operator-key' in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage.setItem" not in javascript
    assert "dataset.operatorKey" not in javascript


def test_founder_outreach_workspace_has_no_autonomous_prepare_mutation() -> None:
    javascript = client.get("/app/assets/outreach.v1.js").text

    assert "/outreach-autonomy/prepare-next" not in javascript
    assert "/growth-mandate" not in javascript
    assert "/spend" not in javascript
