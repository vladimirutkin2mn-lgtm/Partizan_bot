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


def test_shared_host_route_repair_is_guarded_and_rollback_safe() -> None:
    workflow = _text(".github/workflows/deploy-production.yml")
    repair = _text("tools/ensure_shared_host_caddy_route.sh")

    repair_marker = "- name: Ensure shared-host Caddy route"
    deploy_marker = "- name: Deploy, migrate and smoke Partizan"

    assert repair_marker in workflow
    assert workflow.index(repair_marker) < workflow.index(deploy_marker)
    assert '[[ "${PARTIZAN_MANAGED_EDGE}" == "false"' in workflow
    assert "DEPLOY_PATH: ${{ secrets.DEPLOY_PATH }}" in workflow
    assert "bash tools/ensure_shared_host_caddy_route.sh" in workflow
    assert "partizanlabs.com" in repair
    assert 'upstream="partizan-api:8000"' in repair
    assert "expected exactly one published 443 container" in repair
    assert "docker network inspect" in repair
    assert "docker network connect" in repair
    assert "docker network disconnect" in repair
    assert "docker-compose.shared-host.yml" in repair
    assert "Partizan API is not attached to configured edge network" in repair
    assert "proxy attached to Partizan edge network" in repair
    assert "docker container inspect" in repair
    assert 'eq .Destination "/etc/caddy/conf.d"' in repair
    assert "proxy exposes no writable imported route directory" in repair
    assert "mktemp /tmp/Caddyfile.partizan.backup" in repair
    assert "mktemp /tmp/Caddyfile.partizan.candidate" in repair
    assert 'cat "${candidate}" > "${route_file}"' in repair
    assert 'cat "${backup}" > "${route_file}"' in repair
    assert "existing Caddyfile is invalid; refusing mutation" in repair
    assert 'wget -q -O /dev/null -T 5 "http://$1/health/live"' in repair
    assert "BEGIN PARTIZAN MANAGED ROUTE" in repair
    assert "reverse_proxy %s" in repair
    assert "caddy validate" in repair
    assert "caddy reload" in repair
    assert "rollback_network_if_added()" in repair
    assert "restore_route_file()" in repair
    assert "rollback()" in repair
    assert '--resolve "${host}:443:127.0.0.1"' in repair
    assert "route restored and local TLS health verified" in repair

    forbidden_broad_mutations = (
        "docker restart",
        "docker compose restart",
        "systemctl restart",
        "systemctl reload",
        "rm -rf /etc/caddy",
    )
    assert all(command not in repair for command in forbidden_broad_mutations)

    forbidden_disclosures = (
        "cat /etc/caddy/Caddyfile",
        "docker inspect ",
        "printenv",
        'echo "${edge_network}"',
        'echo "${DEPLOY_PATH}"',
        'echo "${caddy_source}"',
    )
    assert all(value not in repair for value in forbidden_disclosures)


def test_shared_host_route_repair_owns_only_its_imported_route_file() -> None:
    repair = _text("tools/ensure_shared_host_caddy_route.sh")

    # The route belongs in the directory the shared proxy imports, never in the
    # other project's Caddyfile: their deploy rsyncs with --delete and would
    # remove anything written there.
    assert 'route_file="${conf_dir_source}/partizan.caddy"' in repair
    assert 'eq .Destination "/etc/caddy/conf.d"' in repair

    executable = "\n".join(
        line for line in repair.splitlines() if not line.lstrip().startswith("#")
    )
    assert "caddy_source" not in executable

    # A single-file bind mount pins the inode present at container start, so a
    # deploy that replaces the file detaches the proxy from it and both writes
    # and reloads become silent no-ops. Never resolve that mount destination.
    assert 'eq .Destination "/etc/caddy/Caddyfile"' not in repair

    # `caddy reload` exits 0 and reports "config is unchanged" when it re-reads
    # identical bytes, so the running config is the only proof the route is live.
    assert "http://127.0.0.1:2019/config/apps/http/servers" in repair
    assert "route_is_served()" in repair
    assert "reloaded config does not serve the target host" in repair
    assert "target host present in running config" in repair

    # Writing the file must not be enough to claim success on its own.
    served_marker = "if ! route_is_served; then"
    sni_marker = '--resolve "${host}:443:127.0.0.1"'
    assert repair.index(served_marker) < repair.index(sni_marker)

    # A route this run created must be removed again on rollback, not left
    # behind as a half-applied config.
    assert "route_file_existed=false" in repair
    assert 'rm -f "${route_file}"' in repair

    forbidden_inode_replacing_writes = (
        "sed -i",
        'mv "${candidate}"',
        'cp "${candidate}" "${route_file}"',
        "tee ${route_file}",
    )
    assert all(command not in executable for command in forbidden_inode_replacing_writes)


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
