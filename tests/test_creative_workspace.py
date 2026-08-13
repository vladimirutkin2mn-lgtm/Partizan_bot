from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_creative_workspace_assets_are_served() -> None:
    css = client.get("/app/assets/creative.v1.css")
    javascript = client.get("/app/assets/creative.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".creative-panel" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "creative-panel" in javascript.text
    assert "Что Partizan собирается показать людям" in javascript.text


def test_execution_bootstrap_loads_creative_workspace_after_autonomy() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert "/app/assets/autonomy.v1.js" in javascript
    assert "/app/assets/creative.v1.css" in javascript
    assert "/app/assets/creative.v1.js" in javascript
    assert 'script.addEventListener("load", loadCreativeAssets)' in javascript


def test_creative_workspace_reads_provider_readiness_and_safe_asset_contracts() -> None:
    javascript = client.get("/app/assets/creative.v1.js").text

    for contract in (
        "/creative-assets`",
        "/creative-readiness`",
        "/creative-generate`",
        "/retire`",
        "/autonomy-overview?timeline_limit=5",
    ):
        assert contract in javascript

    for field in (
        "selected_asset",
        "public_url",
        "provider_asset_id",
        "media_type",
        "provenance",
        "message_hook",
        "value_proposition",
        "constraints",
    ):
        assert field in javascript


def test_creative_workspace_reuses_page_memory_operator_key_without_persisting_it() -> None:
    javascript = client.get("/app/assets/creative.v1.js").text

    assert '#autonomy-operator-key' in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage.setItem" not in javascript
    assert "dataset.operatorKey" not in javascript


def test_creative_workspace_can_regenerate_only_after_retiring_current_asset() -> None:
    javascript = client.get("/app/assets/creative.v1.js").text

    assert 'action === "regenerate" && assetId' in javascript
    retire_pos = javascript.index("/retire`, { method: \"POST\" }")
    generate_pos = javascript.index("/creative-generate`, {", retire_pos)
    assert retire_pos < generate_pos
    assert "Перегенерировать" in javascript
    assert "Убрать креатив" in javascript


def test_creative_workspace_cannot_start_or_mutate_paid_spend() -> None:
    javascript = client.get("/app/assets/creative.v1.js").text

    for forbidden in (
        "/paid-campaign/activate",
        "/activation-authorizations",
        "/paid-campaign/meta/pause",
        "/paid-campaign/tiktok/pause",
        "/spend",
        "/growth-mandate",
        "/autonomous-growth/sweep",
    ):
        assert forbidden not in javascript

    assert "Генерация не запускает рекламный spend" in javascript
