from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_workspace_loads_versioned_jit_funding_after_experiments() -> None:
    response = client.get("/workspace")

    assert response.status_code == 200
    html = response.text
    assert "/workspace/assets/workspace.jit-funding.v1.js?v=" in html
    assert html.index("workspace.experiments.v1.js") < html.index(
        "workspace.jit-funding.v1.js"
    )


def test_jit_funding_asset_is_bound_to_waiting_paid_experiment() -> None:
    response = client.get("/workspace/assets/workspace.jit-funding.v1.js")

    assert response.status_code == 200
    script = response.text
    assert "partizan:workspace-ready" in script
    assert "waiting_experiments" in script
    assert "item.action_type === 'PAID_CAMPAIGN'" in script
    assert "/growth-balance/experiments/" in script
    assert "method: 'POST'" in script
    assert "amount_usd" not in script
    assert "preview_opportunity" not in script
    assert "estimated_cost" not in script
