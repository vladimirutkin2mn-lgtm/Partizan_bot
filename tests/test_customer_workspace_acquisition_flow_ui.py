import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _workspace_asset(name: str) -> str:
    page = client.get("/workspace")
    assert page.status_code == 200
    match = re.search(
        rf'(/workspace/assets/{re.escape(name)}\?v=[a-f0-9]{{12}})',
        page.text,
    )
    assert match is not None
    response = client.get(match.group(1))
    assert response.status_code == 200
    return response.text


def test_workspace_replaces_six_step_activation_with_research_then_channel_choice() -> None:
    javascript = _workspace_asset("workspace.projects.v1.js")
    stylesheet = _workspace_asset("workspace.projects.v1.css")

    assert "Understand your product" in javascript
    assert "Find where customers are" in javascript
    assert "Choose where to start" in javascript
    assert "Research is ready. Choose where to start." in javascript
    assert "Where do you want Partizan to help you acquire customers?" in javascript
    assert "Channel selected" in javascript
    assert ".activation-card .activation-list{display:none!important}" in stylesheet
    assert "acquisition-path-stages" in stylesheet


def test_channel_choice_is_persisted_without_reusing_execution_permission_controls() -> None:
    javascript = _workspace_asset("workspace.projects.v1.js")

    assert "/channel-selection" in javascript
    assert (
        "body: JSON.stringify({ platform: button.dataset.selectAcquisitionChannel })"
        in javascript
    )
    assert (
        "Choosing a channel does not grant account access, execution permission "
        "or acquisition spend"
        in javascript
    )
    assert (
        "Connecting Meta grants account access only; it does not authorize spend "
        "or enable autonomous execution"
        in javascript
    )
    assert (
        "Research only until a customer-facing connection and execution path "
        "is production-ready"
        in javascript
    )
    assert "customer_token" not in javascript


def test_research_recommendation_inference_is_display_only() -> None:
    javascript = _workspace_asset("workspace.projects.v1.js")

    assert "inferResearchedChannel" in javascript
    assert "Recommended · researched" in javascript
    assert "Other channels remain choices, not research claims." in javascript
    assert "Research URLs are display evidence only. Unknown hosts stay unclassified." in javascript
    assert "t.me" in javascript
    assert "reddit.com" in javascript
    assert "instagram.com" in javascript
    assert "tiktok.com" in javascript


def test_paid_research_and_acquisition_budget_are_explicitly_separate() -> None:
    javascript = _workspace_asset("workspace.projects.v1.js")

    assert "Research access and acquisition budget are separate." in javascript
    assert "A larger paid research report can expand the market map" in javascript
    assert "choosing a channel never spends acquisition money" in javascript
