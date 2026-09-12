from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _workspace_html() -> str:
    response = client.get("/workspace")
    assert response.status_code == 200
    return response.text


def test_customer_workspace_exposes_project_scoped_distribution_results() -> None:
    html = _workspace_html()

    assert 'id="distribution-results-card"' in html
    assert 'id="customer-economics"' in html
    assert 'id="managed-delivery"' in html
    assert 'id="customer-learning"' in html
    assert "/customer/workspace/${projectId}/distribution-economics" in html
    assert "/customer/workspace/${projectId}/managed-distribution/assignments" in html
    assert "/customer/workspace/${projectId}/distribution-learning" in html
    assert "Customer cost" in html
    assert "Research fee" in html
    assert "Execution fee" in html
    assert "Distribution spend" in html
    assert "Management fee" in html
    assert "Paid customers" in html
    assert "ROAS" in html
    assert "What Partizan learned" in html
    assert "Scale next" in html
    assert "Stop this pattern" in html


def test_customer_workspace_managed_results_stay_read_only_and_customer_safe() -> None:
    html = _workspace_html()

    forbidden_contracts = (
        "/managed-distribution/publishers",
        "/managed-distribution/selection/preview",
        "/managed-distribution/assignments/${assignmentId}/fulfill",
        "managed_publisher_id",
        "distribution_identity_id",
        "internal_label",
        "partner_reference",
        "operational_cost_usd",
    )
    for value in forbidden_contracts:
        assert value not in html

    assert "Internal operating costs and publisher identities stay private." in html
    assert "Open delivered result" in html
    assert "credentials: 'same-origin'" in html


def test_customer_workspace_learning_uses_observed_exact_experiment_chain() -> None:
    html = _workspace_html()

    assert "No synthetic attribution is added here." in html
    assert "item.experiment_id" in html
    assert "item.observed_basis" in html
    assert "item.replies" in html
    assert "item.removals" in html
    assert "item.observed_cac" in html
    assert "item.decision" in html
    assert "Open opportunity" in html


def test_customer_workspace_results_do_not_mutate_distribution_execution() -> None:
    html = _workspace_html()

    assert "Promise.allSettled" in html
    assert "method: 'POST'" not in html
    assert "method: 'PUT'" not in html
    assert "method: 'DELETE'" not in html
    assert "/fulfill" not in html
    assert "/release" not in html


def test_customer_workspace_results_refresh_after_community_action_changes() -> None:
    html = _workspace_html()

    assert "communityActionSource" in html
    assert "community-action-inbox" in html
    assert "communityObserver.observe" in html
    assert "refresh(true)" in html
    assert "childList: true, subtree: true" in html
