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


def test_overview_channels_use_simple_toggles_and_real_meta_connection_state() -> None:
    javascript = _channel_javascript()

    assert "channel.mode !== 'OFF'" in javascript
    assert "channel.execution_ready ? 'AUTO' : 'RESEARCH_ONLY'" in javascript
    assert "Paid execution ready" in javascript
    assert "Paid execution supported" not in javascript
    assert "channel.execution_blocker" in javascript
    assert "channel.platform === 'INSTAGRAM' && !channel.connected" in javascript
    assert 'data-channel-connect="INSTAGRAM"' in javascript
    assert 'class="channel-toggle"' in javascript
    assert "mode = toggle.checked ? toggle.dataset.onMode : 'OFF'" in javascript


def test_manage_channel_view_is_detailed_and_read_only() -> None:
    javascript = _channel_javascript()

    assert "Channel details" in javascript
    assert "Compare connection status, spend and results by channel" in javascript
    assert "Channel details are read-only here" in javascript
    assert "channel-detail-status" in javascript
    assert "channel-mode-select" not in javascript
