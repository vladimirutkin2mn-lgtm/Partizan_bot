from pathlib import Path


WORKSPACE_HTML = Path("app/web/workspace.v1.html").read_text(encoding="utf-8")
WORKSPACE_JS = Path("app/web/workspace.v1.js").read_text(encoding="utf-8")


def test_persistent_channel_mutations_emit_workspace_state_updates_only_after_success() -> None:
    html = WORKSPACE_HTML

    assert "const nativeFetch = window.fetch.bind(window)" in html
    assert "if (!mutation || !response.ok) return response" in html
    assert "method === 'PUT' && tail === 'channels'" in html
    assert "method === 'DELETE' && ['telegram/connection', 'reddit/connection'].includes(tail)" in html
    assert "method === 'POST' && tail === 'telegram/connection/confirm'" in html
    assert "payload?.status === 'ACTIVE'" in html
    assert "partizan:workspace-state-updated" in html


def test_reddit_oauth_success_replays_canonical_workspace_invalidation() -> None:
    html = WORKSPACE_HTML

    assert "redditConnectedOnLoad" in html
    assert "redditProjectIdOnLoad" in html
    assert "notifyWorkspaceStateUpdated(redditProjectIdOnLoad, 'reddit-connect')" in html
    assert "DOMContentLoaded" in html


def test_workspace_state_updates_reuse_the_existing_canonical_refresh_seam() -> None:
    html = WORKSPACE_HTML

    assert "partizan:workspace-state-updated" in html
    assert "partizan:community-action-updated" in html
    assert "window.addEventListener('partizan:community-action-updated'" in WORKSPACE_JS
    assert "refreshWorkspaceWithoutResearch().catch" in WORKSPACE_JS


def test_transient_connection_steps_and_autoresearch_status_do_not_invalidate_workspace() -> None:
    html = WORKSPACE_HTML
    bridge = html.split("const persistentMutation =", 1)[1].split("window.fetch =", 1)[0]

    assert "telegram/connection/start" not in bridge
    assert "autoresearch/status" not in bridge
    assert "meta/connect" not in bridge
