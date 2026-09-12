import re

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _channel_javascript() -> str:
    page = client.get("/workspace")
    assert page.status_code == 200
    match = re.search(
        r'(/workspace/assets/workspace\.channels\.v1\.js\?v=[a-f0-9]{12})',
        page.text,
    )
    assert match is not None
    response = client.get(match.group(1))
    assert response.status_code == 200
    return response.text


def test_channels_render_backend_publisher_modes_and_capabilities() -> None:
    javascript = _channel_javascript()

    assert "channel.publisher_modes" in javascript
    assert "channel.capabilities" in javascript
    assert "channel.publisher_mode" in javascript
    assert "MANUAL" in javascript
    assert "CLIENT_OWNED" in javascript
    assert "PARTIZAN_MANAGED" in javascript
    assert "I'll do it myself" in javascript
    assert "Use my account" in javascript
    assert "Let Partizan handle it" in javascript
    assert "item.available ? '' : ' disabled'" in javascript
    assert 'class="channel-publisher-select"' in javascript
    assert "updateChannel(platform, { publisher_mode })" in javascript


def test_channels_expose_customer_owned_telegram_and_reddit_connections() -> None:
    javascript = _channel_javascript()

    assert "/telegram/connection/start" in javascript
    assert "/telegram/connection/confirm" in javascript
    assert "challenge_id: telegramChallenge.challenge_id" in javascript
    assert "PASSWORD_REQUIRED" in javascript
    assert "/reddit/connect" in javascript
    assert "payload.authorization_url" in javascript
    assert "data-disconnect-channel" in javascript
    assert "Publishing still requires an approved action" in javascript


def test_community_activation_routes_to_execution_choice_without_publishing() -> None:
    javascript = _channel_javascript()

    assert "platformFromOpportunity" in javascript
    assert "opportunity.surface !== 'COMMUNITY'" in javascript
    assert "hostname === 't.me'" in javascript
    assert "hostname === 'reddit.com'" in javascript
    assert "Choose how to execute this" in javascript
    assert "focusChannel(platform)" in javascript
    assert "/actions/" not in javascript
    assert "/publish" not in javascript


def test_channel_enablement_is_separate_from_execution_mode() -> None:
    javascript = _channel_javascript()

    assert "channel.mode !== 'OFF'" in javascript
    assert "channel.publisher_mode !== 'MANUAL' && canPublish(channel)" in javascript
    assert "mode = toggle.checked ? toggle.dataset.onMode : 'OFF'" in javascript
    assert "Connecting an account grants access only" in javascript
