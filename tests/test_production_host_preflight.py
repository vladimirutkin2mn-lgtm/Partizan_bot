from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PREFLIGHT = ROOT / "tools" / "preflight_prod_host.sh"
BOOTSTRAP = ROOT / "tools" / "bootstrap_prod_host.sh"


def _fake_host_tools(tmp_path: Path) -> str:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in ("docker", "rsync"):
        path = bin_dir / name
        if name == "docker":
            path.write_text(
                "#!/usr/bin/env bash\n"
                "[[ \"${1:-}\" == 'compose' ]] || exit 1\n"
                "exit 0\n",
                encoding="utf-8",
            )
        else:
            path.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return f"{bin_dir}:{os.environ['PATH']}"


def _valid_env(**overrides: str) -> str:
    values = {
        "APP_ENV": "production",
        "RUNTIME_STORAGE": "database",
        "LLM_PROVIDER": "mock",
        "SEARCH_PROVIDER": "mock",
        "CREATIVE_PROVIDER": "unavailable",
        "CREATIVE_VIDEO_PROVIDER": "unavailable",
        "POSTGRES_PASSWORD": "a" * 32,
        "CONTAINER_DATABASE_URL": (
            "postgresql+asyncpg://partizan:" + "a" * 32 + "@postgres:5432/partizan"
        ),
        "OPERATOR_API_KEY": "b" * 64,
        "PARTIZAN_PUBLIC_BASE_URL": "https://partizan.example.com",
        "PARTIZAN_PUBLIC_HOST": "partizan.example.com",
        "STRIPE_SECRET_KEY": "sk_test_partizan_not_real",
        "STRIPE_WEBHOOK_SECRET": "whsec_partizan_not_real",
        "STRIPE_LAUNCH_PRICE_ID": "price_partizan_launch_not_real",
        "STRIPE_AUTOPILOT_PRICE_ID": "price_partizan_autopilot_not_real",
        "PARTIZAN_LAUNCH_PRICE_USD": "49",
        "PARTIZAN_AUTOPILOT_PRICE_USD": "149",
        "PARTIZAN_MANAGED_SPEND_FEE_PCT": "10",
        "PROVIDER_SECRET_ENCRYPTION_KEY": "A" * 43 + "=",
        "META_OAUTH_APP_ID": "123456789012345",
        "META_OAUTH_APP_SECRET": "meta_app_secret_not_real_123456",
        "META_OAUTH_API_VERSION": "v25.0",
    }
    values.update(overrides)
    return "".join(f"{key}={value}\n" for key, value in values.items())


def _run_preflight(
    tmp_path: Path,
    content: str,
    *,
    require_public: bool = False,
    extra_env: dict[str, str] | None = None,
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    env_file = tmp_path / ".env.prod"
    env_file.write_text(content, encoding="utf-8")
    env_file.chmod(0o600)
    env = os.environ.copy()
    env["PATH"] = _fake_host_tools(tmp_path)
    env["PARTIZAN_REQUIRE_PUBLIC_URL"] = "true" if require_public else "false"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(PREFLIGHT), str(env_file)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _read_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


def test_valid_production_host_preflight_passes(tmp_path: Path) -> None:
    result = _run_preflight(tmp_path, _valid_env(), require_public=True)

    assert result.returncode == 0, result.stderr
    assert "production host preflight: ok" in result.stdout
    assert "b" * 64 not in result.stdout + result.stderr
    assert "sk_test_partizan_not_real" not in result.stdout + result.stderr
    assert "meta_app_secret_not_real" not in result.stdout + result.stderr


def test_internal_only_preflight_passes_without_public_edge(tmp_path: Path) -> None:
    result = _run_preflight(
        tmp_path,
        _valid_env(PARTIZAN_PUBLIC_BASE_URL="", PARTIZAN_PUBLIC_HOST=""),
    )

    assert result.returncode == 0, result.stderr


def test_preflight_rejects_missing_or_placeholder_operator_key(tmp_path: Path) -> None:
    missing = _run_preflight(tmp_path / "missing", _valid_env(OPERATOR_API_KEY=""))
    placeholder = _run_preflight(tmp_path / "placeholder", _valid_env(OPERATOR_API_KEY="change-me"))

    assert missing.returncode != 0
    assert placeholder.returncode != 0
    assert "OPERATOR_API_KEY" in missing.stderr
    assert "OPERATOR_API_KEY" in placeholder.stderr


def test_preflight_rejects_weak_database_secret_and_external_database_host(tmp_path: Path) -> None:
    weak = _run_preflight(tmp_path / "weak", _valid_env(POSTGRES_PASSWORD="partizan"))
    external = _run_preflight(
        tmp_path / "external",
        _valid_env(
            CONTAINER_DATABASE_URL=(
                "postgresql+asyncpg://partizan:" + "a" * 32 + "@db.example.com:5432/partizan"
            )
        ),
    )

    assert weak.returncode != 0
    assert external.returncode != 0
    assert "POSTGRES_PASSWORD" in weak.stderr
    assert "internal postgres service" in external.stderr


def test_preflight_rejects_insecure_env_permissions(tmp_path: Path) -> None:
    env_file = tmp_path / ".env.prod"
    env_file.write_text(_valid_env(), encoding="utf-8")
    env_file.chmod(0o644)
    env = os.environ.copy()
    env["PATH"] = _fake_host_tools(tmp_path)

    result = subprocess.run(
        ["bash", str(PREFLIGHT), str(env_file)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert "permissions must be 600" in result.stderr


def test_preflight_can_require_public_https_origin(tmp_path: Path) -> None:
    missing = _run_preflight(
        tmp_path / "missing",
        _valid_env(PARTIZAN_PUBLIC_BASE_URL="", PARTIZAN_PUBLIC_HOST=""),
        require_public=True,
    )
    invalid = _run_preflight(
        tmp_path / "invalid",
        _valid_env(PARTIZAN_PUBLIC_BASE_URL="http://partizan.example.com/path"),
    )

    assert missing.returncode != 0
    assert invalid.returncode != 0
    assert "PARTIZAN_PUBLIC_BASE_URL is required" in missing.stderr
    assert "HTTPS origin" in invalid.stderr


def test_preflight_rejects_public_host_mismatch_or_non_dns_host(tmp_path: Path) -> None:
    mismatch = _run_preflight(
        tmp_path / "mismatch",
        _valid_env(PARTIZAN_PUBLIC_HOST="other.example.com"),
    )
    port = _run_preflight(
        tmp_path / "port",
        _valid_env(
            PARTIZAN_PUBLIC_BASE_URL="https://partizan.example.com:8443",
            PARTIZAN_PUBLIC_HOST="partizan.example.com:8443",
        ),
    )
    orphan_host = _run_preflight(
        tmp_path / "orphan",
        _valid_env(PARTIZAN_PUBLIC_BASE_URL="", PARTIZAN_PUBLIC_HOST="partizan.example.com"),
    )

    assert mismatch.returncode != 0
    assert port.returncode != 0
    assert orphan_host.returncode != 0
    assert "exactly match" in mismatch.stderr
    assert "DNS hostname" in port.stderr
    assert "must be empty" in orphan_host.stderr


def test_public_preflight_requires_valid_stripe_checkout_config(tmp_path: Path) -> None:
    missing_secret = _run_preflight(
        tmp_path / "missing-secret",
        _valid_env(STRIPE_SECRET_KEY=""),
    )
    missing_webhook = _run_preflight(
        tmp_path / "missing-webhook",
        _valid_env(STRIPE_WEBHOOK_SECRET=""),
    )
    wrong_price = _run_preflight(
        tmp_path / "wrong-price",
        _valid_env(STRIPE_LAUNCH_PRICE_ID="prod_not_a_price"),
    )
    missing_autopilot = _run_preflight(
        tmp_path / "missing-autopilot",
        _valid_env(STRIPE_AUTOPILOT_PRICE_ID=""),
    )

    assert missing_secret.returncode != 0
    assert missing_webhook.returncode != 0
    assert wrong_price.returncode != 0
    assert missing_autopilot.returncode != 0
    assert "STRIPE_SECRET_KEY" in missing_secret.stderr
    assert "STRIPE_WEBHOOK_SECRET" in missing_webhook.stderr
    assert "Stripe Price ID" in wrong_price.stderr
    assert "STRIPE_AUTOPILOT_PRICE_ID" in missing_autopilot.stderr


def test_public_preflight_requires_encrypted_meta_oauth_config(tmp_path: Path) -> None:
    missing_encryption = _run_preflight(
        tmp_path / "missing-encryption",
        _valid_env(PROVIDER_SECRET_ENCRYPTION_KEY=""),
    )
    missing_app = _run_preflight(
        tmp_path / "missing-app",
        _valid_env(META_OAUTH_APP_ID=""),
    )
    bad_version = _run_preflight(
        tmp_path / "bad-version",
        _valid_env(META_OAUTH_API_VERSION="latest"),
    )

    assert missing_encryption.returncode != 0
    assert missing_app.returncode != 0
    assert bad_version.returncode != 0
    assert "PROVIDER_SECRET_ENCRYPTION_KEY" in missing_encryption.stderr
    assert "META_OAUTH_APP_ID" in missing_app.stderr
    assert "META_OAUTH_API_VERSION" in bad_version.stderr


def test_shared_host_mode_does_not_require_managed_edge_files(tmp_path: Path) -> None:
    """A public origin fronted by another product's proxy needs no edge files of our own."""
    result = _run_preflight(
        tmp_path,
        _valid_env(),
        require_public=True,
        extra_env={
            "PARTIZAN_MANAGED_EDGE": "false",
            "PARTIZAN_EXTRA_COMPOSE_FILES": "docker-compose.shared-host.yml",
            "PARTIZAN_EDGE_NETWORK": "web",
        },
    )

    assert result.returncode == 0, result.stderr
    assert "production host preflight: ok" in result.stdout


def test_preflight_rejects_unusable_edge_mode_and_missing_overlay(tmp_path: Path) -> None:
    bad_mode = _run_preflight(
        tmp_path / "bad-mode",
        _valid_env(),
        extra_env={"PARTIZAN_MANAGED_EDGE": "maybe"},
    )
    missing_overlay = _run_preflight(
        tmp_path / "missing-overlay",
        _valid_env(),
        extra_env={"PARTIZAN_EXTRA_COMPOSE_FILES": "docker-compose.does-not-exist.yml"},
    )

    assert bad_mode.returncode != 0
    assert missing_overlay.returncode != 0
    assert "PARTIZAN_MANAGED_EDGE must be true or false" in bad_mode.stderr
    assert "docker-compose.does-not-exist.yml" in missing_overlay.stderr


def test_live_provider_modes_require_matching_api_keys(tmp_path: Path) -> None:
    openai_missing = _run_preflight(
        tmp_path / "openai-missing",
        _valid_env(LLM_PROVIDER="openai", OPENAI_API_KEY=""),
    )
    openai_ok = _run_preflight(
        tmp_path / "openai-ok",
        _valid_env(LLM_PROVIDER="openai", OPENAI_API_KEY="sk-test-not-real"),
    )
    gemini_missing = _run_preflight(
        tmp_path / "gemini-missing",
        _valid_env(CREATIVE_VIDEO_PROVIDER="gemini_omni", GEMINI_API_KEY=""),
    )

    assert openai_missing.returncode != 0
    assert openai_ok.returncode == 0, openai_ok.stderr
    assert gemini_missing.returncode != 0


def test_bootstrap_generates_private_env_without_printing_secrets(tmp_path: Path) -> None:
    deploy_path = tmp_path / "partizan"
    env = os.environ.copy()
    env["PARTIZAN_PUBLIC_BASE_URL"] = "https://partizan.example.com"

    result = subprocess.run(
        ["bash", str(BOOTSTRAP), str(deploy_path)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    env_file = deploy_path / ".env.prod"
    values = _read_env(env_file)
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert len(values["POSTGRES_PASSWORD"]) == 64
    assert len(values["OPERATOR_API_KEY"]) == 64
    assert len(values["PROVIDER_SECRET_ENCRYPTION_KEY"]) >= 40
    assert values["APP_ENV"] == "production"
    assert values["RUNTIME_STORAGE"] == "database"
    assert values["PARTIZAN_PUBLIC_BASE_URL"] == "https://partizan.example.com"
    assert values["PARTIZAN_PUBLIC_HOST"] == "partizan.example.com"
    assert values["STRIPE_SECRET_KEY"] == ""
    assert values["STRIPE_WEBHOOK_SECRET"] == ""
    assert values["STRIPE_LAUNCH_PRICE_ID"] == ""
    assert values["STRIPE_AUTOPILOT_PRICE_ID"] == ""
    assert values["PARTIZAN_LAUNCH_PRICE_USD"] == "49"
    assert values["PARTIZAN_AUTOPILOT_PRICE_USD"] == "149"
    assert values["PARTIZAN_MANAGED_SPEND_FEE_PCT"] == "10"
    assert values["META_OAUTH_APP_ID"] == ""
    assert values["META_OAUTH_APP_SECRET"] == ""
    assert values["META_OAUTH_API_VERSION"] == ""
    assert values["POSTGRES_PASSWORD"] not in result.stdout + result.stderr
    assert values["OPERATOR_API_KEY"] not in result.stdout + result.stderr
    assert values["PROVIDER_SECRET_ENCRYPTION_KEY"] not in result.stdout + result.stderr


def test_bootstrap_refuses_to_overwrite_existing_env(tmp_path: Path) -> None:
    deploy_path = tmp_path / "partizan"
    deploy_path.mkdir()
    env_file = deploy_path / ".env.prod"
    env_file.write_text("sentinel=true\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(BOOTSTRAP), str(deploy_path)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode != 0
    assert env_file.read_text(encoding="utf-8") == "sentinel=true\n"
    assert "refusing to overwrite" in result.stderr
