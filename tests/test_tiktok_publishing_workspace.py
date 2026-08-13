from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_publishing_workspace_assets_are_served() -> None:
    css = client.get("/app/assets/publishing.v1.css")
    javascript = client.get("/app/assets/publishing.v1.js")

    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert ".publishing-panel" in css.text

    assert javascript.status_code == 200
    assert "javascript" in javascript.headers["content-type"]
    assert "publishing-panel" in javascript.text
    assert "Разрешение на публикацию" in javascript.text


def test_execution_bootstrap_loads_publishing_after_creative_lab() -> None:
    javascript = client.get("/app/assets/execution.v2.js").text

    assert 'script.src = "/app/assets/creative.v1.js"' in javascript
    assert 'script.addEventListener("load", loadPublishingAssets)' in javascript
    assert 'script.src = "/app/assets/publishing.v1.js"' in javascript
    assert "/app/assets/publishing.v1.css" in javascript
    assert "else {\n      loadPublishingAssets();\n    }" in javascript


def test_publishing_workspace_uses_exact_permissioned_backend_contracts() -> None:
    javascript = client.get("/app/assets/publishing.v1.js").text

    for contract in (
        "/creative-assets`",
        "/creative-readiness`",
        "/owned-publishing/tiktok`",
        "/preflight`",
        "/authorization`",
        "/authorization/revoke`",
        "/direct-post`",
        "/direct-post/reconciliation`",
        "/direct-post/reconcile`",
    ):
        assert contract in javascript

    for field in (
        "privacy_level_options",
        "comment_disabled",
        "duet_disabled",
        "stitch_disabled",
        "commercial_content_enabled",
        "brand_organic_toggle",
        "brand_content_toggle",
        "branded_content_policy_accepted",
        "music_usage_confirmation_accepted",
        "explicit_publish_consent",
        "is_aigc",
        "provider_publish_id",
        "public_post_ids",
    ):
        assert field in javascript


def test_publishing_workspace_does_not_publish_directly_from_browser() -> None:
    javascript = client.get("/app/assets/publishing.v1.js").text

    assert 'api(`${base}/direct-post`, { method: "POST"' not in javascript
    assert 'api(`${base}/direct-post`, {' not in javascript
    assert 'api(`${base}/direct-post/reconcile`, { method: "POST" })' in javascript
    assert "Autonomous worker продолжит публикацию" in javascript
    assert "Partizan не будет публиковать повторно" in javascript


def test_publishing_workspace_requires_explicit_privacy_and_consent() -> None:
    javascript = client.get("/app/assets/publishing.v1.js").text

    assert 'empty.value = ""' in javascript
    assert "empty.selected = true" in javascript
    assert "select.required = true" in javascript
    assert 'checkbox("allow_comment", "Разрешить комментарии", false' in javascript
    assert 'checkbox("allow_duet", "Разрешить Duet", false' in javascript
    assert 'checkbox("allow_stitch", "Разрешить Stitch", false' in javascript
    assert "Music Usage Confirmation" in javascript
    assert "явное согласие" in javascript
    assert 'form.elements.music_usage_confirmation_accepted.checked' in javascript
    assert 'form.elements.explicit_publish_consent.checked' in javascript


def test_generated_video_is_disclosed_as_aigc_in_authorization_payload() -> None:
    javascript = client.get("/app/assets/publishing.v1.js").text

    assert 'asset.source === "GENERATED"' in javascript
    assert 'form.dataset.generatedAsset === "1" || form.elements.is_aigc.checked' in javascript
    assert "Пометить как AI-generated content" in javascript


def test_publishing_workspace_reuses_live_operator_key_without_persisting_it() -> None:
    javascript = client.get("/app/assets/publishing.v1.js").text

    assert '#autonomy-operator-key' in javascript
    assert "X-Partizan-Operator-Key" in javascript
    assert "localStorage" not in javascript
    assert "sessionStorage.setItem" not in javascript
    assert "dataset.operatorKey" not in javascript


def test_publishing_workspace_has_no_paid_or_budget_mutations() -> None:
    javascript = client.get("/app/assets/publishing.v1.js").text

    for forbidden in (
        "/paid-campaign/activate",
        "/activation-authorizations",
        "/paid-campaign/meta/pause",
        "/paid-campaign/tiktok/pause",
        "/spend",
        "/growth-mandate",
    ):
        assert forbidden not in javascript
