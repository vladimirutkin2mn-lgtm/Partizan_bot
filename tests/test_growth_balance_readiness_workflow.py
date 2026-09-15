from pathlib import Path


def test_growth_balance_readiness_workflow_is_read_only_and_post_deploy() -> None:
    source = Path(".github/workflows/growth-balance-readiness.yml").read_text()

    assert 'workflows: ["Deploy production"]' in source
    assert "github.event.workflow_run.conclusion == 'success'" in source
    assert "github.event.workflow_run.head_branch == 'main'" in source
    assert "contents: read" in source
    assert "contents: write" not in source
    assert "actions: write" not in source
    assert "pull-requests: write" not in source
    assert "python -m app.growth_balance_readiness --pretty" in source
    assert "docker compose" in source
    assert " up " not in source
    assert " down " not in source
    assert " restart " not in source


def test_growth_balance_readiness_report_source_has_no_provider_mutation_calls() -> None:
    source = Path("app/growth_balance_readiness.py").read_text()

    assert ".funding_readiness(" in source
    assert ".provision_or_update(" not in source
    assert ".confirm_meta_binding(" not in source
    assert ".pause(" not in source
    assert ".activate(" not in source
    assert "Card.create" not in source
    assert "Card.modify" not in source
