from pathlib import Path


def _workflow() -> str:
    return Path(".github/workflows/community-distribution-acceptance.yml").read_text(
        encoding="utf-8"
    )


def test_community_distribution_acceptance_runs_manually_or_after_deploy() -> None:
    workflow = _workflow()

    assert "workflow_dispatch:" in workflow
    assert "workflow_run:" in workflow
    assert 'workflows: ["Deploy production"]' in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.ref == 'refs/heads/main'" in workflow


def test_community_distribution_acceptance_uses_exact_current_release() -> None:
    workflow = _workflow()

    assert "github.event.workflow_run.head_sha" in workflow
    assert "ref: ${{ steps.release.outputs.sha }}" in workflow
    assert "git ls-remote origin refs/heads/main" in workflow
    assert "Skipping stale acceptance report" in workflow
    assert 'echo "acceptance_allowed=true" >> "$GITHUB_OUTPUT"' in workflow
    assert workflow.count("steps.freshness.outputs.acceptance_allowed == 'true'") >= 5


def test_community_distribution_acceptance_remains_read_only() -> None:
    workflow = _workflow()

    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "issues: write" not in workflow
    assert "report_community_distribution_acceptance.py" in workflow

    forbidden = (
        "docker compose up",
        "docker compose restart",
        "docker restart",
        "caddy reload",
        "/publish",
        "curl -X POST",
        "curl --request POST",
        "curl -X PUT",
        "curl -X DELETE",
    )
    assert all(command not in workflow for command in forbidden)
