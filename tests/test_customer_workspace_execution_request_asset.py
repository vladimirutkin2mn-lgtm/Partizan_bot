from fastapi.testclient import TestClient

from app.main import app


def test_workspace_loads_versioned_execution_request_asset() -> None:
    response = TestClient(app).get("/workspace")

    assert response.status_code == 200
    assert "/workspace/assets/workspace.execution-request.v1.js?v=" in response.text
    assert response.headers.get("X-Partizan-Workspace-Revision")


def test_execution_request_asset_is_served_no_store() -> None:
    response = TestClient(app).get(
        "/workspace/assets/workspace.execution-request.v1.js"
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert "Request one prepared action" in response.text
