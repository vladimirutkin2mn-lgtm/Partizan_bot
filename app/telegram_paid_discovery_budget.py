"""Fail-closed operator-estimated Telegram web budget with atomic monthly slots.

These reservations are NOT a claim about the provider's actual dollar charge.
OpenAI usage may report internal web calls and tokens but no final billed USD.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_FLOOR

from app.openai_usage import capture_openai_usage_rows, summarize_openai_usage
from app.runtime_store import RuntimeStateStore
from app.search import SearchProvider

TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE = "telegram_paid_discovery_daily_claim"
TELEGRAM_PAID_DISCOVERY_MONTHLY_SLOT_NAMESPACE = "telegram_paid_discovery_month_slot"
TELEGRAM_PAID_DISCOVERY_DENIAL_NAMESPACE = "telegram_paid_discovery_denial"


def _positive_amount(value: object) -> Decimal | None:
    if value is None:
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return amount if amount.is_finite() and amount > 0 else None


class _PaidTelegramDiscoveryBudgetProvider:
    """Reserve 1 daily query and 1 monthly price-envelope slot before sending.

    The single daily claim is preserved across errors. Monthly claims use
    pre-sized slots to make the maximum count safe under concurrent workers,
    without a racy read-modify-write balance.
    """

    def __init__(
        self,
        delegate: SearchProvider,
        store: RuntimeStateStore,
        product_id: object,
        *,
        monthly_budget_usd: object = None,
        query_reservation_usd: object = None,
        now=None,
    ):
        self._delegate = delegate
        self._store = store
        self._product_id = str(product_id)
        self._monthly_budget = _positive_amount(monthly_budget_usd)
        self._query_reservation = _positive_amount(query_reservation_usd)
        self._now = now or (lambda: datetime.now(UTC))
        self.last_paid_status = "NOT_ATTEMPTED"

    def _deny(self, reason: str, *, at: datetime) -> None:
        self.last_paid_status = reason
        key = f"{self._product_id}:{at.date().isoformat()}"
        self._store.put(
            TELEGRAM_PAID_DISCOVERY_DENIAL_NAMESPACE,
            key,
            {
                "product_id": self._product_id,
                "attempted_at": at.isoformat(),
                "status": reason,
                "actual_billed_cost_usd": None,
            },
        )

    async def search(self, discovery_query, limit: int = 5):
        query = str(getattr(discovery_query, "query", "") or "")
        if "site:t.me" not in query.casefold():
            return await self._delegate.search(discovery_query, limit=limit)

        at = self._now()
        at = at.astimezone(UTC) if at.tzinfo else at.replace(tzinfo=UTC)
        if self._monthly_budget is None or self._query_reservation is None:
            self._deny("COST_ENVELOPE_NOT_CONFIGURED", at=at)
            return []
        if self._query_reservation > self._monthly_budget:
            self._deny("MONTHLY_BUDGET_INSUFFICIENT", at=at)
            return []

        # At most one slot per UTC day and at most 31 per month. The legacy
        # per-day claim key stays stable across deployments and adaptive rounds.
        day_key = f"{self._product_id}:{at.date().isoformat()}"
        day_claim = {
            "product_id": self._product_id,
            "claimed_at": at.isoformat(),
            "query": query[:500],
            "maximum_paid_queries": 1,
            "estimated_cost_reservation_usd": str(self._query_reservation),
            "monthly_budget_usd": str(self._monthly_budget),
            "actual_billed_cost_usd": None,
            "status": "RESERVING",
        }
        if not self._store.put_if_absent(
            TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE, day_key, day_claim
        ):
            self.last_paid_status = "DAILY_QUERY_LIMIT"
            return []

        permitted_slots = min(
            31,
            int((self._monthly_budget / self._query_reservation).to_integral_value(
                rounding=ROUND_FLOOR
            )),
        )
        month_prefix = f"{self._product_id}:{at.year:04d}-{at.month:02d}"
        month_key = None
        for slot in range(permitted_slots):
            key = f"{month_prefix}:{slot:02d}"
            if self._store.put_if_absent(
                TELEGRAM_PAID_DISCOVERY_MONTHLY_SLOT_NAMESPACE,
                key,
                {
                    "id": key,
                    "product_id": self._product_id,
                    "claimed_at": at.isoformat(),
                    "day_key": day_key,
                    "reserved_usd": str(self._query_reservation),
                    "actual_billed_cost_usd": None,
                    "pricing_status": "OPERATOR_ESTIMATE_ONLY",
                    "status": "RESERVED",
                },
            ):
                month_key = key
                break
        if month_key is None:
            day_claim["status"] = "MONTHLY_BUDGET_EXHAUSTED"
            self._store.put(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE, day_key, day_claim)
            self._deny("MONTHLY_BUDGET_EXHAUSTED", at=at)
            return []

        day_claim["monthly_slot_id"] = month_key
        day_claim["status"] = "REQUEST_STARTED"
        self._store.put(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE, day_key, day_claim)
        usage_rows: list[dict] = []
        try:
            with capture_openai_usage_rows(
                usage_rows, task="telegram_paid_discovery", product_id=self._product_id
            ):
                results = await self._delegate.search(discovery_query, limit=limit)
        except Exception as exc:
            day_claim["status"] = "PROVIDER_FAILED_CHARGE_UNKNOWN"
            day_claim["error_type"] = type(exc).__name__
            self._store.put(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE, day_key, day_claim)
            self.last_paid_status = day_claim["status"]
            raise

        usage = summarize_openai_usage(usage_rows)
        day_claim.update(
            {
                "status": "COMPLETED_BILLING_UNKNOWN",
                "usage_status": "OBSERVED" if usage_rows else "NOT_REPORTED",
                "usage": usage,
                "actual_billed_cost_usd": None,
            }
        )
        self._store.put(TELEGRAM_PAID_DISCOVERY_CLAIM_NAMESPACE, day_key, day_claim)
        self.last_paid_status = day_claim["status"]
        return results
