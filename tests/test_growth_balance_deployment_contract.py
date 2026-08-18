from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_stripe_issuing_mode_is_fail_closed_in_production_preflight() -> None:
    script = (ROOT / "tools" / "preflight_prod_host.sh").read_text(encoding="utf-8")

    assert 'settlement_provider="$(env_value GROWTH_BALANCE_SETTLEMENT_PROVIDER)"' in script
    assert 'if [[ "${settlement_provider}" == "stripe_issuing" ]]; then' in script
    for required in (
        "STRIPE_SECRET_KEY",
        "STRIPE_ISSUING_CARDHOLDER_ID",
        "STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_SECRET",
        "STRIPE_ISSUING_EVENTS_WEBHOOK_SECRET",
        "STRIPE_ISSUING_WEBHOOK_API_VERSION",
    ):
        assert required in script
    assert "PARTIZAN_PUBLIC_BASE_URL is required for Stripe Issuing authorization webhooks" in script
    assert "STRIPE_ISSUING_CURRENCY must currently be usd" in script


def test_bootstrap_and_runbook_keep_money_rail_disabled_until_explicit_setup() -> None:
    bootstrap = (ROOT / "tools" / "bootstrap_prod_host.sh").read_text(encoding="utf-8")
    runbook = (ROOT / "docs" / "GROWTH_BALANCE_ISSUING.md").read_text(encoding="utf-8")

    assert "GROWTH_BALANCE_SETTLEMENT_PROVIDER=unavailable" in bootstrap
    assert "GROWTH_BALANCE_SETTLEMENT_PROVIDER=stripe_issuing" in runbook
    assert "/v1/billing/stripe/issuing-authorizations" in runbook
    assert "/v1/billing/stripe/issuing-events" in runbook
    assert "/growth-balance/rail/meta-binding" in runbook
    assert "does **not** automate adding the Partizan Issuing card" in runbook
    assert "Do not bypass preflight" in runbook
