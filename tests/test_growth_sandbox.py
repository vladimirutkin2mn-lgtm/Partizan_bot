from pathlib import Path

import pytest

from app.growth_sandbox import (
    SANDBOX_MODE,
    SandboxError,
    _repository_root,
    _sandbox_environment,
    run_sandbox,
)


def test_sandbox_environment_is_secret_free_and_forces_safe_providers() -> None:
    env = _sandbox_environment(_repository_root(), 8765)

    assert env["APP_ENV"] == "sandbox"
    assert env["RUNTIME_STORAGE"] == "memory"
    assert env["LLM_PROVIDER"] == "mock"
    assert env["SEARCH_PROVIDER"] == "mock"
    assert env["EXECUTION_PROVIDER"] == "mock"
    assert env["CREATIVE_PROVIDER"] == "unavailable"
    assert env["CREATIVE_VIDEO_PROVIDER"] == "unavailable"
    assert env["OPERATOR_AUTH_REQUIRED"] == "false"
    assert env["PARTIZAN_PUBLIC_BASE_URL"] == "http://127.0.0.1:8765"

    forbidden = {
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "SMTP_PASSWORD",
        "SMTP_USERNAME",
        "PARTIZAN_OPERATOR_KEY",
        "OPERATOR_API_KEY",
        "META_PRODUCT_ACCESS_TOKEN",
        "TIKTOK_PRODUCT_ACCESS_TOKEN",
        "DATABASE_URL",
        "CONTAINER_DATABASE_URL",
    }
    assert forbidden.isdisjoint(env)


def test_sandbox_refuses_production_parent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")

    with pytest.raises(SandboxError, match="APP_ENV=production"):
        run_sandbox()


def test_sandbox_source_has_no_external_execution_or_external_project_dependency() -> None:
    from app import growth_sandbox

    source = Path(growth_sandbox.__file__).read_text(encoding="utf-8")
    assert "/distribution-actions/{action_id}/execute" not in source
    assert "/paid-campaign/activate" not in source
    assert "activation-authorizations" not in source
    assert "SMTP" not in source
    assert "Bot_globa" not in source
    assert "Oracle" not in source
    assert "sandbox.invalid" in source


def test_isolated_sandbox_proves_full_growth_learning_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "test")

    report = run_sandbox()

    assert report.mode == SANDBOX_MODE
    assert report.isolated_runtime_storage == "memory"
    assert report.external_provider_mutation is False
    assert report.child_process_terminated is True
    assert report.economics.visits == 3
    assert report.economics.signups == 3
    assert report.economics.activated_users == 3
    assert report.economics.paid_users == 3
    assert report.economics.spend == 30.0
    assert report.economics.revenue == 90.0
    assert report.economics.cac == 10.0
    assert report.economics.roas == 3.0
    assert report.growth_decision == "SCALE"
    assert report.learning_entries == 1
    assert report.portfolio_items > 0
    assert report.portfolio_uses_observed_economics is True
