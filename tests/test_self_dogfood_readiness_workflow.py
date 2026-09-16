from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "self-dogfood-readiness.yml"


def test_self_dogfood_readiness_runs_after_successful_production_deploy() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert 'workflows: ["Deploy production"]' in source
    assert "workflow_dispatch:" in source
    assert "github.event.workflow_run.conclusion == 'success'" in source
    assert "github.event.workflow_run.head_branch == 'main'" in source
    assert "Refuse stale production release" in source


def test_self_dogfood_readiness_is_read_only_snapshot() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "python -m app.self_dogfood" in source
    assert "--require-proof" not in source
    assert "contents: read" in source
    assert "docker compose" in source
    assert "exec -T api" in source
    assert "partizan-growth-run" not in source
    assert "distribution-actions" not in source
    assert "execute" not in source.lower()
    assert "publish" not in source.lower()


def test_self_dogfood_readiness_does_not_print_production_env() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")

    assert "cat .env.prod" not in source
    assert "printenv" not in source
    assert "env |" not in source
