from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_edge_is_optional_and_publishes_only_http_https_ports() -> None:
    base = (ROOT / "docker-compose.prod.yml").read_text(encoding="utf-8")
    edge = (ROOT / "docker-compose.edge.yml").read_text(encoding="utf-8")

    assert '"127.0.0.1:${PARTIZAN_API_PORT:-8000}:8000"' in base
    assert "5432:5432" not in base
    assert '"80:80"' in edge
    assert '"443:443"' in edge
    assert '"443:443/udp"' in edge
    assert "api:8000" not in edge
    assert "POSTGRES" not in edge


def test_edge_routes_to_internal_api_and_has_persistent_tls_state() -> None:
    edge = (ROOT / "docker-compose.edge.yml").read_text(encoding="utf-8")
    caddy = (ROOT / "Caddyfile.prod").read_text(encoding="utf-8")

    assert "PARTIZAN_PUBLIC_HOST" in edge
    assert "partizan_caddy_data:/data" in edge
    assert "partizan_caddy_config:/config" in edge
    assert "{$PARTIZAN_PUBLIC_HOST}" in caddy
    assert "reverse_proxy api:8000" in caddy
    assert "-Server" in caddy


def test_deploy_selects_edge_only_for_explicit_public_url() -> None:
    deploy = (ROOT / "tools" / "deploy_prod_remote.sh").read_text(encoding="utf-8")

    assert 'if [[ -n "${PARTIZAN_PUBLIC_URL}" ]]' in deploy
    assert "-f docker-compose.edge.yml" in deploy
    assert 'START_SERVICES="${START_SERVICES} edge"' in deploy
    assert "Public HTTPS smoke" in deploy


def test_bootstrap_and_preflight_pin_public_hostname_to_origin() -> None:
    bootstrap = (ROOT / "tools" / "bootstrap_prod_host.sh").read_text(encoding="utf-8")
    preflight = (ROOT / "tools" / "preflight_prod_host.sh").read_text(encoding="utf-8")

    assert 'PUBLIC_HOST="${PUBLIC_BASE_URL#https://}"' in bootstrap
    assert "PARTIZAN_PUBLIC_HOST=${PUBLIC_HOST}" in bootstrap
    assert "PARTIZAN_PUBLIC_HOST must exactly match" in preflight
    assert "docker-compose.edge.yml" in preflight



def test_public_deploy_smoke_verifies_exact_current_onboarding_release() -> None:
    deploy = (ROOT / "tools" / "deploy_prod_remote.sh").read_text(encoding="utf-8")

    assert 'X-Partizan-Release-SHA'.casefold() in deploy.casefold()
    assert 'x-partizan-onboarding-revision' in deploy.casefold()
    assert 'Show Partizan what you built.' in deploy
    assert 'Likely first audiences' in deploy
    assert "'Paste your product.'" in deploy
    assert "'Product website'" in deploy
    assert "'Scan my product'" in deploy
    assert 'start.v2.js start.v2.css goal-dropdown.v1.css goal-dropdown.v1.js' in deploy
    assert 'cmp -s "app/web/${asset}"' in deploy
    assert '/start?release=${PARTIZAN_RELEASE_SHA}' in deploy



def test_public_deploy_smoke_verifies_every_primary_browser_surface() -> None:
    deploy = (ROOT / "tools" / "deploy_prod_remote.sh").read_text(encoding="utf-8")

    assert "Verifying all browser surfaces belong to the release" in deploy
    assert "x-partizan-marketing-revision" in deploy
    assert "x-partizan-workspace-revision" in deploy
    assert "x-partizan-app-revision" in deploy
    assert "x-partizan-legal-revision" in deploy
    assert "landing.v1.css landing.v1.js" in deploy
    assert (
        "workspace.v1.js workspace.channels.v1.js "
        "workspace.projects.v1.js workspace.experiments.v1.js"
    ) in deploy
    assert "partizan.v1.js execution.v2.js paid-control.v1.js" in deploy
    assert "/privacy|What Partizan stores|privacy" in deploy
    assert "/terms|Use Partizan with clear boundaries.|terms" in deploy
    assert "/security|Execution should fail closed|security" in deploy
    assert "/contact|Need help with Partizan?|contact" in deploy
