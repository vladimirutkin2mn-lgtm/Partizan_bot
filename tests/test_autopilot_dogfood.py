from uuid import UUID

import pytest

from app.autopilot_dogfood import (
    LIVE_SPEND_CONFIRMATION,
    AutopilotDogfoodRunner,
    AutopilotDogfoodSnapshot,
)
from app.config import Settings


def _snapshot(**overrides) -> AutopilotDogfoodSnapshot:
    values = {
        "project_id": UUID("11111111-1111-1111-1111-111111111111"),
        "product_id": UUID("22222222-2222-2222-2222-222222222222"),
        "autopilot_status": "ACTIVE",
        "meta_connected": True,
        "growth_balance_available_usd": 180.0,
        "acquisition_spend_usd": 20.0,
        "remaining_acquisition_capacity_usd": 160.0,
        "management_fee_usd": 2.0,
        "settlement_ready": True,
        "target_max_cac": 15.0,
        "paid_customers": 0,
        "revenue_usd": 0.0,
        "cac_usd": None,
        "roas": None,
        "running_experiments": 0,
        "waiting_experiments": 0,
        "readiness_blockers": [],
        "dogfood_complete": False,
    }
    values.update(overrides)
    return AutopilotDogfoodSnapshot(**values)


def test_live_sweep_requires_exact_confirmation_phrase() -> None:
    with pytest.raises(ValueError, match="not authorized"):
        AutopilotDogfoodRunner._assert_live_authorization(_snapshot(), "yes")

    AutopilotDogfoodRunner._assert_live_authorization(
        _snapshot(),
        LIVE_SPEND_CONFIRMATION,
    )


def test_live_sweep_fails_closed_on_readiness_or_growth_balance() -> None:
    with pytest.raises(ValueError, match="blocked"):
        AutopilotDogfoodRunner._assert_live_authorization(
            _snapshot(readiness_blockers=["Meta config missing"]),
            LIVE_SPEND_CONFIRMATION,
        )
    with pytest.raises(ValueError, match="Growth Balance"):
        AutopilotDogfoodRunner._assert_live_authorization(
            _snapshot(remaining_acquisition_capacity_usd=0),
            LIVE_SPEND_CONFIRMATION,
        )
    with pytest.raises(ValueError, match="payment rail"):
        AutopilotDogfoodRunner._assert_live_authorization(
            _snapshot(settlement_ready=False),
            LIVE_SPEND_CONFIRMATION,
        )


def test_runtime_gate_requires_production_database_https_and_real_creative_provider() -> None:
    runner = AutopilotDogfoodRunner(
        settings=Settings(
            _env_file=None,
            app_env="local",
            runtime_storage="memory",
            creative_provider="unavailable",
            partizan_public_base_url="http://localhost:8000",
        ),
        stripe_verify=lambda settings: None,
    )

    blockers = runner._runtime_blockers()

    assert any("APP_ENV=production" in item for item in blockers)
    assert any("RUNTIME_STORAGE=database" in item for item in blockers)
    assert any("public HTTPS" in item for item in blockers)
    assert any("CREATIVE_PROVIDER=openai" in item for item in blockers)


def test_runtime_gate_accepts_live_provider_shape_without_exposing_api_key() -> None:
    secret = "sk-live-never-print-this"
    runner = AutopilotDogfoodRunner(
        settings=Settings(
            _env_file=None,
            app_env="production",
            runtime_storage="database",
            creative_provider="openai",
            openai_api_key=secret,
            partizan_public_base_url="https://partizan.example.com",
        ),
        stripe_verify=lambda settings: None,
    )

    blockers = runner._runtime_blockers()

    assert blockers == []
    assert secret not in " ".join(blockers)


def test_live_authorization_has_no_subscription_requirement() -> None:
    snapshot = _snapshot()

    assert "subscription_status" not in snapshot.model_dump()
    AutopilotDogfoodRunner._assert_live_authorization(snapshot, LIVE_SPEND_CONFIRMATION)


def test_dogfood_completion_requires_paid_conversion_and_cac() -> None:
    incomplete = _snapshot(paid_customers=1, cac_usd=None)
    complete = _snapshot(paid_customers=1, cac_usd=12.5, dogfood_complete=True)

    assert incomplete.dogfood_complete is False
    assert complete.dogfood_complete is True
