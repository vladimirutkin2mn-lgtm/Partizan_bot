from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "self-dogfood-bootstrap.yml"
COMPOSE = ROOT / "docker-compose.prod.yml"


def test_bootstrap_workflow_is_manual_and_main_only() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in source
    assert "workflow_run:" not in source
    assert "github.ref == 'refs/heads/main'" in source
    assert "Refuse stale main checkout" in source
    assert "environment: production" in source
    assert "contents: read" in source


def test_bootstrap_workflow_only_creates_internal_product_and_reports() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "python -m app.self_dogfood_bootstrap" in source
    assert "python -m app.self_dogfood" in source
    assert "partizan-growth-run" not in source
    assert "distribution-actions" not in source
    assert "mark-executed" not in source
    assert "publish" not in source.lower()
    assert "--require-proof" not in source
    assert "cat .env.prod" not in source
    assert "printenv" not in source


def test_production_compose_has_stable_self_dogfood_identity_with_override() -> None:
    source = COMPOSE.read_text(encoding="utf-8")

    assert (
        "PARTIZAN_SELF_DOGFOOD_PRODUCT_ID: "
        "${PARTIZAN_SELF_DOGFOOD_PRODUCT_ID:-eb8f160f-1b85-5f1b-a4f0-18038f63ac34}"
    ) in source
