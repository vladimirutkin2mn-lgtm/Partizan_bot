from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_customer_workspace_refreshes_overview_metrics_after_community_changes() -> None:
    response = client.get("/workspace")
    assert response.status_code == 200
    html = response.text

    assert "const renderOverviewSnapshot = (data) =>" in html
    assert "renderOverviewSnapshot(data);" in html
    assert "balance.acquisition_spend_usd" in html
    assert "overview.paid_customers" in html
    assert "overview.cac_usd" in html
    assert "overview.revenue_usd" in html
    assert "balance.management_fee_usd" in html
    assert "overview.running_experiments" in html
    assert "overview.waiting_experiments" in html
    assert "current-work" in html
    assert "work-state" in html


def test_overview_refresh_remains_customer_read_only() -> None:
    response = client.get("/workspace")
    assert response.status_code == 200
    html = response.text
    overview_block = html.split("const renderOverviewSnapshot = (data) =>", 1)[1]
    overview_block = overview_block.split("const refreshLearning = async", 1)[0]

    assert "method: 'POST'" not in overview_block
    assert "method: 'PUT'" not in overview_block
    assert "method: 'DELETE'" not in overview_block
    assert "/publish" not in overview_block
    assert "/observe" not in overview_block
