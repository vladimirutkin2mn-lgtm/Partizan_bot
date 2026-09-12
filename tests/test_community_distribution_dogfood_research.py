from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_dogfood_research_runner_is_first_party_and_real_provider_only() -> None:
    runner = _text("tools/run_community_distribution_dogfood_research.py")

    assert "partizan_self_dogfood_product_id" in runner
    assert 'settings.app_env.strip().lower() != "production"' in runner
    assert 'settings.runtime_storage.strip().lower() != "database"' in runner
    assert 'settings.search_provider.strip().lower() == "mock"' in runner
    assert 'settings.telegram_research_provider.strip().lower() != "telethon"' in runner
    assert "telegram_research_public_ready" in runner
    assert "audience_intelligence_service.discover" in runner
    assert "DistributionPlatform.TELEGRAM" in runner
    assert "DistributionPlatform.REDDIT" in runner
    assert "opportunity_enrichment_service.enrich_product" in runner
    assert "1 <= max_reddit <= 5" in runner


def test_dogfood_research_runner_cannot_publish_or_execute() -> None:
    runner = _text("tools/run_community_distribution_dogfood_research.py")

    forbidden = (
        "distribution_execution_service",
        "customer_autopilot",
        "self_dogfood_service",
        "managed_distribution_service",
        "telegram_client",
        "reddit_client",
        "paid_activation",
        ".execute(",
        ".publish(",
    )
    assert all(value not in runner for value in forbidden)


def test_dogfood_research_workflow_is_one_shot_and_exact_release_guarded() -> None:
    workflow = _text(".github/workflows/community-distribution-dogfood-research.yml")

    assert "workflow_dispatch:" in workflow
    assert "workflow_run:" in workflow
    assert 'workflows: ["Community distribution acceptance"]' in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "git ls-remote origin refs/heads/main" in workflow
    assert "[community-dogfood-research]" in workflow
    assert "Automatic dogfood research marker is absent; skipping" in workflow
    assert 'echo "research_allowed=true" >> "$GITHUB_OUTPUT"' in workflow
    assert "--max-reddit 5" in workflow
    assert "report_community_distribution_acceptance.py" in workflow


def test_dogfood_research_workflow_has_no_github_write_or_service_restart() -> None:
    workflow = _text(".github/workflows/community-distribution-dogfood-research.yml")

    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "issues: write" not in workflow
    forbidden = (
        "docker restart",
        "docker compose restart",
        "caddy reload",
        "curl -X POST",
        "curl --request POST",
        "/publish",
    )
    assert all(value not in workflow for value in forbidden)
