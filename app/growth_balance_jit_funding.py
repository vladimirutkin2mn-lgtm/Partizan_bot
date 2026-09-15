from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP

_CENT = Decimal("0.01")


@dataclass(frozen=True)
class NextMoveFundingPlan:
    required_acquisition_usd: float
    remaining_acquisition_capacity_usd: float
    topup_amount_usd: float
    management_fee_pct: int

    @property
    def funding_required(self) -> bool:
        return self.topup_amount_usd > 0


class GrowthBalanceJitFundingService:
    """Calculate the smallest Growth Balance top-up for one executable paid move."""

    def plan(
        self,
        *,
        required_acquisition_usd: float,
        project_budget_usd: float,
        funded_usd: float,
        acquisition_spend_usd: float,
        remaining_acquisition_capacity_usd: float,
        management_fee_pct: int,
    ) -> NextMoveFundingPlan:
        required_cents = self._usd_to_cents(
            required_acquisition_usd,
            field="required_acquisition_usd",
        )
        project_budget_cents = self._usd_to_cents(
            project_budget_usd,
            field="project_budget_usd",
        )
        if required_cents > project_budget_cents:
            raise ValueError("Paid move exceeds the customer test budget")
        if management_fee_pct < 0 or management_fee_pct > 100:
            raise ValueError("Growth Balance management fee is invalid")

        funded_cents = self._usd_to_cents(
            funded_usd,
            field="funded_usd",
            allow_zero=True,
        )
        spent_cents = self._usd_to_cents(
            acquisition_spend_usd,
            field="acquisition_spend_usd",
            allow_zero=True,
        )
        target_spend_cents = spent_cents + required_cents
        required_funded_cents = target_spend_cents + self._fee_cents(
            target_spend_cents,
            management_fee_pct,
        )
        topup_cents = max(required_funded_cents - funded_cents, 0)

        return NextMoveFundingPlan(
            required_acquisition_usd=self._cents_to_usd(required_cents),
            remaining_acquisition_capacity_usd=max(
                self._money(remaining_acquisition_capacity_usd),
                0.0,
            ),
            topup_amount_usd=self._cents_to_usd(topup_cents),
            management_fee_pct=int(management_fee_pct),
        )

    @staticmethod
    def _fee_cents(spend_cents: int, fee_pct: int) -> int:
        return int(
            (Decimal(spend_cents) * Decimal(fee_pct) / Decimal(100)).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

    @classmethod
    def _usd_to_cents(
        cls,
        value: object,
        *,
        field: str,
        allow_zero: bool = True,
    ) -> int:
        try:
            amount = Decimal(str(value)).quantize(_CENT, rounding=ROUND_HALF_UP)
        except Exception as exc:
            raise ValueError(f"{field} must be a valid USD amount") from exc
        if amount < 0 or (amount == 0 and not allow_zero):
            raise ValueError(f"{field} must be positive")
        return int(amount * 100)

    @classmethod
    def _money(cls, value: object) -> float:
        return cls._cents_to_usd(
            cls._usd_to_cents(value, field="amount", allow_zero=True)
        )

    @staticmethod
    def _cents_to_usd(value: int) -> float:
        return float((Decimal(value) / Decimal(100)).quantize(_CENT))


growth_balance_jit_funding_service = GrowthBalanceJitFundingService()
