"""Customer Activity screen must expose safe, read-only Telegram discovery detail."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _asset() -> str:
    return (
        ROOT / "app" / "web" / "workspace.live-opportunities.v1.js"
    ).read_text(encoding="utf-8")


def test_activity_includes_discovery_progress_before_live_opportunities() -> None:
    source = _asset()
    assert "How Partizan is finding opportunities" in source
    assert "const ensureDiscoveryPanel =" in source
    assert "const renderDiscovery =" in source
    assert "activity.insertBefore(panel, livePanel)" in source
    assert "/telegram-discovery" in source
    assert "loadDiscovery(projectId)" in source
    assert "No Telegram discovery run recorded" in source


def test_discovery_ui_is_read_only_and_escapes_untrusted_provider_text() -> None:
    source = _asset()
    start = source.index("const renderDiscovery =")
    end = source.index("const freshnessLabel =", start)
    section = source[start:end]
    assert "escapeHtml(stage.label)" in section
    assert "escapeHtml(stage.detail" in section
    assert "escapeHtml(stage.search_query)" in section
    assert "escapeHtml(stage.source)" in section
    assert "escapeHtml(next)" in section
    assert "stage.action_target_url" not in section
    assert "publishability" not in section
    assert "joinTelegram" not in section
    assert "joinCommunity" not in section
    assert "fetch(" not in section
    assert "send(" not in section
    assert "publish(" not in section


def test_project_switch_and_session_loss_do_not_expose_previous_project() -> None:
    source = _asset()
    start = source.index("const loadDiscovery =")
    end = source.index("const load = async () =>", start)
    section = source[start:end]
    assert "credentials: 'same-origin'" in section
    assert "if (currentProjectId() !== projectId) return;" in section
    assert "response.status === 401" in section
    assert "response.status === 403" in section
    assert "panel.classList.add('hidden')" in section
    assert "temporarily unavailable" in section
