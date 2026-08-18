from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from app.config import get_settings
from app.customer_funnel import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerPaymentRequiredError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

GROWTH_BALANCE_TOPUP_NAMESPACE = "customer_growth_balance_topups"
_CENT = Decimal("0.01")


@dataclass(frozen=True)
class GrowthBalanceSummary:
    funded_usd: float
    acquisition_spend_usd: float
    management_fee_pct: int
    management_fee_usd: float
    used_usd: float
    available_usd: float
    acquisition_capacity_usd: float
    remaining_acquisition_capacity_usd: float
    settlement_ready: bool
    settlement_status: str


class GrowthBalanceSettlementService:
    """Boundary for moving prepaid Partizan funds to acquisition providers.

    The customer-funded ledger is implemented now, but paid activation stays fail-closed
    until a real Partizan-funded payment rail (for example, an approved card/Issuing
    integration) is connected. This prevents accepting a Growth Balance and then also
    charging the customer's provider billing method.
    """

    def readiness(self, project_id: UUID) -> tuple[bool, str]:
        del project_id
        return False, "PARTIZAN_FUNDED_PAYMENT_RAIL_NOT_CONFIGURED"


class GrowthBalanceService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        *,
        settlement_service: GrowthBalanceSettlementService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settlement = settlement_service or growth_balance_settlement_service

    def prepare_checkout(
        self,
        project_id: UUID,
        customer_token: str,
        amount_usd: float,
    ) -> tuple[int, str | None, int]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if project.get("autopilot_subscription_status") != "ACTIVE":
            raise CustomerPaymentRequiredError("Activate the Autopilot subscription first")
        ready, status = self._settlement.readiness(project_id)
        if not ready:
            raise ValueError(
                "Growth Balance funding is disabled until the Partizan-funded provider "
                f"payment rail is ready ({status})"
            )
        amount_cents = self._usd_to_cents(amount_usd)
        generation = int(project.get("growth_balance_checkout_generation") or 0) + 1
        project["growth_balance_checkout_generation"] = generation
        self._persist_project(project)
        return generation, project.get("stripe_customer_id"), amount_cents

    def mark_checkout_pending(
        self,
        project_id: UUID,
        customer_token: str,
        *,
        session_id: str,
        amount_cents: int,
    ) -> None:
        customer_funnel_service.get_project_payload(project_id, customer_token)
        payload = {
            "session_id": session_id,
            "project_id": str(project_id),
            "amount_cents": int(amount_cents),
            "currency": "usd",
            "state": "PENDING",
            "created_at": datetime.now(UTC).isoformat(),
        }
        existing = self._store.put_if_absent(
            GROWTH_BALANCE_TOPUP_NAMESPACE,
            session_id,
            payload,
        )
        if existing is not None:
            if (
                str(existing.get("project_id")) != str(project_id)
                or int(existing.get("amount_cents") or 0) != int(amount_cents)
            ):
                raise ValueError("Growth Balance Checkout Session is already bound differently")

    def pending(self, session_id: str) -> dict | None:
        return self._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id)

    def credit_paid_checkout(
        self,
        project_id: UUID,
        *,
        session_id: str,
        amount_cents: int,
        currency: str,
        stripe_customer_id: str | None = None,
    ) -> bool:
        record = self.pending(session_id)
        if record is None:
            return False
        if str(record.get("project_id")) != str(project_id):
            return False
        if int(record.get("amount_cents") or 0) != int(amount_cents):
            return False
        if str(record.get("currency") or "").lower() != currency.lower():
            return False
        if record.get("state") == "PAID":
            return True
        record["state"] = "PAID"
        record["paid_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id, record)

        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CustomerProjectNotFoundError(project_id)
        if stripe_customer_id:
            project["stripe_customer_id"] = stripe_customer_id
        project["growth_balance_last_funded_at"] = datetime.now(UTC).isoformat()
        self._persist_project(project)
        return True

    def summary(self, project_id: UUID, acquisition_spend_usd: float) -> GrowthBalanceSummary:
        settings = get_settings()
        fee_pct = int(settings.partizan_managed_spend_fee_pct)
        funded_cents = sum(
            int(item.get("amount_cents") or 0)
            for item in self._store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)
            if str(item.get("project_id")) == str(project_id) and item.get("state") == "PAID"
        )
        spend_cents = max(self._usd_to_cents(acquisition_spend_usd, allow_zero=True), 0)
        fee_cents = self._fee_cents(spend_cents, fee_pct)
        used_cents = spend_cents + fee_cents
        available_cents = max(funded_cents - used_cents, 0)
        capacity_cents = self._max_acquisition_cents(funded_cents, fee_pct)
        remaining_capacity_cents = max(capacity_cents - spend_cents, 0)
        settlement_ready, settlement_status = self._settlement.readiness(project_id)
        return GrowthBalanceSummary(
            funded_usd=self._cents_to_usd(funded_cents),
            acquisition_spend_usd=self._cents_to_usd(spend_cents),
            management_fee_pct=fee_pct,
            management_fee_usd=self._cents_to_usd(fee_cents),
            used_usd=self._cents_to_usd(used_cents),
            available_usd=self._cents_to_usd(available_cents),
            acquisition_capacity_usd=self._cents_to_usd(capacity_cents),
            remaining_acquisition_capacity_usd=self._cents_to_usd(remaining_capacity_cents),
            settlement_ready=settlement_ready,
            settlement_status=settlement_status,
        )

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)

    def _persist_project(self, project: dict) -> None:
        project["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(CUSTOMER_PROJECT_NAMESPACE, str(project["id"]), project)

    @staticmethod
    def _fee_cents(spend_cents: int, fee_pct: int) -> int:
        return int(
            (Decimal(spend_cents) * Decimal(fee_pct) / Decimal(100)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

    @classmethod
    def _max_acquisition_cents(cls, funded_cents: int, fee_pct: int) -> int:
        if funded_cents <= 0:
            return 0
        guess = funded_cents * 100 // (100 + fee_pct)
        while guess > 0 and guess + cls._fee_cents(guess, fee_pct) > funded_cents:
            guess -= 1
        while guess + 1 + cls._fee_cents(guess + 1, fee_pct) <= funded_cents:
            guess += 1
        return guess

    @staticmethod
    def _usd_to_cents(value: float, *, allow_zero: bool = False) -> int:
        amount = Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
        if amount < 0 or (amount == 0 and not allow_zero):
            raise ValueError("Growth Balance amount must be positive")
        return int(amount * 100)

    @staticmethod
    def _cents_to_usd(value: int) -> float:
        return float((Decimal(value) / Decimal(100)).quantize(_CENT))


growth_balance_settlement_service = GrowthBalanceSettlementService()
growth_balance_service = GrowthBalanceService(
    settlement_service=growth_balance_settlement_service,
)
