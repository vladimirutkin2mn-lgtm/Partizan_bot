from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_outreach_autosend_workspace_assets_are_served() -> None:
    css = client.get("/app/assets/outreach-autosend.v1.css")
    javascript = client.get("/app/assets/outreach-autosend.v1.js")

    assert css.status_code == 200
    assert ".outreach-autosend-panel" in css.text
    assert javascript.status_code == 200
    assert "Autonomous Outreach" in javascript.text
    assert "outreach-autosend-panel" in javascript.text


def test_execution_bootstrap_loads_autosend_after_founder_outreach() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert 'script.src = versionedAsset("/app/assets/outreach.v1.js")' in javascript
    assert 'script.addEventListener("load", loadOutreachAutosendAssets)' in javascript
    assert 'script.src = "/app/assets/outreach-autosend.v1.js"' in javascript
    assert "/app/assets/outreach-autosend.v1.css" in javascript


def test_browser_can_manage_delegation_but_cannot_trigger_send() -> None:
    javascript = client.get("/app/assets/outreach-autosend.v1.js").text

    assert "/outreach-autosend/state" in javascript
    assert "/outreach-autosend/delegate" in javascript
    assert "/outreach-autosend/status" in javascript
    assert "/outreach-autosend/run-next" not in javascript
    assert "/send-authorizations" not in javascript
    assert "/send-attempt" not in javascript
    assert "confirm_autonomous_initial_send" in javascript
    assert "Follow-up: 0" in javascript


def test_browser_reuses_live_operator_key_without_persisting_it() -> None:
    javascript = client.get("/app/assets/outreach-autosend.v1.js").text

    assert '#autonomy-operator-key' in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage.setItem" not in javascript


def test_autosend_state_is_fail_closed_without_delegation() -> None:
    product_id = uuid4()

    response = client.get(f"/v1/products/{product_id}/outreach-autosend/state")

    assert response.status_code == 200
    payload = response.json()
    assert payload["product_id"] == str(product_id)
    assert payload["delegation"] is None
    assert payload["valid"] is False
    assert payload["blockers"] == ["Outreach auto-send is not delegated"]
