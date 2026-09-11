from pathlib import Path


def test_real_community_research_workflow_is_guarded_and_targeted() -> None:
    workflow = Path(
        ".github/workflows/community-research-real-acceptance.yml"
    ).read_text(encoding="utf-8")

    assert 'workflows: ["Deploy production"]' in workflow
    assert "github.event.workflow_run.conclusion == 'success'" in workflow
    assert "github.event.workflow_run.head_branch == 'main'" in workflow
    assert "github.event.workflow_run.head_sha" in workflow
    assert "git ls-remote origin refs/heads/main" in workflow
    assert "Skipping stale production research release" in workflow
    assert "contents: read" in workflow
    assert "contents: write" not in workflow
    assert "issues: write" not in workflow
    assert "run_community_research_acceptance.py" in workflow
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


def test_real_community_research_runner_is_bounded_and_has_no_publish_surface() -> None:
    runner = Path("tools/run_community_research_acceptance.py").read_text(
        encoding="utf-8"
    )

    assert "TelegramDiscoveryAdapter" in runner
    assert "RedditDiscoveryAdapter" in runner
    assert "default_platform_adapters" not in runner
    assert "icps=context.icp_result.icps[:1]" in runner
    assert "per_query_limit=5" in runner
    assert "max_opportunities=20" in runner
    assert "MAX_REDDIT_ENRICHMENTS = 3" in runner
    assert "AUDIENCE_MAP_NAMESPACE" in runner
    assert "ProductProfileStatus.CONFIRMED" in runner
    assert "community_distribution_acceptance_service.report(None)" in runner

    forbidden = (
        "distribution_execution",
        "publish(",
        "execute(",
        "send_message",
        "join",
        "invite",
        "participants",
    )
    assert all(token not in runner for token in forbidden)


def test_real_community_research_requires_prior_customer_research_consent() -> None:
    runner = Path("tools/run_community_research_acceptance.py").read_text(
        encoding="utf-8"
    )

    assert "CUSTOMER_PROJECT_NAMESPACE" in runner
    assert 'project.get("understanding_confirmed") is not True' in runner
    assert 'project.get("deleted_at")' in runner
    assert 'preview.get("free_research_status")' in runner
    assert '"FOUND"' in runner
    assert '"NEEDS_MORE_RESEARCH"' in runner
    assert '"UNAVAILABLE"' in runner
    assert 'source="CONFIRMED_CUSTOMER_PREVIEW_RESEARCH"' in runner
    assert "NO_ELIGIBLE_CONFIRMED_RESEARCH_CONTEXT" in runner
    assert "existing_map: AudienceDistributionMapView | None" in runner
