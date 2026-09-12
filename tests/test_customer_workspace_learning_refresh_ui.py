from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_customer_workspace_refreshes_learning_after_community_action_changes() -> None:
    response = client.get("/workspace")
    assert response.status_code == 200
    html = response.text

    assert "const renderLearning = (data) =>" in html
    assert "const refreshLearning = async () =>" in html
    assert "/customer/workspace/${encodeURIComponent(projectId)}" in html
    assert "overview.running_experiments" in html
    assert "overview.waiting_experiments" in html
    assert "overview.recent_decisions" in html
    assert "refreshLearning().catch(() => {})" in html
    assert "#community-action-inbox" in html


def test_learning_refresh_remains_read_only() -> None:
    response = client.get("/workspace")
    assert response.status_code == 200
    html = response.text
    learning_block = html.split("const renderLearning = (data) =>", 1)[1].split("const refresh = async", 1)[0]

    assert "method: 'POST'" not in learning_block
    assert "method: 'PUT'" not in learning_block
    assert "method: 'DELETE'" not in learning_block
    assert "/publish" not in learning_block
    assert "/observe" not in learning_block
