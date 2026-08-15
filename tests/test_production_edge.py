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
