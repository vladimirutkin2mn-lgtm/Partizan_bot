from pathlib import Path

import pytest
from pydantic import ValidationError

from app.customer_schemas import (
    CustomerAutopilotConfigureRequest,
    CustomerGrowthBalanceTopUpRequest,
    CustomerPreviewRequest,
)


def _payload(budget_usd: int) -> CustomerPreviewRequest:
    return CustomerPreviewRequest(
        brief="A customer acquisition product with enough detail for preview validation.",
        website_url="https://example.com",
        market="United States",
        goal="Validate demand",
        budget_usd=budget_usd,
    )


def test_preview_budget_accepts_small_non_round_amounts() -> None:
    assert _payload(1).budget_usd == 1
    assert _payload(37).budget_usd == 37
    assert _payload(99).budget_usd == 99


def test_preview_budget_still_requires_positive_amount() -> None:
    with pytest.raises(ValidationError):
        _payload(0)


def test_start_form_keeps_preview_budget_flexible_and_removes_delegated_budget() -> None:
    html = Path("app/web/start.v2.html").read_text(encoding="utf-8")
    assert 'id="budget" type="number" min="1" max="100000" step="1"' in html
    assert 'id="growth-balance-amount" type="number" min="1" max="100000" step="1"' in html
    assert 'id="autopilot-budget"' not in html


def test_growth_balance_topup_accepts_small_all_in_amounts_without_restoring_budget_field() -> None:
    assert CustomerGrowthBalanceTopUpRequest(amount_usd=1).amount_usd == 1
    with pytest.raises(ValidationError):
        CustomerGrowthBalanceTopUpRequest(amount_usd=0)

    request = CustomerAutopilotConfigureRequest(
        target_max_cac=20,
        confirm_autonomous_spend=True,
    )
    assert not hasattr(request, "marketing_budget_usd")
