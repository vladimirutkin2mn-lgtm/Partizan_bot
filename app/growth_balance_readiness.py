from __future__ import annotations

import argparse
import json
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.growth_balance import (
    GROWTH_BALANCE_LOCK_NAMESPACE,
    GROWTH_BALANCE_RAIL_NAMESPACE,
    GROWTH_BALANCE_TOPUP_NAMESPACE,
    GROWTH_BALANCE_TRANSACTION_NAMESPACE,
    GrowthBalanceSettlementService,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

GrowthBalanceReadinessStatus = Literal[
    "CONFIGURATION_BLOCKED",
    "PROVIDER_BLOCKED",
    "RECONCILIATION_REQUIRED",
    "RAIL_NOT_READY",
    "READY_FOR_DOGFOOD",
    "SPEND_OBSERVED",
]


class GrowthBalanceReadinessReport(BaseModel):
    status: GrowthBalanceReadinessStatus
    release_sha: str
    production_environment: bool
    persistent_runtime: bool
    provider: str
    provider_configured: bool
    authorization_webhook_configured: bool
    events_webhook_configured: bool
    provider_reachable: bool
    provider_status: str
    rail_count: int = Field(ge=0)
    provisioned_rail_count: int = Field(ge=0)
    bound_rail_count: int = Field(ge=0)
    active_rail_count: int = Field(ge=0)
    paused_rail_count: int = Field(ge=0)
    ready_rail_count: int = Field(ge=0)
    reconciliation_required_count: int = Field(ge=0)
    paid_topup_count: int = Field(ge=0)
    issuing_transaction_count: int = Field(ge=0)
    observed_provider_spend_usd: float = Field(ge=0)
    operational_ready: bool
    spend_observed: bool
    blockers: list[str] = Field(default_factory=list)


class GrowthBalanceReadinessService:
    """Read-only, non-sensitive production readiness snapshot for Growth Balance.

    The report intentionally exposes only configuration booleans, aggregate rail counts,
    sanitized readiness statuses and aggregate settled spend. It never returns Stripe
    secrets, card identifiers, Meta account identifiers, customer identifiers or PAN/CVC.
    """

    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        *,
        settings: Settings | None = None,
        settlement_service: GrowthBalanceSettlementService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings_override = settings
        effective_settings = settings or get_settings()
        self._settlement = settlement_service or GrowthBalanceSettlementService(
            self._store,
            settings=effective_settings,
        )

    def report(self) -> GrowthBalanceReadinessReport:
        settings = self._settings_override or get_settings()
        production_environment = settings.app_env.strip().lower() == "production"
        persistent_runtime = not self._store.ephemeral
        provider = settings.growth_balance_settlement_provider.strip().lower()
        provider_configured = bool(
            provider == "stripe_issuing"
            and settings.stripe_secret_key is not None
            and settings.stripe_issuing_cardholder_id
        )
        authorization_webhook_configured = (
            settings.stripe_issuing_authorization_webhook_secret is not None
        )
        events_webhook_configured = settings.stripe_issuing_events_webhook_secret is not None

        provider_reachable = False
        provider_status = "NOT_PROBED"
        if provider_configured:
            try:
                provider_reachable, provider_status = self._settlement.funding_readiness(
                    UUID(int=0),
                    required_liquidity_cents=0,
                )
            except Exception:
                # Provider/library failures are deliberately collapsed to a safe status.
                provider_reachable = False
                provider_status = "STRIPE_ISSUING_UNAVAILABLE"

        rails = self._store.list_namespace(GROWTH_BALANCE_RAIL_NAMESPACE)
        transactions = self._store.list_namespace(GROWTH_BALANCE_TRANSACTION_NAMESPACE)
        locks = self._store.list_namespace(GROWTH_BALANCE_LOCK_NAMESPACE)
        topups = self._store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)

        provisioned_rail_count = sum(bool(item.get("card_id")) for item in rails)
        bound_rail_count = sum(item.get("binding_status") == "BOUND" for item in rails)
        active_rail_count = sum(item.get("card_status") == "active" for item in rails)
        paused_rail_count = sum(bool(item.get("paused_reason")) for item in rails)
        ready_rail_count = sum(self._rail_ready(item) for item in rails)
        reconciliation_required_count = sum(
            item.get("state") == "RECONCILIATION_REQUIRED" for item in locks
        )
        paid_topup_count = sum(
            item.get("state") == "PAID" and bool(item.get("session_id")) for item in topups
        )
        settled_spend_cents = max(
            sum(int(item.get("spend_delta_cents") or 0) for item in transactions),
            0,
        )
        spend_observed = settled_spend_cents > 0

        blockers: list[str] = []
        if not production_environment:
            blockers.append("APP_ENV_NOT_PRODUCTION")
        if not persistent_runtime:
            blockers.append("RUNTIME_STORAGE_NOT_PERSISTENT")
        if provider != "stripe_issuing":
            blockers.append("STRIPE_ISSUING_PROVIDER_NOT_ENABLED")
        if settings.stripe_secret_key is None:
            blockers.append("STRIPE_NOT_CONFIGURED")
        if not settings.stripe_issuing_cardholder_id:
            blockers.append("STRIPE_ISSUING_CARDHOLDER_NOT_CONFIGURED")
        if not authorization_webhook_configured:
            blockers.append("STRIPE_ISSUING_AUTHORIZATION_WEBHOOK_NOT_CONFIGURED")
        if not events_webhook_configured:
            blockers.append("STRIPE_ISSUING_EVENTS_WEBHOOK_NOT_CONFIGURED")

        configuration_ready = (
            production_environment
            and persistent_runtime
            and provider_configured
            and authorization_webhook_configured
            and events_webhook_configured
        )
        if not configuration_ready:
            status: GrowthBalanceReadinessStatus = "CONFIGURATION_BLOCKED"
        elif not provider_reachable:
            blockers.append(provider_status)
            status = "PROVIDER_BLOCKED"
        elif reconciliation_required_count:
            blockers.append("GROWTH_BALANCE_RECONCILIATION_REQUIRED")
            status = "RECONCILIATION_REQUIRED"
        elif ready_rail_count == 0:
            blockers.append("NO_SAFE_ACTIVE_BOUND_GROWTH_BALANCE_RAIL")
            status = "RAIL_NOT_READY"
        elif spend_observed:
            status = "SPEND_OBSERVED"
        else:
            blockers.append("REAL_PROVIDER_SPEND_NOT_OBSERVED")
            status = "READY_FOR_DOGFOOD"

        operational_ready = status in {"READY_FOR_DOGFOOD", "SPEND_OBSERVED"}
        return GrowthBalanceReadinessReport(
            status=status,
            release_sha=settings.partizan_release_sha,
            production_environment=production_environment,
            persistent_runtime=persistent_runtime,
            provider=provider,
            provider_configured=provider_configured,
            authorization_webhook_configured=authorization_webhook_configured,
            events_webhook_configured=events_webhook_configured,
            provider_reachable=provider_reachable,
            provider_status=provider_status,
            rail_count=len(rails),
            provisioned_rail_count=provisioned_rail_count,
            bound_rail_count=bound_rail_count,
            active_rail_count=active_rail_count,
            paused_rail_count=paused_rail_count,
            ready_rail_count=ready_rail_count,
            reconciliation_required_count=reconciliation_required_count,
            paid_topup_count=paid_topup_count,
            issuing_transaction_count=len(transactions),
            observed_provider_spend_usd=round(settled_spend_cents / 100, 2),
            operational_ready=operational_ready,
            spend_observed=spend_observed,
            blockers=blockers,
        )

    @staticmethod
    def _rail_ready(rail: dict) -> bool:
        return bool(
            rail.get("provider") == "stripe_issuing"
            and rail.get("card_id")
            and rail.get("binding_status") == "BOUND"
            and rail.get("card_status") == "active"
            and not rail.get("paused_reason")
            and int(rail.get("acquisition_limit_cents") or 0) > 0
        )


growth_balance_readiness_service = GrowthBalanceReadinessService()


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Report non-sensitive Growth Balance production spend-rail readiness."
    )
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    parser.add_argument(
        "--require-ready",
        action="store_true",
        help="Exit non-zero unless at least one safe active/bound rail is operationally ready.",
    )
    parser.add_argument(
        "--require-spend-observed",
        action="store_true",
        help="Exit non-zero unless real Stripe Issuing transaction spend is present.",
    )
    return parser.parse_args()


def main() -> int:
    args = _args()
    report = growth_balance_readiness_service.report()
    print(
        json.dumps(
            report.model_dump(mode="json"),
            indent=2 if args.pretty else None,
            sort_keys=True,
        )
    )
    if args.require_spend_observed and not report.spend_observed:
        return 4
    if args.require_ready and not report.operational_ready:
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
