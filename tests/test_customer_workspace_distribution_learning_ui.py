from pathlib import Path


LEARNING_JS = Path("app/web/workspace.learning.v1.js").read_text(encoding="utf-8")
WEB_ROUTES = Path("app/web_routes.py").read_text(encoding="utf-8")


def test_workspace_serves_distribution_learning_as_a_versioned_secondary_asset() -> None:
    assert '"workspace.learning.v1.js": "text/javascript; charset=utf-8"' in WEB_ROUTES
    assert "_WORKSPACE_LEARNING_SCRIPT" in WEB_ROUTES
    assert "/workspace/assets/workspace.learning.v1.js" in WEB_ROUTES
    assert "*_CUSTOMER_WORKSPACE_ASSETS" in WEB_ROUTES


def test_distribution_learning_renders_customer_safe_observed_decisions() -> None:
    js = LEARNING_JS

    assert "distribution-learning-card" in js
    assert "Why Partizan changed course" in js
    assert "SCALE" in js
    assert "CONTINUE" in js
    assert "MODIFY" in js
    assert "STOP" in js
    assert "observed_basis" in js
    assert "opportunity_url" in js
    assert "distribution_identity_id" not in js
    assert "tactic_id" not in js
    assert "operational_cost" not in js


def test_distribution_learning_uses_canonical_workspace_fanout_and_get_only_refresh() -> None:
    js = LEARNING_JS

    assert "window.addEventListener('partizan:workspace-ready'" in js
    assert "/distribution-learning" in js
    assert "credentials: 'same-origin'" in js
    assert "cache: 'no-store'" in js
    assert "refreshQueued" in js
    assert "method: 'POST'" not in js
    assert "method: 'PUT'" not in js
    assert "method: 'DELETE'" not in js
