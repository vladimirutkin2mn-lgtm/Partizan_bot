from __future__ import annotations

from app.config import Settings
from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GROWTH_BALANCE_TRANSACTION_NAMESPACE,
)
from app.growth_balance_readiness import GrowthBalanceReadinessService
from app.runtime_store import MemoryRuntimeStateStore


class PersistentMemoryStore(MemoryRuntimeStateStore):
    ephemeral = False


class FakeSettlement:
    def __init__(self, *, ready: bool = True, status: str = "READY_FOR_FUNDING") -> None:
        self.ready = ready
        self.status = status
        self.calls = 0

    def funding_readiness(self, project_id, *, required_liquidity_cents: int):
        del project_id
        assert required_liquidity_cents == 0
        self.calls += 1
        return self.ready, self.status


def _settings(**overrides) -> Settings:
    values = {
        "app_env": "production",
        "runtime_storage": "database",
        "partizan_release_sha": "a" * 40,
        "growth_balance_settlement_provider": "stripe_issuing",
        "stripe_secret_key": "sk_live_do_not_print",
        "stripe_issuing_cardholder_id": "ich_do_not_print",
        "stripe_issuing_authorization_webhook_secret": "whsec_auth_do_not_print",
        "stripe_issuing_events_webhook_secret": "whsec_events_do_not_print",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def _safe_rail() -> dict:
    return {
        "project_id": "11111111-1111-1111-1111-111111111111",
        "provider": "stripe_issuing",
        "card_id": "ic_do_not_print",
        "card_last4": "4242",
        "card_status": "active",
        "currency": "usd",
        "binding_status": "BOUND",
        "bound_provider": "meta",
        "bound_provider_account_id": "act_do_not_print",
        "acquisition_limit_cents": 10_000,
        "paused_reason": None,
    }


def test_configuration_blockers_do_not_probe_provider() -> None:
    store = MemoryRuntimeStateStore()
    settlement = FakeSettlement()
    settings = _settings(
        app_env="local",
        runtime_storage="memory",
        growth_balance_settlement_provider="unavailable",
        stripe_secret_key=None,
        stripe_issuing_cardholder_id=None,
        stripe_issuing_authorization_webhook_secret=None,
        stripe_issuing_events_webhook_secret=None,
    )

    report = GrowthBalanceReadinessService(
        store,
        settings=settings,
        settlement_service=settlement,
    ).report()

    assert report.status == "CONFIGURATION_BLOCKED"
    assert report.operational_ready is False
    assert report.provider_reachable is False
    assert settlement.calls == 0
    assert "APP_ENV_NOT_PRODUCTION" in report.blockers
    assert "RUNTIME_STORAGE_NOT_PERSISTENT" in report.blockers
    assert "STRIPE_ISSUING_PROVIDER_NOT_ENABLED" in report.blockers


def test_configured_provider_without_safe_rail_is_not_operationally_ready() -> None:
    store = PersistentMemoryStore()
    settlement = FakeSettlement()

    report = GrowthBalanceReadinessService(
        store,
        settings=_settings(),
        settlement_service=settlement,
    ).report()

    assert report.status == "RAIL_NOT_READY"
    assert report.provider_reachable is True
    assert report.provider_status == "READY_FOR_FUNDING"
    assert report.ready_rail_count == 0
    assert report.operational_ready is False
    assert settlement.calls == 1


def test_safe_active_bound_rail_is_ready_for_real_dogfood() -> None:
    store = PersistentMemoryStore()
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, "project-1", _safe_rail())
    store.put(
        GROWTH_BALANCE_TOPUP_NAMESPACE,
        "cs_paid",
        {
            "session_id": "cs_paid",
            "state": "PAID",
            "project_id": "11111111-1111-1111-1111-111111111111",
        },
    )

    report = GrowthBalanceReadinessService(
        store,
        settings=_settings(),
        settlement_service=FakeSettlement(),
    ).report()

    assert report.status == "READY_FOR_DOGFOOD"
    assert report.operational_ready is True
    assert report.spend_observed is False
    assert report.rail_count == 1
    assert report.provisioned_rail_count == 1
    assert report.bound_rail_count == 1
    assert report.active_rail_count == 1
    assert report.ready_rail_count == 1
    assert report.paid_topup_count == 1
    assert report.blockers == ["REAL_PROVIDER_SPEND_NOT_OBSERVED"]


def test_observed_issuing_spend_is_reported_only_as_aggregate() -> None:
    store = PersistentMemoryStore()
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, "project-1", _safe_rail())
    store.put(
        GROWTH_BALANCE_TRANSACTION_NAMESPACE,
        "itxn_secret_identifier",
        {
            "transaction_id": "itxn_secret_identifier",
            "project_id": "11111111-1111-1111-1111-111111111111",
            "card_id": "ic_do_not_print",
            "spend_delta_cents": 2500,
        },
    )

    report = GrowthBalanceReadinessService(
        store,
        settings=_settings(),
        settlement_service=FakeSettlement(),
    ).report()
    rendered = report.model_dump_json()

    assert report.status == "SPEND_OBSERVED"
    assert report.operational_ready is True
    assert report.spend_observed is True
    assert report.issuing_transaction_count == 1
    assert report.observed_provider_spend_usd == 25.0
    assert "ic_do_not_print" not in rendered
    assert "itxn_secret_identifier" not in rendered
    assert "act_do_not_print" not in rendered
    assert "sk_live_do_not_print" not in rendered
    assert "whsec_auth_do_not_print" not in rendered


def test_reconciliation_required_fail_closes_even_with_an_active_rail() -> None:
    store = PersistentMemoryStore()
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, "project-1", _safe_rail())
    store.put(
        GROWTH_BALANCE_LOCK_NAMESPACE,
        "incident-1",
        {
            "kind": "ISSUING_AUTHORIZATION_FALLBACK",
            "state": "RECONCILIATION_REQUIRED",
        },
    )

    report = GrowthBalanceReadinessService(
        store,
        settings=_settings(),
        settlement_service=FakeSettlement(),
    ).report()

    assert report.status == "RECONCILIATION_REQUIRED"
    assert report.operational_ready is False
    assert report.reconciliation_required_count == 1
    assert "GROWTH_BALANCE_RECONCILIATION_REQUIRED" in report.blockers


def test_paused_or_zero_limit_rail_never_counts_as_ready() -> None:
    store = PersistentMemoryStore()
    paused = _safe_rail()
    paused["paused_reason"] = "ISSUING_FALLBACK_iauth_1_webhook_timeout"
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, "paused", paused)
    zero_limit = _safe_rail()
    zero_limit["acquisition_limit_cents"] = 0
    store.put(GROWTH_BALANCE_RAIL_NAMESPACE, "zero", zero_limit)

    report = GrowthBalanceReadinessService(
        store,
        settings=_settings(),
        settlement_service=FakeSettlement(),
    ).report()

    assert report.status == "RAIL_NOT_READY"
    assert report.rail_count == 2
    assert report.paused_rail_count == 1
    assert report.ready_rail_count == 0
