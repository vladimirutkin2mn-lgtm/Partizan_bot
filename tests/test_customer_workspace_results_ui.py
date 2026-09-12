from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def _workspace_html() -> str:
    response = client.get("/workspace")
    assert response.status_code == 200
    return response.text


def _results_refresh_script(html: str) -> str:
    marker = "const getJson = async (path) => {"
    assert marker in html
    return html.split(marker, 1)[1]


def test_customer_workspace_exposes_project_scoped_distribution_results() -> None:
    html = _workspace_html()

    assert 'id="distribution-results-card"' in html
    assert 'id="customer-economics"' in html
    assert 'id="managed-delivery"' in html
    assert "/customer/workspace/${projectId}/distribution-economics" in html
    assert "/customer/workspace/${projectId}/managed-distribution/assignments" in html
    assert "Customer cost" in html
    assert "Research fee" in html
    assert "Execution fee" in html
    assert "Distribution spend" in html
    assert "Management fee" in html
    assert "Paid customers" in html
    assert "ROAS" in html


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


def test_customer_workspace_results_do_not_mutate_distribution_execution() -> None:
    html = _workspace_html()
    results_script = _results_refresh_script(html)

    assert "Promise.allSettled" in results_script
    assert "method: 'POST'" not in results_script
    assert "method: 'PUT'" not in results_script
    assert "method: 'DELETE'" not in results_script
    assert "/fulfill" not in results_script
    assert "/release" not in results_script


def test_customer_workspace_results_refresh_from_canonical_workspace_ready() -> None:
    html = _workspace_html()

    assert "window.addEventListener('partizan:workspace-ready'" in html
    assert "refresh(true).catch(() => {})" in html
    assert "const communityObserver = new MutationObserver" not in html
    assert "communityObserver.observe" not in html


def test_customer_workspace_results_replay_forced_refresh_after_inflight_request() -> None:
    html = _workspace_html()
    results_script = _results_refresh_script(html)

    assert "let queuedForceRefresh = false" in html
    assert "if (loading)" in results_script
    assert "if (force) queuedForceRefresh = true" in results_script
    assert "if (queuedForceRefresh)" in results_script
    assert "window.queueMicrotask(() => refresh(true).catch(() => {}))" in results_script
