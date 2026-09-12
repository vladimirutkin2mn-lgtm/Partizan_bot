from pathlib import Path

WORKSPACE_HTML = Path("app/web/workspace.v1.html").read_text(encoding="utf-8")
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


def test_canonical_workspace_refresh_fans_out_to_read_only_surfaces() -> None:
    event = "partizan:workspace-ready"

    assert f"new CustomEvent('{event}'" in WORKSPACE_JS
    assert f"window.addEventListener('{event}'" in EXPERIMENTS_JS
    assert f"window.addEventListener('{event}'" in WORKSPACE_HTML


def test_customer_results_do_not_install_a_second_community_observer() -> None:
    assert "const communityObserver = new MutationObserver" not in WORKSPACE_HTML
    assert "communityObserver.observe" not in WORKSPACE_HTML
    assert "const refreshLearning = async" not in WORKSPACE_HTML
    assert "const renderLearning = (data) =>" not in WORKSPACE_HTML
    assert "const renderOverviewSnapshot = (data) =>" not in WORKSPACE_HTML
