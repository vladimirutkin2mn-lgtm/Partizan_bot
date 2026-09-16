from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSET = ROOT / "app" / "web" / "workspace.jit-funding.v1.js"
ROUTES = ROOT / "app" / "web_routes.py"


def test_jit_funding_controller_uses_server_derived_paid_proposal() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "/growth-balance/paid-proposal`" in source
    assert "/growth-balance/paid-proposal/${encodeURIComponent(pendingProposal.proposal_id)}/checkout" in source
    assert "amount_usd" not in source
    assert "stopImmediatePropagation" in source
    assert "}, true);" in source


def test_jit_funding_controller_requires_exact_amount_review_before_checkout() -> None:
    source = ASSET.read_text(encoding="utf-8")

    review_index = source.index("reviewProposal(proposal, currentProjectId)")
    checkout_index = source.index("pendingProposal.proposal_id")
    assert review_index > checkout_index
    assert "Add only ${money(proposal.topup_amount_usd)}" in source
    assert "only ${money(proposal.topup_amount_usd)} needs to be added" in source
    assert "Funding does not start spend." in source


def test_jit_funding_controller_fails_closed_for_research_only_preview() -> None:
    source = ASSET.read_text(encoding="utf-8")

    assert "No executable paid move is ready yet" in source
    assert "research-only preview" in source
    assert "/execute" not in source
    assert "/approve" not in source
    assert "/distribution-actions" not in source


def test_workspace_serves_versioned_jit_funding_controller() -> None:
    source = ROUTES.read_text(encoding="utf-8")

    assert '"workspace.jit-funding.v1.js": "text/javascript; charset=utf-8"' in source
    assert "_WORKSPACE_JIT_FUNDING_SCRIPT" in source
    assert "workspace.jit-funding.v1.js" in source
    assert "*_CUSTOMER_WORKSPACE_ASSETS" in source
