from pathlib import Path

WORKSPACE_HTML = Path("app/web/workspace.v1.html").read_text(encoding="utf-8")
WORKSPACE_JS = Path("app/web/workspace.v1.js").read_text(encoding="utf-8")


def test_customer_workspace_canonical_renderer_owns_overview_metrics() -> None:
    block = WORKSPACE_JS.split("const renderWorkspace = (data) =>", 1)[1]
    block = block.split("const loadWorkspace = async () =>", 1)[0]

    assert "balance.acquisition_spend_usd" in block
    assert "overview.paid_customers" in block
    assert "overview.cac_usd" in block
    assert "overview.revenue_usd" in block
    assert "balance.management_fee_usd" in block
    assert "$('current-work')" not in block
    assert "renderActivity(overview);" in block
    assert "$('work-state').textContent" in block


def test_overview_does_not_have_a_second_snapshot_renderer() -> None:
    assert "const renderOverviewSnapshot = (data) =>" not in WORKSPACE_HTML
    assert "renderOverviewSnapshot(data);" not in WORKSPACE_HTML
