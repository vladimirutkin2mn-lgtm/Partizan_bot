from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID, uuid4

import stripe

from app.config import Settings, get_settings
from app.customer_funnel import (
    CUSTOMER_PROJECT_NAMESPACE,
    CustomerPaymentRequiredError,
    CustomerProjectNotFoundError,
    customer_funnel_service,
)
from app.runtime_store import RuntimeStateStore, get_runtime_store

GROWTH_BALANCE_TOPUP_NAMESPACE = "customer_growth_balance_topups"
GROWTH_BALANCE_RAIL_NAMESPACE = "customer_growth_balance_rails"
GROWTH_BALANCE_TRANSACTION_NAMESPACE = "customer_growth_balance_transactions"
GROWTH_BALANCE_LOCK_NAMESPACE = "customer_growth_balance_locks"
ADVERTISING_MERCHANT_CATEGORY = "advertising_services"
_PENDING_LIQUIDITY_HOLD = timedelta(minutes=31)
_LIQUIDITY_LOCK_TTL = timedelta(seconds=60)
_LIQUIDITY_LOCK_KEY = "stripe_issuing_liquidity"
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
    """Stripe Issuing-backed Partizan-funded acquisition rail.

    Customer funds stay represented by the Growth Balance ledger. Provider spend is
    paid from a Partizan-owned, pre-funded Stripe Issuing liquidity pool through one
    virtual card per customer project. Cards are restricted to advertising merchants
    and an all-time limit derived from funded Growth Balance acquisition capacity.

    Sensitive PAN/CVC data is never retrieved or stored here. Provider billing binding
    is an operator-controlled step and paid activation remains blocked until that
    binding is explicitly confirmed.
    """

    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settings_override = settings

    def readiness(self, project_id: UUID) -> tuple[bool, str]:
        configured, status = self._configuration_readiness()
        if not configured:
            return False, status
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None:
            return False, "STRIPE_ISSUING_CARD_NOT_PROVISIONED"
        if rail.get("binding_status") != "BOUND":
            return False, "META_BILLING_NOT_BOUND_TO_PARTIZAN_CARD"
        return True, "READY"

    def funding_readiness(
        self,
        project_id: UUID,
        *,
        required_liquidity_cents: int,
    ) -> tuple[bool, str]:
        del project_id
        configured, status = self._configuration_readiness()
        if not configured:
            return False, status
        settings = self._settings()
        try:
            cardholder = self._retrieve_cardholder(str(settings.stripe_issuing_cardholder_id))
            if str(cardholder.get("status") or "").lower() != "active":
                return False, "STRIPE_ISSUING_CARDHOLDER_INACTIVE"
            requirements = cardholder.get("requirements") or {}
            if requirements.get("disabled_reason") or requirements.get("past_due"):
                return False, "STRIPE_ISSUING_CARDHOLDER_REQUIREMENTS_DUE"
            issuing_available = self._retrieve_issuing_available_cents(
                settings.stripe_issuing_currency
            )
        except stripe.StripeError:
            return False, "STRIPE_ISSUING_UNAVAILABLE"
        if issuing_available < max(int(required_liquidity_cents), 0):
            return False, "STRIPE_ISSUING_LIQUIDITY_INSUFFICIENT"
        return True, "READY_FOR_FUNDING"

    def provision_or_update(self, project_id: UUID, acquisition_capacity_cents: int) -> dict:
        if acquisition_capacity_cents <= 0:
            raise ValueError("Growth Balance acquisition capacity must be positive")
        settings = self._require_configured()
        controls = self._spending_controls(acquisition_capacity_cents)
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        bound = bool(rail and rail.get("binding_status") == "BOUND")
        target_status = "active" if bound else "inactive"
        try:
            if rail and rail.get("card_id"):
                card = self._modify_card(
                    str(rail["card_id"]),
                    status=target_status,
                    spending_controls=controls,
                    metadata={
                        "partizan_project_id": str(project_id),
                        "partizan_purpose": "growth_balance_acquisition",
                    },
                    idempotency_key=(
                        f"partizan-issuing-limit-{project_id}-{acquisition_capacity_cents}-{target_status}"
                    ),
                )
            else:
                card = self._create_card(
                    cardholder=str(settings.stripe_issuing_cardholder_id),
                    currency=settings.stripe_issuing_currency,
                    type="virtual",
                    status="inactive",
                    spending_controls=controls,
                    metadata={
                        "partizan_project_id": str(project_id),
                        "partizan_purpose": "growth_balance_acquisition",
                    },
                    idempotency_key=f"partizan-issuing-card-{project_id}",
                )
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card provisioning failed") from exc

        payload = dict(rail or {})
        payload.update(
            {
                "project_id": str(project_id),
                "provider": "stripe_issuing",
                "card_id": str(card.get("id") or payload.get("card_id") or ""),
                "card_last4": str(card.get("last4") or payload.get("card_last4") or ""),
                "card_status": str(card.get("status") or target_status),
                "currency": settings.stripe_issuing_currency,
                "allowed_categories": [ADVERTISING_MERCHANT_CATEGORY],
                "acquisition_limit_cents": int(acquisition_capacity_cents),
                "binding_status": payload.get("binding_status") or "UNBOUND",
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        payload.setdefault("created_at", datetime.now(UTC).isoformat())
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), payload)
        return payload

    def confirm_meta_binding(self, project_id: UUID, ad_account_id: str) -> dict:
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None or not rail.get("card_id"):
            raise ValueError("Partizan-funded Stripe Issuing card is not provisioned")
        if int(rail.get("acquisition_limit_cents") or 0) <= 0:
            raise ValueError("Fund the Growth Balance before binding provider billing")
        self._require_configured()
        try:
            card = self._modify_card(
                str(rail["card_id"]),
                status="active",
                spending_controls=self._spending_controls(
                    int(rail["acquisition_limit_cents"])
                ),
                metadata={
                    "partizan_project_id": str(project_id),
                    "partizan_purpose": "growth_balance_acquisition",
                    "partizan_meta_ad_account_id": ad_account_id,
                },
                idempotency_key=f"partizan-issuing-bind-{project_id}-{ad_account_id}",
            )
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card activation failed") from exc
        rail["binding_status"] = "BOUND"
        rail["bound_provider"] = "meta"
        rail["bound_provider_account_id"] = ad_account_id
        rail["bound_at"] = datetime.now(UTC).isoformat()
        rail["card_status"] = str(card.get("status") or "active")
        rail["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)
        return rail

    def pause(self, project_id: UUID, reason: str) -> None:
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None or not rail.get("card_id"):
            return
        self._require_configured()
        try:
            card = self._modify_card(
                str(rail["card_id"]),
                status="inactive",
                idempotency_key=f"partizan-issuing-pause-{project_id}-{reason}",
            )
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card pause failed") from exc
        rail["card_status"] = str(card.get("status") or "inactive")
        rail["paused_reason"] = reason
        rail["paused_at"] = datetime.now(UTC).isoformat()
        rail["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)

    def activate(self, project_id: UUID) -> None:
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        if rail is None or rail.get("binding_status") != "BOUND" or not rail.get("card_id"):
            raise ValueError("Partizan-funded card must be bound to Meta before activation")
        self._require_configured()
        try:
            card = self._modify_card(
                str(rail["card_id"]),
                status="active",
                spending_controls=self._spending_controls(
                    int(rail.get("acquisition_limit_cents") or 0)
                ),
                idempotency_key=f"partizan-issuing-activate-{project_id}",
            )
        except stripe.StripeError as exc:
            raise RuntimeError("Stripe Issuing card activation failed") from exc
        rail["card_status"] = str(card.get("status") or "active")
        rail["paused_reason"] = None
        rail["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id), rail)

    def uses_ledger(self, project_id: UUID) -> bool:
        return self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id)) is not None

    def settled_spend_cents(self, project_id: UUID) -> int:
        total = sum(
            int(item.get("spend_delta_cents") or 0)
            for item in self._store.list_namespace(GROWTH_BALANCE_TRANSACTION_NAMESPACE)
            if str(item.get("project_id")) == str(project_id)
        )
        return max(total, 0)

    def record_transaction(self, transaction: dict) -> bool:
        transaction_id = str(transaction.get("id") or "").strip()
        card_id = self._object_id(transaction.get("card"))
        if not transaction_id or not card_id:
            return False
        rail = self._rail_for_card(card_id)
        if rail is None:
            return False
        currency = str(transaction.get("currency") or "").lower()
        if currency != str(rail.get("currency") or "").lower():
            self.pause(UUID(str(rail["project_id"])), "UNEXPECTED_TRANSACTION_CURRENCY")
            raise ValueError("Issuing transaction currency does not match Growth Balance rail")
        merchant_data = transaction.get("merchant_data") or {}
        category = str(merchant_data.get("category") or "")
        transaction_type = str(transaction.get("type") or "")
        amount_cents = int(transaction.get("amount") or 0)
        payload = {
            "transaction_id": transaction_id,
            "project_id": str(rail["project_id"]),
            "card_id": card_id,
            "amount_cents": amount_cents,
            "spend_delta_cents": -amount_cents,
            "currency": currency,
            "type": transaction_type,
            "merchant_category": category,
            "merchant_name": str(merchant_data.get("name") or ""),
            "authorization_id": self._object_id(transaction.get("authorization")),
            "updated_at": datetime.now(UTC).isoformat(),
        }
        self._store.put(GROWTH_BALANCE_TRANSACTION_NAMESPACE, transaction_id, payload)
        if transaction_type == "capture" and category != ADVERTISING_MERCHANT_CATEGORY:
            self.pause(UUID(str(rail["project_id"])), "UNEXPECTED_MERCHANT_CATEGORY")
        return True

    def authorize_request(self, authorization: dict) -> bool:
        card_id = self._object_id(authorization.get("card"))
        if not card_id:
            return False
        rail = self._rail_for_card(card_id)
        if rail is None:
            return False
        if rail.get("binding_status") != "BOUND" or rail.get("card_status") != "active":
            return False
        pending = authorization.get("pending_request") or {}
        amount_cents = int(pending.get("amount") or 0)
        currency = str(pending.get("currency") or authorization.get("currency") or "").lower()
        merchant_data = authorization.get("merchant_data") or {}
        if currency != str(rail.get("currency") or "").lower():
            return False
        if str(merchant_data.get("category") or "") != ADVERTISING_MERCHANT_CATEGORY:
            return False
        if amount_cents <= 0:
            return False
        remaining = max(
            int(rail.get("acquisition_limit_cents") or 0)
            - self.settled_spend_cents(UUID(str(rail["project_id"]))),
            0,
        )
        if amount_cents > remaining:
            return False
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(rail["project_id"]))
        if project is None or project.get("autopilot_subscription_status") != "ACTIVE":
            return False
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            return False
        try:
            from app.autonomy_schemas import GrowthMandateStatus
            from app.autonomy_service import growth_mandate_service

            mandate = growth_mandate_service.get(UUID(str(product_id_raw)))
        except (KeyError, ValueError):
            return False
        return mandate.status == GrowthMandateStatus.ACTIVE

    def rail_view(self, project_id: UUID) -> dict:
        rail = self._store.get(GROWTH_BALANCE_RAIL_NAMESPACE, str(project_id))
        ready, status = self.readiness(project_id)
        if rail is None:
            return {
                "project_id": str(project_id),
                "provider": self._settings().growth_balance_settlement_provider,
                "settlement_ready": ready,
                "settlement_status": status,
            }
        return {
            "project_id": str(project_id),
            "provider": rail.get("provider"),
            "settlement_ready": ready,
            "settlement_status": status,
            "card_id": rail.get("card_id"),
            "card_last4": rail.get("card_last4"),
            "card_status": rail.get("card_status"),
            "currency": rail.get("currency"),
            "binding_status": rail.get("binding_status"),
            "bound_provider": rail.get("bound_provider"),
            "bound_provider_account_id": rail.get("bound_provider_account_id"),
            "acquisition_limit_usd": GrowthBalanceService._cents_to_usd(
                int(rail.get("acquisition_limit_cents") or 0)
            ),
        }

    def _configuration_readiness(self) -> tuple[bool, str]:
        settings = self._settings()
        if settings.growth_balance_settlement_provider != "stripe_issuing":
            return False, "PARTIZAN_FUNDED_PAYMENT_RAIL_NOT_CONFIGURED"
        if settings.stripe_secret_key is None:
            return False, "STRIPE_NOT_CONFIGURED"
        if not settings.stripe_issuing_cardholder_id:
            return False, "STRIPE_ISSUING_CARDHOLDER_NOT_CONFIGURED"
        return True, "CONFIGURED"

    def _require_configured(self) -> Settings:
        ready, status = self._configuration_readiness()
        if not ready:
            raise ValueError(f"Partizan-funded payment rail is not ready ({status})")
        return self._settings()

    def _settings(self) -> Settings:
        return self._settings_override or get_settings()

    def _set_stripe_key(self) -> None:
        settings = self._require_configured()
        stripe.api_key = settings.stripe_secret_key.get_secret_value()

    def _retrieve_cardholder(self, cardholder_id: str):
        self._set_stripe_key()
        return stripe.issuing.Cardholder.retrieve(cardholder_id)

    def _retrieve_issuing_available_cents(self, currency: str) -> int:
        self._set_stripe_key()
        balance = stripe.Balance.retrieve()
        issuing = balance.get("issuing") or {}
        available = issuing.get("available") or []
        return sum(
            int(item.get("amount") or 0)
            for item in available
            if str(item.get("currency") or "").lower() == currency.lower()
        )

    def _create_card(self, **kwargs):
        self._set_stripe_key()
        return stripe.issuing.Card.create(**kwargs)

    def _modify_card(self, card_id: str, **kwargs):
        self._set_stripe_key()
        return stripe.issuing.Card.modify(card_id, **kwargs)

    def _rail_for_card(self, card_id: str) -> dict | None:
        return next(
            (
                item
                for item in self._store.list_namespace(GROWTH_BALANCE_RAIL_NAMESPACE)
                if str(item.get("card_id") or "") == card_id
            ),
            None,
        )

    @staticmethod
    def _spending_controls(amount_cents: int) -> dict:
        if amount_cents <= 0:
            raise ValueError("Stripe Issuing card limit must be positive")
        return {
            "allowed_categories": [ADVERTISING_MERCHANT_CATEGORY],
            "spending_limits": [{"amount": int(amount_cents), "interval": "all_time"}],
        }

    @staticmethod
    def _object_id(value: object) -> str:
        getter = getattr(value, "get", None)
        if callable(getter):
            return str(getter("id") or "")
        return str(value or "")


class GrowthBalanceService:
    def __init__(
        self,
        store: RuntimeStateStore | None = None,
        *,
        settlement_service: GrowthBalanceSettlementService | None = None,
    ) -> None:
        self._store = store or get_runtime_store()
        self._settlement = settlement_service or GrowthBalanceSettlementService(self._store)

    def prepare_checkout(
        self,
        project_id: UUID,
        customer_token: str,
        amount_usd: float,
    ) -> tuple[int, str | None, int]:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        if project.get("autopilot_subscription_status") != "ACTIVE":
            raise CustomerPaymentRequiredError("Activate the Autopilot subscription first")
        amount_cents = self._usd_to_cents(amount_usd)
        lock_token = self._acquire_liquidity_lock()
        try:
            if self._has_active_pending_checkout(project_id):
                raise ValueError("A Growth Balance checkout is already pending for this project")
            fee_pct = int(get_settings().partizan_managed_spend_fee_pct)
            existing_funded = self._funded_cents(project_id)
            incremental_capacity = max(
                self._max_acquisition_cents(existing_funded + amount_cents, fee_pct)
                - self._max_acquisition_cents(existing_funded, fee_pct),
                0,
            )
            required_liquidity = self._outstanding_acquisition_cents() + incremental_capacity
            funding_readiness = getattr(self._settlement, "funding_readiness", None)
            if funding_readiness is None:
                ready, status = self._settlement.readiness(project_id)
            else:
                ready, status = funding_readiness(
                    project_id,
                    required_liquidity_cents=required_liquidity,
                )
            if not ready:
                raise ValueError(
                    "Growth Balance funding is disabled until the Partizan-funded provider "
                    f"payment rail is ready ({status})"
                )
            generation = int(project.get("growth_balance_checkout_generation") or 0) + 1
            reservation_key = self._reservation_key(project_id, generation)
            reservation = {
                "reservation_key": reservation_key,
                "project_id": str(project_id),
                "checkout_generation": generation,
                "amount_cents": amount_cents,
                "currency": "usd",
                "state": "RESERVED",
                "created_at": datetime.now(UTC).isoformat(),
            }
            if not self._store.put_if_absent(
                GROWTH_BALANCE_TOPUP_NAMESPACE,
                reservation_key,
                reservation,
            ):
                raise ValueError("Growth Balance liquidity reservation already exists")
            project["growth_balance_checkout_generation"] = generation
            self._persist_project(project)
            return generation, project.get("stripe_customer_id"), amount_cents
        finally:
            self._release_liquidity_lock(lock_token)

    def mark_checkout_pending(
        self,
        project_id: UUID,
        customer_token: str,
        *,
        session_id: str,
        amount_cents: int,
        checkout_generation: int | None = None,
    ) -> None:
        project = customer_funnel_service.get_project_payload(project_id, customer_token)
        generation = (
            int(checkout_generation)
            if checkout_generation is not None
            else int(project.get("growth_balance_checkout_generation") or 0)
        )
        if generation <= 0:
            raise ValueError("Growth Balance Checkout generation is missing")
        reservation_key = self._reservation_key(project_id, generation)
        reservation = self._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, reservation_key)
        if reservation is None or reservation.get("state") != "RESERVED":
            raise ValueError("Growth Balance liquidity reservation is missing")
        if (
            int(reservation.get("amount_cents") or 0) != int(amount_cents)
            or str(reservation.get("project_id") or "") != str(project_id)
        ):
            raise ValueError("Growth Balance liquidity reservation does not match Checkout")
        payload = {
            "session_id": session_id,
            "project_id": str(project_id),
            "checkout_generation": generation,
            "amount_cents": int(amount_cents),
            "currency": "usd",
            "state": "PENDING",
            "created_at": str(reservation["created_at"]),
        }
        created = self._store.put_if_absent(
            GROWTH_BALANCE_TOPUP_NAMESPACE,
            session_id,
            payload,
        )
        if not created:
            existing = self._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id)
            if existing is None or (
                str(existing.get("project_id")) != str(project_id)
                or int(existing.get("amount_cents") or 0) != int(amount_cents)
                or int(existing.get("checkout_generation") or 0) != generation
            ):
                raise ValueError("Growth Balance Checkout Session is already bound differently")
        reservation["state"] = "CHECKOUT_CREATED"
        reservation["session_id"] = session_id
        reservation["updated_at"] = datetime.now(UTC).isoformat()
        self._store.put(GROWTH_BALANCE_TOPUP_NAMESPACE, reservation_key, reservation)

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
            record = self._recover_paid_checkout_record(
                project_id,
                session_id=session_id,
                amount_cents=amount_cents,
                currency=currency,
            )
        if record is None:
            return False
        if str(record.get("project_id")) != str(project_id):
            return False
        if int(record.get("amount_cents") or 0) != int(amount_cents):
            return False
        if str(record.get("currency") or "").lower() != currency.lower():
            return False
        if record.get("state") != "PAID":
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
        self._sync_project_rail(project_id)
        return True

    def _recover_paid_checkout_record(
        self,
        project_id: UUID,
        *,
        session_id: str,
        amount_cents: int,
        currency: str,
    ) -> dict | None:
        """Recover a Stripe-paid checkout when session persistence lost a race.

        The liquidity reservation is written before Stripe Checkout is created. If the
        Checkout creation succeeds but the following session-id write fails, Stripe's
        signed completion webhook can safely reconnect the paid session to that exact
        project/amount reservation instead of accepting money without customer credit.
        """

        candidates = [
            item
            for item in self._store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)
            if item.get("reservation_key")
            and item.get("state") in {"RESERVED", "CHECKOUT_CREATED"}
            and str(item.get("project_id") or "") == str(project_id)
            and int(item.get("amount_cents") or 0) == int(amount_cents)
            and str(item.get("currency") or "").lower() == currency.lower()
            and (not item.get("session_id") or str(item.get("session_id")) == session_id)
        ]
        if not candidates:
            return None
        reservation = max(
            candidates,
            key=lambda item: int(item.get("checkout_generation") or 0),
        )
        generation = int(reservation.get("checkout_generation") or 0)
        if generation <= 0:
            return None
        record = {
            "session_id": session_id,
            "project_id": str(project_id),
            "checkout_generation": generation,
            "amount_cents": int(amount_cents),
            "currency": currency.lower(),
            "state": "PENDING",
            "created_at": str(reservation.get("created_at") or datetime.now(UTC).isoformat()),
            "recovered_from_reservation": True,
        }
        created = self._store.put_if_absent(
            GROWTH_BALANCE_TOPUP_NAMESPACE,
            session_id,
            record,
        )
        if not created:
            return self._store.get(GROWTH_BALANCE_TOPUP_NAMESPACE, session_id)
        reservation["state"] = "CHECKOUT_CREATED"
        reservation["session_id"] = session_id
        reservation["recovered_at"] = datetime.now(UTC).isoformat()
        self._store.put(
            GROWTH_BALANCE_TOPUP_NAMESPACE,
            str(reservation["reservation_key"]),
            reservation,
        )
        return record

    def summary(self, project_id: UUID, acquisition_spend_usd: float) -> GrowthBalanceSummary:
        settings = get_settings()
        fee_pct = int(settings.partizan_managed_spend_fee_pct)
        funded_cents = self._funded_cents(project_id)
        uses_ledger = getattr(self._settlement, "uses_ledger", lambda _project_id: False)(
            project_id
        )
        if uses_ledger:
            spend_cents = max(
                int(self._settlement.settled_spend_cents(project_id)),
                0,
            )
        else:
            spend_cents = max(
                self._usd_to_cents(acquisition_spend_usd, allow_zero=True),
                0,
            )
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

    def confirm_meta_binding(self, project_id: UUID, ad_account_id: str) -> dict:
        project = self._store.get(CUSTOMER_PROJECT_NAMESPACE, str(project_id))
        if project is None:
            raise CustomerProjectNotFoundError(project_id)
        product_id_raw = project.get("product_id")
        if not product_id_raw:
            raise ValueError("Customer project does not have a researched Product")
        from app.paid_provider_connections import paid_provider_connection_service

        connection = paid_provider_connection_service.get_meta(UUID(str(product_id_raw)))
        if connection is None:
            raise ValueError("Connect Meta before binding Partizan-funded billing")
        if str(connection.ad_account_id) != ad_account_id:
            raise ValueError("Meta ad account does not match the customer connection")
        return self._settlement.confirm_meta_binding(project_id, ad_account_id)

    def pause_rail(self, project_id: UUID, reason: str) -> None:
        pause = getattr(self._settlement, "pause", None)
        if pause is not None:
            pause(project_id, reason)

    def activate_rail(self, project_id: UUID) -> None:
        activate = getattr(self._settlement, "activate", None)
        if activate is not None:
            activate(project_id)

    def authorize_request(self, authorization: dict) -> bool:
        authorize = getattr(self._settlement, "authorize_request", None)
        return bool(authorize and authorize(authorization))

    def record_issuing_transaction(self, transaction: dict) -> bool:
        record = getattr(self._settlement, "record_transaction", None)
        return bool(record and record(transaction))

    def rail_view(self, project_id: UUID) -> dict:
        view = getattr(self._settlement, "rail_view", None)
        if view is None:
            ready, status = self._settlement.readiness(project_id)
            return {
                "project_id": str(project_id),
                "settlement_ready": ready,
                "settlement_status": status,
            }
        return view(project_id)

    def reset(self) -> None:
        if self._store.ephemeral:
            self._store.clear_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)
            self._store.clear_namespace(GROWTH_BALANCE_RAIL_NAMESPACE)
            self._store.clear_namespace(GROWTH_BALANCE_TRANSACTION_NAMESPACE)
            self._store.clear_namespace(GROWTH_BALANCE_LOCK_NAMESPACE)

    def _sync_project_rail(self, project_id: UUID) -> None:
        provision = getattr(self._settlement, "provision_or_update", None)
        if provision is None:
            return
        fee_pct = int(get_settings().partizan_managed_spend_fee_pct)
        capacity_cents = self._max_acquisition_cents(self._funded_cents(project_id), fee_pct)
        if capacity_cents > 0:
            provision(project_id, capacity_cents)

    def _funded_cents(self, project_id: UUID) -> int:
        return sum(
            int(item.get("amount_cents") or 0)
            for item in self._store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)
            if str(item.get("project_id")) == str(project_id) and item.get("state") == "PAID"
        )

    def _outstanding_acquisition_cents(self) -> int:
        fee_pct = int(get_settings().partizan_managed_spend_fee_pct)
        funded_by_project: dict[str, int] = {}
        pending_capacity = 0
        now = datetime.now(UTC)
        for item in self._store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE):
            project_id = str(item.get("project_id") or "")
            amount = int(item.get("amount_cents") or 0)
            if not project_id or amount <= 0:
                continue
            state = item.get("state")
            if state == "PAID":
                funded_by_project[project_id] = funded_by_project.get(project_id, 0) + amount
            elif state in {"RESERVED", "PENDING"} and self._pending_is_active(item, now):
                pending_capacity += self._max_acquisition_cents(amount, fee_pct)
        outstanding = pending_capacity
        for project_id, funded_cents in funded_by_project.items():
            capacity = self._max_acquisition_cents(funded_cents, fee_pct)
            settled = 0
            uses_ledger = getattr(self._settlement, "uses_ledger", lambda _project_id: False)(
                UUID(project_id)
            )
            if uses_ledger:
                settled = int(self._settlement.settled_spend_cents(UUID(project_id)))
            outstanding += max(capacity - settled, 0)
        return outstanding

    def _has_active_pending_checkout(self, project_id: UUID) -> bool:
        now = datetime.now(UTC)
        return any(
            str(item.get("project_id")) == str(project_id)
            and item.get("state") in {"RESERVED", "PENDING"}
            and self._pending_is_active(item, now)
            for item in self._store.list_namespace(GROWTH_BALANCE_TOPUP_NAMESPACE)
        )

    def _acquire_liquidity_lock(self) -> str:
        now = datetime.now(UTC)
        token = uuid4().hex
        payload = {"token": token, "created_at": now.isoformat()}
        if self._store.put_if_absent(
            GROWTH_BALANCE_LOCK_NAMESPACE,
            _LIQUIDITY_LOCK_KEY,
            payload,
        ):
            return token
        existing = self._store.get(GROWTH_BALANCE_LOCK_NAMESPACE, _LIQUIDITY_LOCK_KEY)
        if existing is not None and self._lock_is_stale(existing, now):
            self._store.delete(GROWTH_BALANCE_LOCK_NAMESPACE, _LIQUIDITY_LOCK_KEY)
            if self._store.put_if_absent(
                GROWTH_BALANCE_LOCK_NAMESPACE,
                _LIQUIDITY_LOCK_KEY,
                payload,
            ):
                return token
        raise ValueError("Growth Balance liquidity allocation is busy; retry shortly")

    def _release_liquidity_lock(self, token: str) -> None:
        existing = self._store.get(GROWTH_BALANCE_LOCK_NAMESPACE, _LIQUIDITY_LOCK_KEY)
        if existing is not None and existing.get("token") == token:
            self._store.delete(GROWTH_BALANCE_LOCK_NAMESPACE, _LIQUIDITY_LOCK_KEY)

    @staticmethod
    def _lock_is_stale(item: dict, now: datetime) -> bool:
        created_raw = item.get("created_at")
        if not created_raw:
            return True
        try:
            created = datetime.fromisoformat(str(created_raw))
        except ValueError:
            return True
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return now - created > _LIQUIDITY_LOCK_TTL

    @staticmethod
    def _reservation_key(project_id: UUID, checkout_generation: int) -> str:
        return f"reservation:{project_id}:{checkout_generation}"

    @staticmethod
    def _pending_is_active(item: dict, now: datetime) -> bool:
        created_raw = item.get("created_at")
        if not created_raw:
            return False
        try:
            created = datetime.fromisoformat(str(created_raw))
        except ValueError:
            return False
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        return now - created <= _PENDING_LIQUIDITY_HOLD

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
