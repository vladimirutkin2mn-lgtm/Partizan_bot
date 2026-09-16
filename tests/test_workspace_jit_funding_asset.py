from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "app" / "web" / "workspace.jit-funding.v1.js"
ROUTES = ROOT / "app" / "web_routes.py"
JIT_ROUTES = ROOT / "app" / "growth_balance_jit_routes.py"


def test_controller_loads_server_selected_recommendation_without_client_budget_math() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "/recommended-action`" in source
    assert "/recommended-action/paid-proposal`" in source
    checkout_route = (
        "/recommended-action/paid-proposals/"
        "${encodeURIComponent(pendingProposal.proposal_id)}/checkout"
    )
    assert checkout_route in source
    assert "{ method: 'POST' }" in source
    assert "body:" not in source
    assert "amount_usd:" not in source
    assert "estimated_cost_max_usd" not in source
    assert "recommendedTargetUrl = recommendation.target_url" in source


def test_controller_requires_exact_amount_review_before_checkout() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "if (pendingProposal) {" in source
    assert "reviewProposal(proposal, currentProjectId);" in source
    assert "Add only ${money(proposal.topup_amount_usd)}" in source
    assert "only ${money(proposal.topup_amount_usd)} needs to be added" in source
    assert "execution still requires separate approval" in source


def test_controller_keeps_manual_action_manual_and_paid_flow_fail_closed() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "recommendationMode === 'manual'" in source
    assert "window.open(recommendedTargetUrl" in source
    assert "stopImmediatePropagation" in source
    assert "error.status === 409 && resolvingProposal" in source
    assert "current ranked move no longer needs paid funding" in source
    assert "/execute" not in source
    assert "/approve" not in source
    assert "/distribution-actions" not in source


def test_controller_discards_stale_recommendation_response_after_project_change() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "let recommendationGeneration = 0;" in source
    assert "const generation = ++recommendationGeneration;" in source
    assert "generation !== recommendationGeneration" in source
    assert "activeProjectId !== nextProjectId" in source


def test_recommended_action_get_is_read_only_and_checkout_is_proposal_bound() -> None:
    source = JIT_ROUTES.read_text(encoding="utf-8")

    assert '@router.get(\n    "/customer/workspace/{project_id}/recommended-action"' in source
    assert (
        '"/customer/workspace/{project_id}/recommended-action/paid-proposal"'
        in source
    )
    assert (
        '"/customer/workspace/{project_id}/recommended-action/paid-proposals/'
        '{proposal_id}/checkout"' in source
    )
    get_section = source.split("def get_recommended_action", 1)[1].split("@router.post", 1)[0]
    assert "prepare_checkout" not in get_section
    assert "mark_checkout_pending" not in get_section
    assert "create_growth_balance_checkout" not in get_section


def test_workspace_serves_versioned_jit_funding_controller() -> None:
    source = ROUTES.read_text(encoding="utf-8")

    assert '"workspace.jit-funding.v1.js": "text/javascript; charset=utf-8"' in source
    assert "_WORKSPACE_JIT_FUNDING_SCRIPT" in source
    assert "workspace.jit-funding.v1.js" in source
    assert "*_CUSTOMER_WORKSPACE_ASSETS" in source
