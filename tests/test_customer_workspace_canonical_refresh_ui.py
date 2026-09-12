from pathlib import Path

WORKSPACE_JS = Path("app/web/workspace.v1.js").read_text(encoding="utf-8")
EXPERIMENTS_JS = Path("app/web/workspace.experiments.v1.js").read_text(encoding="utf-8")


def test_community_inbox_changes_refresh_the_canonical_workspace_snapshot() -> None:
    block = WORKSPACE_JS.split("const installCommunityWorkspaceRefresh = () =>", 1)[1]
    block = block.split("const surfaceLabels =", 1)[0]

    assert "#community-action-inbox" in block
    assert "refreshWorkspaceWithoutResearch().catch" in block
    assert "observer.observe(communityActionSource, { childList: true, subtree: true });" in block
    assert "let communityActionSignature = null;" in block
    assert "if (nextSignature === communityActionSignature) return;" in block
    assert "communityActionSignature = nextSignature;" in block
    assert "method: 'POST'" not in block
    assert "method: 'PUT'" not in block
    assert "method: 'DELETE'" not in block


def test_canonical_workspace_refresh_fans_out_to_continuous_learning() -> None:
    assert "window.dispatchEvent(new CustomEvent('partizan:workspace-ready'" in WORKSPACE_JS
    assert "window.addEventListener('partizan:workspace-ready'" in EXPERIMENTS_JS
