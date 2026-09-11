from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_deploy_workflow_opens_and_closes_one_production_incident() -> None:
    workflow = _text(".github/workflows/deploy-production.yml")

    assert "issues: write" in workflow
    assert "Open or update production deploy incident" in workflow
    assert "failure() && steps.freshness.outputs.deploy_allowed == 'true'" in workflow
    assert "🚨 Production deploy failing" in workflow
    assert "gh issue create" in workflow
    assert "Close recovered production deploy incident" in workflow
    assert "success() && steps.freshness.outputs.deploy_allowed == 'true'" in workflow
    assert "gh issue close" in workflow


def test_deploy_refuses_stale_release_before_production_mutation() -> None:
    workflow = _text(".github/workflows/deploy-production.yml")

    freshness_marker = "- name: Refuse stale production release"
    mutation_marker = "- name: Deploy, migrate and smoke Partizan"

    assert freshness_marker in workflow
    assert mutation_marker in workflow
    assert workflow.index(freshness_marker) < workflow.index(mutation_marker)
    assert "git ls-remote origin refs/heads/main" in workflow
    assert "Unable to resolve current main HEAD; refusing production mutation" in workflow
    assert 'echo "deploy_allowed=false" >> "$GITHUB_OUTPUT"' in workflow
    assert "Skipping stale production deploy" in workflow
    assert 'echo "deploy_allowed=true" >> "$GITHUB_OUTPUT"' in workflow
    assert workflow.count("steps.freshness.outputs.deploy_allowed == 'true'") >= 7


def test_deploy_propagates_and_verifies_exact_release_sha() -> None:
    workflow = _text(".github/workflows/deploy-production.yml")
    deploy = _text("tools/deploy_prod_remote.sh")
    compose = _text("docker-compose.prod.yml")

    assert "PARTIZAN_RELEASE_SHA: ${{ steps.release.outputs.sha }}" in workflow
    assert "PARTIZAN_RELEASE_SHA must be an exact 40-character Git commit SHA" in deploy
    assert "${base}/version" in deploy
    assert "served_release_sha" in deploy
    assert "expected release ${PARTIZAN_RELEASE_SHA}" in deploy
    assert "PARTIZAN_RELEASE_SHA: ${PARTIZAN_RELEASE_SHA:-unknown}" in compose


def test_shared_host_deploy_failure_collects_read_only_tls_diagnostics() -> None:
    workflow = _text(".github/workflows/deploy-production.yml")
    diagnostic = _text("tools/diagnose_shared_host_tls.sh")

    assert '[[ "${PARTIZAN_MANAGED_EDGE}" == "false"' in workflow
    assert "bash tools/diagnose_shared_host_tls.sh || true" in workflow
    assert "getent ahosts" in diagnostic
    assert "ss -ltnp" in diagnostic
    assert "docker ps --format" in diagnostic
    assert "--resolve" in diagnostic
    assert "openssl s_client" in diagnostic
    assert "caddy validate" in diagnostic
    assert "caddy-config-validation=ok" in diagnostic
    assert "caddy-target-host-route=present" in diagnostic
    assert "caddy-target-host-route=missing" in diagnostic
    assert "caddy-target-certificate-storage=present" in diagnostic
    assert "caddy-target-certificate-storage=missing" in diagnostic
    assert "classification=local_tls_handshake_ok_check_proxy_route_or_external_dns" in diagnostic
    assert "classification=local_sni_tls_failed_check_shared_proxy_certificate_and_host_route" in diagnostic

    forbidden_mutations = (
        "docker restart",
        "docker compose restart",
        "systemctl restart",
        "systemctl reload",
        "caddy reload",
        "nginx -s reload",
    )
    assert all(command not in diagnostic for command in forbidden_mutations)

    forbidden_disclosures = (
        ".env.prod",
        'echo "${DEPLOY_HOST}"',
        "cat /etc/caddy/Caddyfile",
        "docker inspect",
        "printenv",
    )
    assert all(value not in diagnostic for value in forbidden_disclosures)


def test_meta_oauth_handoff_is_versioned_with_exact_callback_and_scopes() -> None:
    runbook = _text("docs/META_OAUTH_REQUEST.md")

    assert "https://partizanlabs.com/v1/customer-meta/oauth/callback" in runbook
    assert "ads_management" in runbook
    assert "ads_read" in runbook
    assert "META_OAUTH_APP_ID" in runbook
    assert "META_OAUTH_APP_SECRET" in runbook
    assert "META_OAUTH_API_VERSION" in runbook
    assert "Do not send it in chat" in runbook
