from pathlib import Path


def test_community_distribution_acceptance_workflow_is_manual_and_read_only() -> None:
    workflow = Path(
        ".github/workflows/community-distribution-acceptance.yml"
    ).read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "issues: write" not in workflow

    forbidden = (
        "docker compose up",
        "docker compose restart",
        "docker restart",
        "caddy reload",
        "/publish",
        "curl -X POST",
        "curl --request POST",
    )
    assert all(command not in workflow for command in forbidden)
